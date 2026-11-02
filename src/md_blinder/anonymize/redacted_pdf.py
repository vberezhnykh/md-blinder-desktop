"""Обезличенный PDF: страницы картинками, где найденные данные закрашены
плашками с плейсхолдерами, и невидимый текстовый слой с тем же текстом.

Такой PDF нужен, чтобы проверить обезличенный Markdown по изображению: модель
с распознаванием изображений видит таблицы и разметку, а данные на картинке
закрыты так же, как в тексте.

Каждая страница рисуется заново в новый документ, поэтому исходные пиксели,
текст, метаданные, ссылки и вложения в файл не попадают. Плашка закрывает слово
целиком, с запасом до середины промежутка до соседнего слова.

Где лежат слова:
- страницы с текстовым слоем — координаты слов MuPDF;
- сканы — строки Indra OCR. Indra отдает рамки строк, а слова внутри строки
  находятся по просветам между чернилами (_split_words). Страница выводится так,
  как ее читала Indra: повернутой и выпрямленной, в этом кадре строки
  горизонтальны и рамки точные.

Что закрывается:
- вхождения значений всех замен документа (как в Markdown), без учета регистра и
  латинских двойников кириллицы, с переносами по строкам;
- строки, слов которых нет в Markdown: колонтитулы, которые убрала конвертация,
  страницы, не распознанные при обработке, текст, удаленный в редакторе;
- картинки: на сканах — области Picture Indra, на страницах с текстовым слоем —
  вставленные изображения меньше половины страницы (подписи, печати, логотипы).
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np
import pymupdf
import regex

from ..cancel import CancelToken, check
from .types import Entity

DPI = 200  # как у OCR: рамки строк Indra в пикселях этого разрешения
SCALE = DPI / 72

PLATE_FILL = (0.88, 0.88, 0.88)
PLATE_BORDER = (0.45, 0.45, 0.45)
LABEL_COLOR = (0.1, 0.1, 0.1)
HIDDEN_FILL = (0.94, 0.94, 0.94)
PICTURE_LABEL = "[Изображение удалено]"

# Доля строк страниц, которых нет в Markdown, после которой PDF считается чужим.
FOREIGN_SHARE = 0.6
# Строка показывается, если в Markdown есть хотя бы такая доля ее слов.
LINE_KNOWN_SHARE = 0.5
# Вставленная картинка меньше этой доли страницы закрывается целиком.
SMALL_IMAGE_SHARE = 0.5

Box = tuple[float, float, float, float]


class RedactedPdfError(Exception):
    """Ошибка с готовым текстом для пользователя."""


@dataclass
class Word:
    text: str
    box: Box  # область плашки в пикселях кадра страницы, с запасом
    core: Optional[Box] = None  # сами буквы слова: от верха прописных до низа выносных


@dataclass
class PageLine:
    words: list[Word]
    box: Box

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class PageFrame:
    """Строки страницы в пикселях ее кадра. Само изображение не хранится: на длинном
    документе это гигабайты, оно рисуется заново при сборке (frame_image)."""

    lines: list[PageLine]
    pictures: list[Box] = field(default_factory=list)
    turn: Optional[tuple[int, float, tuple[int, int]]] = None  # скан: поворот Indra, наклон, размер кадра


@dataclass
class Redaction:
    line: int
    first: int  # индексы слов в строке, включительно
    last: int
    label: str  # плейсхолдер: на картинке подписана каждая часть значения
    prefix: str = ""  # часть первого слова до значения, остается в текстовом слое
    suffix: str = ""
    continued: bool = False  # продолжение значения: в текстовом слое метка уже есть


@dataclass
class RedactedPdfReport:
    pages: int
    redactions: int
    unmatched: list[tuple[str, str]]  # (плейсхолдер, значение), не найденные на страницах
    hidden_lines: int  # строки, которых нет в Markdown


# ---------------------------------------------------------------- сравнение текста

_FOLD = {
    # Латинские и кириллические двойники, ноль и «о», кавычки, тире, пробелы.
    **dict(zip("авекмнорстухіјѕё", "abekmhopctyxijse")),
    "0": "o",
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "`": "'",
    "–": "-", "—": "-", "‑": "-", "‐": "-", "−": "-",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\t": " ",
}
_FOLD_TABLE = str.maketrans(_FOLD)


def fold(text: str) -> str:
    """Посимвольная нормализация для поиска: длина строки не меняется."""
    lowered = "".join(ch if len(ch.lower()) != 1 else ch.lower() for ch in text)
    return lowered.translate(_FOLD_TABLE)


_MD_ESCAPE_RE = regex.compile(r"\\([!-/:-@\[-`{-~])")
_MD_EMPHASIS_RE = regex.compile(r"(?<!\\)(?:\*\*|__|\*)")
_TOKEN_RE = regex.compile(r"[\p{L}\p{N}]+")


def normalize_value(value: str) -> str:
    """Значение замены в виде для сравнения со строками страницы: без экранирования
    и выделения Markdown (обратная косая черта перед знаком, «**»)."""
    value = _MD_EMPHASIS_RE.sub("", value)
    return " ".join(fold(_MD_ESCAPE_RE.sub(r"\1", value)).split())


def _boundary(ch: str, neighbor: str) -> bool:
    """Значение не продолжает соседнее слово той же природы: «Ann» в «Annual»,
    «123» в «41234». ИНН, слитый с меткой («ИНН7701…»), при этом находится."""
    if not neighbor:
        return True
    if ch.isalpha():
        return not neighbor.isalpha()
    if ch.isdigit():
        return not neighbor.isdigit()
    return True


def replacement_values(source_text: str, entities: Sequence[Entity]) -> list[tuple[str, str]]:
    """(значение, плейсхолдер) всех вхождений, которые заменяются в Markdown."""
    seen: dict[str, str] = {}
    for e in entities:
        if not e.anonymize:
            continue
        for p in e.positions:
            value = source_text[p.start:p.end].strip()
            if value and value not in seen:
                seen[value] = e.placeholder
    # Длинные значения первыми: «Ivan Petrenko» закрывается одной плашкой.
    return sorted(seen.items(), key=lambda item: -len(item[0]))


def _known_tokens(source_text: str) -> set[str]:
    return set(_TOKEN_RE.findall(fold(source_text)))


def _line_known(line: PageLine, known: set[str]) -> bool:
    tokens = _TOKEN_RE.findall(fold(line.text))
    tokens = [t for t in tokens if len(t) > 1 or t.isdigit()]
    if not tokens:
        return True
    return sum(t in known for t in tokens) >= LINE_KNOWN_SHARE * len(tokens)


def _next_lines(pages: Sequence[Sequence[PageLine]]) -> list[list[int]]:
    """Строки, которыми может продолжаться значение после конца строки: следующая по
    порядку чтения (в том числе первая строка следующей страницы), строки ниже в той
    же колонке и строки правее в том же ряду. Порядок чтения на сканах с таблицами и
    двумя колонками не совпадает с порядком в Markdown, поэтому смотрятся все соседи."""
    result: list[list[int]] = []
    offset = 0
    total = sum(len(lines) for lines in pages)
    for lines in pages:
        for index, line in enumerate(lines):
            x0, y0, x1, y1 = line.box
            h = max(1.0, y1 - y0)
            nexts = [offset + index + 1] if offset + index + 1 < total else []
            for other, cand in enumerate(lines):
                if other in (index, index + 1):
                    continue
                a0, b0, a1, b1 = cand.box
                overlap_y = min(y1, b1) - max(y0, b0)
                below = b0 >= y0 + 0.4 * h and b0 - y1 < 2.5 * h and min(x1, a1) - max(x0, a0) > 0
                right = overlap_y > 0.5 * min(h, b1 - b0) and a0 >= x1 - 0.5 * h and a0 - x1 < 4 * h
                if below or right:
                    nexts.append(offset + other)
            result.append(nexts)
        offset += len(lines)
    return result


def _chain(texts: Sequence[str], nexts: Sequence[list[int]], value: str, line: int, start: int,
           ) -> Optional[list[tuple[int, int, int]]]:
    """Значение, начатое в строке line с символа start, по цепочке строк.

    Возвращает куски (строка, начало, конец) или None. Пробел значения совпадает с
    концом строки; слово может переходить на следующую строку с дефисом или без."""
    stack: list[tuple[int, int, int, list[tuple[int, int, int]], int]] = [(line, start, 0, [], 0)]
    while stack:
        i, s, k, pieces, depth = stack.pop()
        text = texts[i]
        begin = s
        while k < len(value) and s < len(text) and text[s] == value[k]:
            s += 1
            k += 1
        if k == len(value):
            if _boundary(value[-1], text[s] if s < len(text) else ""):
                return pieces + [(i, begin, s)]
            continue
        if depth >= 12:
            continue
        if s == len(text) - 1 and text[s] == "-" and value[k] != " ":
            s += 1  # перенос с дефисом: «Petren-» + «ko»
        if s < len(text):
            continue
        k_next = k + 1 if value[k] == " " else k
        for j in nexts[i]:
            stack.append((j, 0, k_next, pieces + [(i, begin, s)], depth + 1))
    return None


def find_redactions(
    pages: Sequence[Sequence[PageLine]], values: Sequence[tuple[str, str]],
) -> tuple[list[list[Redaction]], list[tuple[str, str]]]:
    """Плашки по страницам и значения, которых нет ни на одной странице.

    Слово, задетое значением, закрывается целиком. Значение ищется и через перенос
    строки, колонки таблицы и страницы (_next_lines)."""
    flat = [(p, i, line) for p, lines in enumerate(pages) for i, line in enumerate(lines)]
    texts = [fold(line.text) for _, _, line in flat]
    # Символ строки -> (слово, символ в слове).
    owners: list[list[Optional[tuple[int, int]]]] = []
    for _, _, line in flat:
        row: list[Optional[tuple[int, int]]] = []
        for w, word in enumerate(line.words):
            if w:
                row.append(None)
            row.extend((w, c) for c in range(len(word.text)))
        owners.append(row)
    nexts = _next_lines(pages)

    claimed: set[tuple[int, int]] = set()  # (строка в общем списке, слово)
    result: list[list[Redaction]] = [[] for _ in pages]
    unmatched: list[tuple[str, str]] = []
    for raw, placeholder in values:
        value = normalize_value(raw)
        if not _TOKEN_RE.search(value):
            continue
        head = value.split(" ")[0]
        found = False
        for index, text in enumerate(texts):
            starts: list[int] = []
            s = text.find(head)
            while s != -1:
                starts.append(s)
                s = text.find(head, s + 1)
            # Первое слово значения переносится на следующую строку: «Petren-» + «ko».
            for s in range(max(0, len(text) - len(head) + 1), len(text)):
                rest = text[s:].rstrip("-")
                if rest and head.startswith(rest) and s not in starts:
                    starts.append(s)
            for s in starts:
                if not _boundary(value[0], text[s - 1] if s else ""):
                    continue
                pieces = _chain(texts, nexts, value, index, s)
                if pieces:
                    found = True
                    _claim(pieces, owners, flat, claimed, placeholder, result)
        if not found:
            unmatched.append((placeholder, raw))
    return result, unmatched


def _claim(pieces, owners, flat, claimed, placeholder, result) -> None:
    labeled = False
    for number, (index, start, end) in enumerate(pieces):
        hits = [owners[index][c] for c in range(start, min(end, len(owners[index]))) if owners[index][c]]
        if not hits:
            continue
        spans: dict[int, list[int]] = {}
        for w, c in hits:
            spans.setdefault(w, [c, c])[1] = c
        free = [w for w in sorted(spans) if (index, w) not in claimed]
        if not free:
            labeled = True  # значение уже под плашкой более длинного
            continue
        page, line_index, line = flat[index]
        runs: list[list[int]] = [[free[0]]]
        for w in free[1:]:
            if w == runs[-1][-1] + 1:
                runs[-1].append(w)
            else:
                runs.append([w])
        for run in runs:
            first, last = run[0], run[-1]
            # Часть слова до и после значения остается в текстовом слое, как в Markdown.
            prefix = line.words[first].text[: spans[first][0]] if number == 0 else ""
            suffix = line.words[last].text[spans[last][1] + 1:] if number == len(pieces) - 1 else ""
            result[page].append(Redaction(line_index, first, last, placeholder, prefix, suffix, continued=labeled))
            labeled = True
            claimed.update((index, w) for w in run)


# ---------------------------------------------------------------- слова на скане

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Участки подряд идущих True: (начало, конец не включая)."""
    padded = np.concatenate(([False], mask, [False]))
    diff = np.diff(padded.astype(np.int8))
    return list(zip(np.flatnonzero(diff == 1).tolist(), np.flatnonzero(diff == -1).tolist()))


def _ink(crop: np.ndarray) -> np.ndarray:
    """Маска чернил: порог Оцу по строке, не светлее фона страницы."""
    import cv2

    if crop.size == 0 or int(crop.max()) - int(crop.min()) < 40:
        return np.zeros(crop.shape, bool)
    threshold, _ = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return crop < min(threshold, 200)


_NARROW = set("ijlrtfI1.,:;'!|()[]/-\"")
_WIDE = set("MWmw@%")


def _char_width(ch: str) -> float:
    """Примерная ширина символа в долях средней: для оценки, где кончается слово."""
    if ch == " ":
        return 0.9
    if ch in _NARROW:
        return 0.5
    if ch in _WIDE:
        return 1.5
    if ch.isupper():
        return 1.3
    return 1.0


def _word_gaps_by_width(gaps: Sequence[tuple[float, float]], n: int, h: float) -> Optional[list[Optional[int]]]:
    """Пробелы между словами шире просветов между буквами. Если ширины просветов
    делятся на две группы и широких ровно столько, сколько пробелов в тексте, это они.
    Так надежнее всего на тексте с выравниванием по ширине, где пробелы разные."""
    if n == 0 or len(gaps) < n:
        return None
    widths = sorted(b - a for a, b in gaps)
    for i in range(len(widths) - 1):
        low, high = widths[i], widths[i + 1]
        if high >= 1.6 * low and high >= 0.2 * h:
            wide = [j for j, (a, b) in enumerate(gaps) if b - a >= high]
            if len(wide) == n:
                return list(wide)
    return None


def _word_gaps_by_position(gaps: Sequence[tuple[float, float]], expected: Sequence[float], h: float,
                           span: float) -> list[Optional[int]]:
    """Каждая граница притягивается к просвету рядом с оценкой (динамическое
    программирование, порядок сохраняется); широкие просветы предпочтительнее."""
    n, m = len(expected), len(gaps)
    virtual_cost = 1.2
    wide = [g[1] - g[0] >= 0.3 * h for g in gaps]

    def gap_cost(k: int, j: int) -> float:
        a, b = gaps[j]
        return abs((a + b) / 2 - expected[k]) / (0.6 * h + 0.03 * span) - min(1.0, (b - a) / (0.4 * h))

    inf = float("inf")
    # best[k][j + 1]: граница k назначена, последний занятый просвет j (-1 — ни одного).
    best = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[Optional[tuple[int, Optional[int]]]]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for k in range(n):
        for j in range(-1, m):
            cost = best[k][j + 1]
            if cost == inf:
                continue
            # Граница без просвета.
            if cost + virtual_cost < best[k + 1][j + 1]:
                best[k + 1][j + 1] = cost + virtual_cost
                back[k + 1][j + 1] = (j, None)
            skipped = 0.0
            for nj in range(j + 1, m):
                total = cost + skipped + gap_cost(k, nj)
                if total < best[k + 1][nj + 1]:
                    best[k + 1][nj + 1] = total
                    back[k + 1][nj + 1] = (j, nj)
                skipped += 0.6 if wide[nj] else 0.0
    final = [best[n][j + 1] + sum(0.6 for g in range(j + 1, m) if wide[g]) for j in range(-1, m)]
    j = int(np.argmin(final)) - 1
    chosen: list[Optional[int]] = [None] * n
    for k in range(n, 0, -1):
        prev, gap = back[k][j + 1]
        chosen[k - 1] = gap
        j = prev
    return chosen


def _split_words(gray: np.ndarray, box: Sequence[float], text: str) -> list[Word]:
    """Слова строки скана по просветам между чернилами.

    Границы слов сначала оцениваются по доле символов строки, затем каждая
    притягивается к ближайшему просвету подходящей ширины (динамическое
    программирование, порядок слов сохраняется). Если просвета рядом нет, граница
    остается оценкой, а соседние плашки перекрываются с запасом: лучше закрыть
    лишнюю букву соседа, чем оставить край значения."""
    words = text.split()
    height, width = gray.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, x1 = max(0, x0), min(width, max(x0 + 1, x1))
    y0, y1 = max(0, y0), min(height, max(y0 + 1, y1))
    h = max(1.0, float(y1 - y0))
    if not words:
        return []
    ink = _ink(gray[y0:y1, x0:x1])
    # Высота строки по чернилам: рамка Indra бывает выше букв.
    rows = np.flatnonzero(ink.sum(axis=1) >= max(1, round(0.004 * ink.shape[1]))) if ink.size else []
    core_top, core_bottom = (float(y0 + rows[0]), float(y0 + rows[-1] + 1)) if len(rows) else (float(y0), float(y1))
    pad_y = 0.15 * (core_bottom - core_top)
    top, bottom = max(0.0, core_top - pad_y), min(float(height), core_bottom + pad_y)
    columns = ink.sum(axis=0) >= max(1, round(0.04 * h)) if ink.size else np.zeros(0, bool)
    if not columns.any():
        left, right = float(x0), float(x1)
        gaps: list[tuple[float, float]] = []
    else:
        inked = np.flatnonzero(columns)
        left, right = float(x0 + inked[0]), float(x0 + inked[-1] + 1)
        gaps = [(float(x0 + a), float(x0 + b)) for a, b in _runs(~columns)
                if a > inked[0] and b <= inked[-1] and b - a >= 0.12 * h]
    edge = 0.2 * h
    if len(words) == 1:
        return [Word(words[0], (max(0.0, left - edge), top, min(float(width), right + edge), bottom),
                     (left, core_top, right, core_bottom))]

    # Ожидаемые границы: середины пробелов, с учетом ширины букв.
    joined = " ".join(words)
    weights = [_char_width(ch) for ch in joined]
    total = sum(weights)
    span = max(1.0, right - left)
    expected: list[float] = []
    run = 0.0
    for ch, wgt in zip(joined, weights):
        if ch == " ":
            expected.append(left + span * (run + wgt / 2) / total)
        run += wgt
    n = len(expected)
    chosen = _word_gaps_by_width(gaps, n, h)
    if chosen is None:
        chosen = _word_gaps_by_position(gaps, expected, h, span)

    # Края слов: середина просвета или оценка с перекрытием.
    cuts: list[tuple[float, float]] = []  # (правый край слова k, левый край слова k + 1)
    for k, gap in enumerate(chosen):
        if gap is None:
            cuts.append((expected[k] + 0.3 * h, expected[k] - 0.3 * h))
        else:
            middle = (gaps[gap][0] + gaps[gap][1]) / 2
            cuts.append((middle, middle))
    result: list[Word] = []
    for k, word in enumerate(words):
        a = left - edge if k == 0 else cuts[k - 1][1]
        b = right + edge if k == n else cuts[k][0]
        a, b = max(0.0, a), min(float(width), b)
        result.append(Word(word, (a, top, max(a + 1, b), bottom), (a, core_top, max(a + 1, b), core_bottom)))
    return result


# ---------------------------------------------------------------- кадры страниц

_engine_lock = threading.Lock()
_engine: Optional[tuple[Path, object]] = None


def _frame_engine(model_dir: Path):
    """Indra, которая запоминает кадр страницы: изображение после поворота и
    выпрямления и рамки строк в нем, до перевода рамок обратно на страницу."""
    global _engine
    with _engine_lock:
        if _engine is None or _engine[0] != model_dir:
            from indra_ocr import OrtOCR

            from ..convert.ocr import _threads

            class FrameOCR(OrtOCR):
                def _turn(self, gray, k, a, small=None):
                    out = super()._turn(gray, k, a, small)
                    self.frame = out
                    return out

                def assemble(self, *args, **kwargs):
                    page = super().assemble(*args, **kwargs)
                    self.frame_lines = [(list(line.box), line.text) for line in page.lines]
                    self.frame_pictures = [list(b["bbox"]) for b in page.blocks if b["label"] == "Picture"]
                    return page

            _engine = (model_dir, FrameOCR(model_dir, threads=_threads(), furniture=True))
        return _engine[1]


def _render_rgb(page: pymupdf.Page) -> np.ndarray:
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB, alpha=False)
    data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
    # Копия: буфер MuPDF только для чтения, а плашки пишутся прямо в массив.
    return np.array(data[:, : pix.width * 3].reshape(pix.height, pix.width, 3))


def _turn_rgb(rgb: np.ndarray, gray_shape: tuple[int, ...], k: int, angle: float) -> np.ndarray:
    """Цветная страница в кадре Indra: тот же поворот и выпрямление, что у серой."""
    import cv2
    from indra_ocr.pipeline import VERTICAL

    if k == VERTICAL:
        rgb = np.rot90(rgb, 1)
    elif k:
        rgb = np.rot90(rgb, -k)
    rgb = np.ascontiguousarray(rgb)
    if angle:
        h, w = rgb.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rgb = cv2.warpAffine(rgb, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    if rgb.shape[:2] != tuple(gray_shape[:2]):  # не должно случаться; размер кадра — как у серой
        rgb = cv2.resize(rgb, (gray_shape[1], gray_shape[0]))
    return rgb


def ocr_frame(page: pymupdf.Page, engine) -> PageFrame:
    from ..convert.ocr import render_gray

    engine(render_gray(page, DPI))
    gray, k, angle = engine.frame
    lines = [PageLine(_split_words(gray, box, text), tuple(box)) for box, text in engine.frame_lines if text.split()]
    _fit_to_lines(lines)
    return PageFrame(lines, [tuple(b) for b in engine.frame_pictures], (k, angle, gray.shape[:2]))


def frame_image(page: pymupdf.Page, frame: PageFrame) -> np.ndarray:
    """Цветное изображение страницы в кадре, в котором лежат рамки строк."""
    rgb = _render_rgb(page)
    if frame.turn is None:
        return rgb
    k, angle, shape = frame.turn
    return _turn_rgb(rgb, shape, k, angle)


def _fit_to_lines(lines: Sequence[PageLine]) -> None:
    """Плашки не заходят на соседние строки: верх и низ плашки ограничены серединой
    между буквами этой строки и строки выше или ниже, если они перекрываются по
    горизонтали. Плашка с запасом по высоте иначе закрывала половину соседней строки."""
    cores: list[Optional[Box]] = []
    for line in lines:
        boxes = [w.core for w in line.words if w.core]
        cores.append(_union(boxes) if boxes else None)
    for index, line in enumerate(lines):
        core = cores[index]
        if core is None:
            continue
        h = core[3] - core[1]
        top, bottom = core[1] - 0.3 * h, core[3] + 0.3 * h
        for other, near in enumerate(cores):
            if other == index or near is None or min(core[2], near[2]) - max(core[0], near[0]) <= 0:
                continue
            if (near[1] + near[3]) / 2 < (core[1] + core[3]) / 2:
                top = max(top, (near[3] + core[1]) / 2)
            else:
                bottom = min(bottom, (near[1] + core[3]) / 2)
        for word in line.words:
            x0, y0, x1, y1 = word.box
            word.box = (x0, max(y0, top), x1, max(max(y0, top) + 1, min(y1, bottom)))


def _text_words(page: pymupdf.Page) -> list[list[tuple[str, pymupdf.Rect]]]:
    """Слова строк текстового слоя: текст и рамка самих букв в координатах страницы.

    Рамки символов MuPDF берутся по метрикам шрифта и у многих шрифтов выше
    межстрочного интервала, поэтому высота считается от базовой линии по кеглю:
    от верха прописных до низа выносных."""
    lines: list[list[tuple[str, pymupdf.Rect]]] = []
    for block in page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES)["blocks"]:
        for line in block.get("lines", []):
            horizontal = abs(line["dir"][1]) < 1e-3
            words: list[tuple[str, pymupdf.Rect]] = []
            text, rect = "", None
            for span in line["spans"]:
                size = float(span["size"])
                for char in span["chars"]:
                    ch = char["c"]
                    if ch.isspace():
                        if text:
                            words.append((text, rect))
                        text, rect = "", None
                        continue
                    box = pymupdf.Rect(char["bbox"])
                    if horizontal:
                        baseline = char["origin"][1]
                        box = pymupdf.Rect(box.x0, baseline - 0.78 * size, box.x1, baseline + 0.24 * size)
                    text += ch
                    rect = box if rect is None else rect | box
            if text:
                words.append((text, rect))
            if words:
                lines.append(words)
    return lines


def text_frame(page: pymupdf.Page) -> PageFrame:
    """Страница с текстовым слоем: слова MuPDF в пикселях отрисовки."""
    to_pixels = page.rotation_matrix * pymupdf.Matrix(SCALE, SCALE)
    width, height = page.rect.width * SCALE, page.rect.height * SCALE
    lines: list[PageLine] = []
    for raw in _text_words(page):
        items = sorted(((rect * to_pixels, text) for text, rect in raw), key=lambda item: item[0].x0)
        h = max(1.0, max(r.height for r, _ in items))
        pad = 0.15 * h
        words: list[Word] = []
        for index, (rect, text) in enumerate(items):
            a = rect.x0 - 0.2 * h if index == 0 else (items[index - 1][0].x1 + rect.x0) / 2
            b = rect.x1 + 0.2 * h if index == len(items) - 1 else (rect.x1 + items[index + 1][0].x0) / 2
            words.append(Word(
                text,
                (max(0.0, min(a, rect.x0)), max(0.0, rect.y0 - pad), min(width, max(b, rect.x1)), min(height, rect.y1 + pad)),
                (rect.x0, rect.y0, rect.x1, rect.y1),
            ))
        box = pymupdf.Rect(items[0][0])
        for rect, _ in items[1:]:
            box |= rect
        lines.append(PageLine(words, (box.x0, box.y0, box.x1, box.y1)))
    _fit_to_lines(lines)
    area = page.rect.width * page.rect.height
    centers = [((w.box[0] + w.box[2]) / 2, (w.box[1] + w.box[3]) / 2) for ln in lines for w in ln.words]
    pictures: list[Box] = []
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & page.rect
        if rect.is_empty or rect.width * rect.height >= SMALL_IMAGE_SHARE * area:
            continue
        rect = rect * to_pixels
        # Картинка с текстом поверх — кусок скана с распознанным слоем, а не подпись
        # или логотип: данные на ней закрываются по словам.
        inside = sum(rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1 for x, y in centers)
        if inside < 3:
            pictures.append((rect.x0, rect.y0, rect.x1, rect.y1))
    return PageFrame(lines, pictures)


# ---------------------------------------------------------------- сборка PDF

def _fonts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ui" / "assets" / "fonts"


def _font_file() -> str:
    return str(_fonts_dir() / "IBMPlexSans-Regular.ttf")


def _union(boxes: Sequence[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _inside(box: Box, area: Box) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] <= cx <= area[2] and area[1] <= cy <= area[3]


def _label_size(frame: PageFrame) -> float:
    """Один кегль меток на странице: чуть меньше высоты букв обычной строки."""
    heights = sorted(w.core[3] - w.core[1] for line in frame.lines for w in line.words if w.core)
    if not heights:
        return 9.0
    return max(5.0, 0.72 * heights[len(heights) // 2] / SCALE)


def _fit_label(page: pymupdf.Page, font: pymupdf.Font, rect: pymupdf.Rect, label: str, size: float) -> None:
    """Метка по центру плашки кеглем страницы. Не помещается по ширине — сначала
    сжимается до 65%, потом уменьшается; выше плашки не бывает."""
    size = min(size, rect.height * 0.8)
    room = max(1.0, rect.width - 2.0)
    length = font.text_length(label, fontsize=size)
    sx = min(1.0, room / length) if length else 1.0
    if sx < 0.65:
        size *= sx / 0.65
        sx = 0.65
        length = font.text_length(label, fontsize=size)
    x = rect.x0 + (rect.width - length * sx) / 2
    y = rect.y0 + rect.height / 2 + size * 0.35
    page.insert_text((x, y), label, fontsize=size, fontname="plex", color=LABEL_COLOR,
                     morph=(pymupdf.Point(x, y), pymupdf.Matrix(sx, 1)))


def _paint(image: np.ndarray, frame: PageFrame, redactions: Sequence[Redaction], hidden: Sequence[int],
           font: pymupdf.Font) -> pymupdf.Pixmap:
    """Изображение страницы с плашками.

    Сначала пиксели под плашками стираются в массиве, затем поверх рисуются плашки
    с подписями, и страница растрируется заново: в итоговую картинку попадает только
    результат, исходное изображение остается в памяти."""
    height, width = image.shape[:2]

    def erase(box: Box, fill: tuple[float, float, float]) -> None:
        x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
        x1, y1 = min(width, int(np.ceil(box[2]))), min(height, int(np.ceil(box[3])))
        if x1 > x0 and y1 > y0:
            image[y0:y1, x0:x1] = [round(c * 255) for c in fill]

    plates: list[tuple[Box, str, tuple[float, float, float], bool]] = []
    for box in frame.pictures:
        plates.append((box, PICTURE_LABEL, PLATE_FILL, True))
    for index in hidden:
        line = frame.lines[index]
        plates.append((_union([w.box for w in line.words]), "", HIDDEN_FILL, False))
    for r in redactions:
        line = frame.lines[r.line]
        plates.append((_union([w.box for w in line.words[r.first: r.last + 1]]), r.label, PLATE_FILL, True))
    for box, _, fill, _ in plates:
        erase(box, fill)

    doc = pymupdf.open()
    page = doc.new_page(width=width / SCALE, height=height / SCALE)
    page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, width, height, image.tobytes(), False))
    page.insert_font(fontname="plex", fontbuffer=font.buffer)
    size = _label_size(frame)
    for box, label, fill, border in plates:
        rect = pymupdf.Rect(*box) / SCALE
        page.draw_rect(rect, color=PLATE_BORDER if border else None, fill=fill, width=0.6 if border else 0)
        if label:
            _fit_label(page, font, rect, label, size)
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB, alpha=False)
    doc.close()
    return pix


def _is_gray(image: np.ndarray) -> bool:
    sample = image[::4, ::4].astype(np.int16)
    spread = sample.max(axis=2) - sample.min(axis=2)
    return float(np.mean(spread > 24)) < 0.002


def _line_text(line: PageLine, redactions: Sequence[Redaction]) -> str:
    by_first = {r.first: r for r in redactions}
    parts: list[str] = []
    index = 0
    while index < len(line.words):
        r = by_first.get(index)
        if r is None:
            parts.append(line.words[index].text)
            index += 1
            continue
        piece = r.prefix + ("" if r.continued else r.label) + r.suffix
        if piece:
            parts.append(piece)
        index = r.last + 1
    return " ".join(parts)


def _write_text_layer(page: pymupdf.Page, frame: PageFrame, redactions: Sequence[Redaction],
                      hidden: set[int], font: pymupdf.Font) -> None:
    """Невидимый текст строк поверх картинки (режим отрисовки 3): по нему
    работают поиск, выделение и извлечение текста."""
    by_line: dict[int, list[Redaction]] = {}
    for r in redactions:
        by_line.setdefault(r.line, []).append(r)
    page.insert_font(fontname="plex", fontbuffer=font.buffer)
    for index, line in enumerate(frame.lines):
        if index in hidden:
            continue
        text = _line_text(line, by_line.get(index, []))
        if not text.strip():
            continue
        x0, y0, x1, y1 = (v / SCALE for v in line.box)
        if any(_inside(line.box, p) for p in frame.pictures):
            continue
        h = max(1.0, y1 - y0)
        size = h * 0.8
        length = font.text_length(text, fontsize=size)
        baseline = y1 - h * 0.22
        sx = (x1 - x0) / length if length else 1.0
        page.insert_text((x0, baseline), text, fontsize=size, fontname="plex", render_mode=3,
                         morph=(pymupdf.Point(x0, baseline), pymupdf.Matrix(sx, 1)))


def page_kinds(source: str | Path, force_ocr: bool = False, cancel: Optional[CancelToken] = None) -> list[str]:
    """Какие страницы распознаются (ocr), а какие читаются из текстового слоя (text):
    так же, как при обработке документа."""
    from ..convert.pdf import analyze_pdf

    try:
        return list(analyze_pdf(source, force_ocr=force_ocr, cancel=cancel).kinds)
    except ValueError as exc:  # PDF с паролем
        raise RedactedPdfError(str(exc)) from None
    except RuntimeError as exc:  # MuPDF не открыл файл
        raise RedactedPdfError(f"Не удалось открыть PDF: {exc}") from None


@dataclass
class _Progress:
    callback: Optional[Callable[[int, int], None]]

    def __call__(self, done: int, total: int) -> None:
        if self.callback:
            self.callback(done, total)


def export_redacted_pdf(
    source: str | Path,
    target: str | Path,
    source_text: str,
    entities: Sequence[Entity],
    model_dir: Optional[str | Path],
    force_ocr: bool = False,
    on_progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[CancelToken] = None,
    kinds: Optional[Sequence[str]] = None,
) -> RedactedPdfReport:
    """Собирает обезличенный PDF из исходного. Страницы-сканы распознаются заново
    той же моделью, что и при обработке, поэтому строки совпадают с Markdown."""
    progress = _Progress(on_progress)
    if kinds is None:
        kinds = page_kinds(source, force_ocr, cancel)
    engine = None
    if "ocr" in kinds:
        if model_dir is None:
            raise RedactedPdfError("Для сканов нужна модель распознавания текста.")
        engine = _frame_engine(Path(model_dir))

    frames: list[PageFrame] = []
    total = len(kinds)
    with pymupdf.open(source) as doc:
        for index, kind in enumerate(kinds):
            check(cancel)
            progress(index, total * 2)
            page = doc[index]
            frames.append(ocr_frame(page, engine) if kind == "ocr" else text_frame(page))
    check(cancel)

    known = _known_tokens(source_text)
    hidden_pages: list[set[int]] = []
    for frame in frames:
        hidden = {i for i, line in enumerate(frame.lines) if not _line_known(line, known)}
        hidden |= {i for i, line in enumerate(frame.lines) if any(_inside(line.box, p) for p in frame.pictures)}
        hidden_pages.append(hidden)
    line_count = sum(len(f.lines) for f in frames)
    hidden_count = sum(len(h) for h in hidden_pages)
    if line_count and hidden_count > FOREIGN_SHARE * line_count:
        raise RedactedPdfError(
            "Текст этого PDF почти не совпадает с документом. Выберите исходный файл, из которого он был получен."
        )

    values = replacement_values(source_text, entities)
    redactions, unmatched = find_redactions([f.lines for f in frames], values)

    font = pymupdf.Font(fontfile=_font_file())
    out = pymupdf.open()
    count = 0
    with pymupdf.open(source) as doc:
        for index, frame in enumerate(frames):
            check(cancel)
            progress(total + index, total * 2)
            page_redactions = [r for r in redactions[index] if r.line not in hidden_pages[index]]
            count += len(page_redactions)
            image = frame_image(doc[index], frame)
            pix = _paint(image, frame, page_redactions, sorted(hidden_pages[index]), font)
            if _is_gray(image):
                pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
            del image
            jpeg = pix.tobytes("jpeg", jpg_quality=80)
            page = out.new_page(width=pix.width / SCALE, height=pix.height / SCALE)
            page.insert_image(page.rect, stream=jpeg)
            _write_text_layer(page, frame, page_redactions, hidden_pages[index], font)
    progress(total * 2, total * 2)
    check(cancel)
    buffer = io.BytesIO()
    out.save(buffer, garbage=4, deflate=True)
    out.close()
    Path(target).write_bytes(buffer.getvalue())
    return RedactedPdfReport(pages=total, redactions=count, unmatched=unmatched, hidden_lines=hidden_count)

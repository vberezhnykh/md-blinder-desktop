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
    label: str  # плейсхолдер; пустой у продолжения значения на следующей строке
    prefix: str = ""  # часть первого слова до значения, остается в текстовом слое
    suffix: str = ""


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
            result[page].append(Redaction(line_index, first, last, "" if labeled else placeholder, prefix, suffix))
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
    pad_y = 0.12 * h
    top, bottom = max(0.0, y0 - pad_y), min(float(height), y1 + pad_y)
    if not words:
        return []
    ink = _ink(gray[y0:y1, x0:x1])
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
        return [Word(words[0], (max(0.0, left - edge), top, min(float(width), right + edge), bottom))]

    # Ожидаемые границы: середины пробелов в тексте строки.
    joined = " ".join(words)
    spaces = [i for i, ch in enumerate(joined) if ch == " "]
    span = max(1.0, right - left)
    expected = [left + span * (i + 0.5) / len(joined) for i in spaces]

    n, m = len(expected), len(gaps)
    virtual_cost = 1.2
    wide = [g[1] - g[0] >= 0.3 * h for g in gaps]

    def gap_cost(k: int, j: int) -> float:
        a, b = gaps[j]
        return abs((a + b) / 2 - expected[k]) / (0.6 * h + 0.03 * span) - min(1.0, (b - a) / (0.5 * h)) * 0.5

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
                skipped += 0.4 if wide[nj] else 0.0
    final = [best[n][j + 1] + sum(0.4 for g in range(j + 1, m) if wide[g]) for j in range(-1, m)]
    j = int(np.argmin(final)) - 1
    chosen: list[Optional[int]] = [None] * n
    for k in range(n, 0, -1):
        prev, gap = back[k][j + 1]
        chosen[k - 1] = gap
        j = prev

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
        result.append(Word(word, (a, top, max(a + 1, b), bottom)))
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
    return PageFrame(lines, [tuple(b) for b in engine.frame_pictures], (k, angle, gray.shape[:2]))


def frame_image(page: pymupdf.Page, frame: PageFrame) -> np.ndarray:
    """Цветное изображение страницы в кадре, в котором лежат рамки строк."""
    rgb = _render_rgb(page)
    if frame.turn is None:
        return rgb
    k, angle, shape = frame.turn
    return _turn_rgb(rgb, shape, k, angle)


def text_frame(page: pymupdf.Page) -> PageFrame:
    """Страница с текстовым слоем: слова MuPDF в пикселях отрисовки."""
    to_pixels = page.rotation_matrix * pymupdf.Matrix(SCALE, SCALE)
    width, height = page.rect.width * SCALE, page.rect.height * SCALE
    grouped: dict[tuple[int, int], list[tuple[pymupdf.Rect, str]]] = {}
    for x0, y0, x1, y1, text, block, line, _ in page.get_text("words", sort=False):
        rect = pymupdf.Rect(x0, y0, x1, y1) * to_pixels
        grouped.setdefault((block, line), []).append((rect, text))
    lines: list[PageLine] = []
    for items in grouped.values():
        items.sort(key=lambda item: item[0].x0)
        h = max(1.0, max(r.height for r, _ in items))
        pad = 0.12 * h
        words: list[Word] = []
        for index, (rect, text) in enumerate(items):
            a = rect.x0 - 0.2 * h if index == 0 else (items[index - 1][0].x1 + rect.x0) / 2
            b = rect.x1 + 0.2 * h if index == len(items) - 1 else (rect.x1 + items[index + 1][0].x0) / 2
            words.append(Word(text, (max(0.0, min(a, rect.x0)), max(0.0, rect.y0 - pad),
                                     min(width, max(b, rect.x1)), min(height, rect.y1 + pad))))
        box = pymupdf.Rect(items[0][0])
        for rect, _ in items[1:]:
            box |= rect
        lines.append(PageLine(words, (box.x0, box.y0, box.x1, box.y1)))
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


def _fit_label(page: pymupdf.Page, font: pymupdf.Font, rect: pymupdf.Rect, label: str) -> None:
    """Плейсхолдер по центру плашки; не помещается — сжимается по ширине."""
    size = rect.height * 0.62
    length = font.text_length(label, fontsize=size)
    room = rect.width * 0.94
    sx = min(1.0, room / length) if length else 1.0
    if sx < 0.6:
        # Узкая плашка: шрифт меньше, но не мельче 40% высоты, остальное — сжатием.
        size = max(rect.height * 0.4, size * sx / 0.6)
        length = font.text_length(label, fontsize=size)
        sx = min(1.0, room / length) if length else 1.0
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
    for box, label, fill, border in plates:
        rect = pymupdf.Rect(*box) / SCALE
        page.draw_rect(rect, color=PLATE_BORDER if border else None, fill=fill, width=0.6 if border else 0)
        if label:
            _fit_label(page, font, rect, label)
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
        piece = r.prefix + r.label + r.suffix
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

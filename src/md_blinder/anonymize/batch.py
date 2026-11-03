"""Пакет документов с согласованными метками (порт src/lib/anonymize/batch.ts).

Пакет — несколько документов, которые обезличиваются согласованно: одно и то
же значение (сторона договора, реквизит) получает одну и ту же метку во всех
документах пакета. Например, в договорах «Альфа — Бета» и «Бета — Гамма»
«Бета» везде становится [COMPANY_2].

Метки хранятся в самих документах, отдельного словаря нет. Словарь пакета
каждый раз выводится из разметки остальных документов: значение связано с
меткой, если совпадает нормализованная форма любого из вхождений. Поэтому
правки разметки в одном документе сразу учитываются при согласовании остальных.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Protocol, Sequence, TypeVar

import regex

from ..convert.script_mix import TO_LAT
from .apply import build_anonymized_text, entity_current_value, next_placeholder_number
from .csv_export import _SURROGATE_RE, _csv_escape
from .image_safety import mask_images_for_detection
from .normalize import normalize_value
from .related import company_core_words
from .types import Entity, EntityType, Position

MIN_SEED_LENGTH = 3
# Адрес связывается с адресом другого документа и по вложению («P.O. Box 3159,
# Road Town» внутри «Quijano Chambers, P.O. Box 3159, Road Town»), если короткая
# форма не короче этого: иначе «London» связал бы все лондонские адреса.
MIN_ADDRESS_LINK_LENGTH = 12

_PLACEHOLDER_RE = re.compile(r"^\[([A-Z_]+)_(\d+)\]$")
_PLACEHOLDER_NUM_RE = re.compile(r"_(\d+)\]$")


class BatchMember(Protocol):
    source_text: str
    entities: list[Entity]


M = TypeVar("M", bound=BatchMember)


def _is_main(e: Entity) -> bool:
    return not e.candidate and not e.dismissed


def _placeholder_number(placeholder: str) -> Optional[int]:
    m = _PLACEHOLDER_NUM_RE.search(placeholder)
    return int(m.group(1)) if m else None


def _key(type_: EntityType, text: str) -> str:
    return f"{type_} {normalize_value(type_, text)}"


def _entity_keys(e: Entity, source_text: str) -> list[str]:
    """Нормализованные формы всех вхождений сущности, в порядке появления в тексте,
    и формы из других документов, с которыми пользователь объединил сущность."""
    keys: dict[str, None] = {}
    for p in sorted(e.positions, key=lambda p: p.start):
        keys[_key(e.type, source_text[p.start:p.end])] = None
    for alias in e.batch_aliases:
        keys[_key(e.type, alias)] = None
    return list(keys)


def _company_core_keys(e: Entity, source_text: str) -> list[str]:
    """Запасной ключ для компаний: название без ОПФ и кавычек, чтобы «ООО «Гамма»»
    в одном договоре и «Гамма» в другом считались одной стороной."""
    if e.type != "COMPANY":
        return []
    keys: dict[str, None] = {}
    for p in e.positions:
        core = " ".join(company_core_words(source_text[p.start:p.end])).lower()
        if len(core) >= MIN_SEED_LENGTH:
            keys[core] = None
    return list(keys)


def _address_values(e: Entity, source_text: str) -> list[str]:
    if e.type != "ADDRESS":
        return []
    # Знаки по краям не мешают сравнению: «, p.o. box 3159, road town,».
    return [k.split(" ", 1)[1].strip(" ,;:") for k in _entity_keys(e, source_text)]


def _contains_words(long: str, short: str) -> bool:
    """short входит в long целыми словами: «12 main street» не входит в «112 main street»."""
    pattern = rf"(?<![\p{{L}}\p{{N}}]){regex.escape(short)}(?![\p{{L}}\p{{N}}])"
    return regex.search(pattern, long, regex.V0) is not None


@dataclass
class _Dictionary:
    by_key: dict[str, str]
    by_core: dict[str, Optional[str]]  # None — запасной ключ ведет к разным меткам
    used_placeholders: set[str]
    addresses: list[tuple[str, str]]  # (нормализованный адрес, метка)

    def by_address(self, values: list[str], taken: set[str]) -> Optional[str]:
        """Метка адреса другого документа, который содержит один из адресов
        сущности или содержится в нем. None, если такого нет или меток несколько."""
        found: set[str] = set()
        for value in values:
            for other, placeholder in self.addresses:
                short, long = sorted((value, other), key=len)
                if len(short) >= MIN_ADDRESS_LINK_LENGTH and _contains_words(long, short):
                    found.add(placeholder)
        found -= taken
        return next(iter(found)) if len(found) == 1 else None


def _build_dictionary(members: Sequence[BatchMember]) -> _Dictionary:
    by_key: dict[str, str] = {}
    by_core: dict[str, Optional[str]] = {}
    used: set[str] = set()
    addresses: list[tuple[str, str]] = []
    for member in members:
        for e in member.entities:
            if not _is_main(e):
                continue
            used.add(e.placeholder)
            addresses.extend((value, e.placeholder) for value in _address_values(e, member.source_text))
            for key in _entity_keys(e, member.source_text):
                by_key.setdefault(key, e.placeholder)
            for core in _company_core_keys(e, member.source_text):
                if core not in by_core:
                    by_core[core] = e.placeholder
                elif by_core[core] != e.placeholder:
                    by_core[core] = None
    return _Dictionary(by_key, by_core, used, addresses)


def _by_placeholder_number(entities: list[Entity]) -> list[Entity]:
    """Порядок по номеру метки; сущности без номера в конце, взаимный порядок сохраняется."""
    indexed = list(enumerate(entities))
    indexed.sort(key=lambda x: (_placeholder_number(x[1].placeholder) is None,
                                _placeholder_number(x[1].placeholder) or 0, x[0]))
    return [e for _, e in indexed]


def reconcile_batch_placeholders(
    entities: list[Entity], source_text: str, others: Sequence[BatchMember]
) -> list[Entity]:
    """Согласует метки документа с остальными документами пакета.

    1. Значение, которое уже есть в другом документе, получает его метку. Адрес
       связывается и по вложению: расширенный или укороченный в одном документе
       адрес сохраняет общую метку.
    2. Остальные сохраняют свою метку, если ее не занимает другое значение пакета.
    3. Иначе получают наименьший свободный в пакете номер.
    4. Кандидаты и отклоненные нумеруются после всех занятых номеров, как в
       compact_placeholder_numbers, чтобы принятие кандидата не давало коллизий.

    Две сущности документа никогда не получают одну метку: если пользователь
    разделил значения в этом документе, разделение сохраняется. Возвращает тот же
    список, если ни одна метка не изменилась.
    """
    if not others:
        return entities
    d = _build_dictionary(others)
    assigned: dict[str, str] = {}
    taken: set[str] = set()
    main = _by_placeholder_number([e for e in entities if _is_main(e)])

    for e in main:
        linked = next(
            (p for p in (d.by_key.get(k) for k in _entity_keys(e, source_text)) if p is not None and p not in taken),
            None,
        )
        if linked is None:
            linked = next(
                (p for p in (d.by_core.get(c) for c in _company_core_keys(e, source_text))
                 if isinstance(p, str) and p not in taken),
                None,
            )
        if linked is not None:
            assigned[e.id] = linked
            taken.add(linked)

    # Вложение адресов проверяется после точных совпадений: иначе адрес мог бы занять
    # метку, которая по точной форме принадлежит другой сущности документа.
    for e in main:
        if e.id in assigned:
            continue
        linked = d.by_address(_address_values(e, source_text), taken)
        if linked is not None:
            assigned[e.id] = linked
            taken.add(linked)

    for e in main:
        if e.id in assigned:
            continue
        own = e.placeholder
        if _placeholder_number(own) is not None and own not in taken and own not in d.used_placeholders:
            assigned[e.id] = own
            taken.add(own)

    for e in main:
        if e.id in assigned:
            continue
        n = 1
        while f"[{e.type}_{n}]" in taken or f"[{e.type}_{n}]" in d.used_placeholders:
            n += 1
        fresh = f"[{e.type}_{n}]"
        assigned[e.id] = fresh
        taken.add(fresh)

    max_used: dict[str, int] = {}
    for p in taken | d.used_placeholders:
        m = _PLACEHOLDER_RE.match(p)
        if m:
            max_used[m.group(1)] = max(max_used.get(m.group(1), 0), int(m.group(2)))
    for e in _by_placeholder_number([e for e in entities if not _is_main(e)]):
        n = max_used.get(e.type, 0) + 1
        max_used[e.type] = n
        assigned[e.id] = f"[{e.type}_{n}]"

    changed = False
    result: list[Entity] = []
    for e in entities:
        nxt = assigned.get(e.id, e.placeholder)
        if nxt == e.placeholder:
            result.append(e)
        else:
            changed = True
            result.append(replace(e, placeholder=nxt))
    return result if changed else entities


def align_batch(docs: Sequence[M]) -> list[M]:
    """Согласует весь пакет по порядку: каждый документ — с уже согласованными
    предыдущими. Документы без изменений возвращаются теми же объектами."""
    aligned: list[M] = []
    for doc in docs:
        entities = reconcile_batch_placeholders(doc.entities, doc.source_text, list(aligned))
        aligned.append(doc if entities is doc.entities else replace(doc, entities=entities))  # type: ignore[type-var]
    return aligned


def seed_from_batch(source_text: str, entities: list[Entity], others: Sequence[BatchMember]) -> list[Entity]:
    """Размечает в новом документе точные формы значений, которые пользователь уже
    подтвердил в других документах пакета, даже если NER их здесь не нашел.

    Совпадение проверяется с границами слов и по нормализованной форме. Найденное
    вхождение добавляется к сущности документа с той же формой, или принимает
    непроверенного кандидата, который ровно его покрывает, или становится новой
    сущностью (метку выставит reconcile_batch_placeholders). Вхождения,
    пересекающие другую разметку, пропускаются.
    """
    if not others:
        return entities
    forms: dict[str, tuple[EntityType, str]] = {}
    for member in others:
        for e in member.entities:
            if not _is_main(e) or not e.anonymize:
                continue
            for p in e.positions:
                text = member.source_text[p.start:p.end].strip()
                if len(text) >= MIN_SEED_LENGTH:
                    forms.setdefault(f"{e.type} {text.lower()}", (e.type, text))
    if not forms:
        return entities

    searchable = mask_images_for_detection(source_text)
    working = [replace(e, positions=list(e.positions)) for e in entities]
    claimed = [(p.start, p.end) for e in working for p in e.positions]
    created: dict[str, Entity] = {}
    changed = False

    # Длинные формы первыми: «ООО «Гамма»» занимает место раньше, чем «Гамма».
    for type_, text in sorted(forms.values(), key=lambda f: -len(f[1])):
        key = _key(type_, text)
        pattern = regex.compile(
            rf"(?<![\p{{L}}\p{{N}}]){regex.escape(text)}(?![\p{{L}}\p{{N}}])", regex.IGNORECASE | regex.V0
        )
        for m in pattern.finditer(searchable):
            start, end = m.start(), m.end()
            if _key(type_, source_text[start:end]) != key:
                continue
            overlapping = [c for c in claimed if start < c[1] and c[0] < end]
            if overlapping:
                candidate = next(
                    (e for e in working if e.candidate and not e.dismissed and e.type == type_
                     and any(p.start == start and p.end == end for p in e.positions)),
                    None,
                )
                if candidate is not None and len(overlapping) == 1:
                    candidate.candidate = False
                    candidate.candidate_note = None
                    candidate.related_to = None
                    candidate.anonymize = True
                    changed = True
                continue
            owner = next(
                (e for e in working if _is_main(e) and e.type == type_ and key in _entity_keys(e, source_text)),
                None,
            ) or created.get(key)
            if owner is not None:
                owner.positions.append(Position(start, end))
            else:
                entity = Entity(
                    id=str(uuid.uuid4()),
                    type=type_,
                    value=source_text[start:end],
                    placeholder=f"[{type_}_{next_placeholder_number(working, type_)}]",
                    positions=[Position(start, end)],
                )
                created[key] = entity
                working.append(entity)
            claimed.append((start, end))
            changed = True
    if not changed:
        return entities
    for e in working:
        e.positions.sort(key=lambda p: p.start)
    return working


# --- Объединение с заменой из другого документа ---

# Типы, в которых OCR и опечатки дают разные написания одного значения. В номерах
# и реквизитах отличие в одном знаке — уже другое значение.
SIMILAR_TYPES: frozenset[str] = frozenset({"PERSON", "COMPANY", "ADDRESS"})
MIN_SIMILAR_LENGTH = 6
# На сколько знаков допускается отличие: один на каждые 7 знаков более длинной формы.
# «makedon kiriakidis» и «makedon kirjakidjs» (18 знаков, 2 отличия) похожи,
# «john smith» и «jane smith» (10 знаков, 3 отличия) — нет.
SIMILAR_CHARS_PER_EDIT = 7

_NON_WORD_RE = regex.compile(r"[^\p{L}\p{N}]+")
_DIGITS_RE = regex.compile(r"\p{N}+")


@dataclass(frozen=True)
class BatchTarget:
    """Замена из других документов пакета: одна метка и все ее исходные формы."""

    placeholder: str
    type: EntityType
    forms: tuple[str, ...]  # исходные формы, первая — для показа
    docs: tuple[str, ...]  # имена документов, где метка встречается

    @property
    def value(self) -> str:
        return self.forms[0]


def batch_targets(others: Sequence[BatchMember]) -> list[BatchTarget]:
    """Подтвержденные замены остальных документов пакета, по одной на метку.

    Документы пакета согласованы, поэтому одна метка в разных документах — одно
    значение; его формы собираются со всех документов."""
    forms: dict[str, dict[str, str]] = {}  # метка → ключ формы → форма
    types: dict[str, EntityType] = {}
    docs: dict[str, dict[str, None]] = {}
    for member in others:
        name = getattr(member, "name", "")
        for e in member.entities:
            if not _is_main(e) or not e.positions:
                continue
            types.setdefault(e.placeholder, e.type)
            if types[e.placeholder] != e.type:
                continue
            bucket = forms.setdefault(e.placeholder, {})
            texts = [member.source_text[p.start:p.end] for p in sorted(e.positions, key=lambda p: p.start)]
            for text in [*texts, *e.batch_aliases]:
                text = " ".join(text.split())
                if text:
                    bucket.setdefault(_key(e.type, text), text)
            docs.setdefault(e.placeholder, {})[name] = None
    targets = [
        BatchTarget(placeholder, types[placeholder], tuple(bucket.values()), tuple(docs[placeholder]))
        for placeholder, bucket in forms.items() if bucket
    ]
    targets.sort(key=lambda t: (t.type, _placeholder_number(t.placeholder) or 0, t.placeholder))
    return targets


def link_to_batch(entities: list[Entity], entity_id: str, target: BatchTarget) -> list[Entity]:
    """Объединяет сущность документа с заменой из другого документа пакета.

    Формы цели запоминаются у сущности (batch_aliases), и согласование пакета
    (reconcile_batch_placeholders) дает ей метку цели. Связь живет в разметке
    документа: остальные документы получают ту же метку при своем согласовании.
    """
    entity = next((e for e in entities if e.id == entity_id), None)
    if entity is None or entity.type != target.type or not _is_main(entity):
        return entities
    aliases = list(dict.fromkeys([*entity.batch_aliases, *target.forms]))
    return [
        replace(e, batch_aliases=aliases, placeholder=target.placeholder) if e.id == entity_id else e
        for e in entities
    ]


def _fold(text: str) -> str:
    """Форма для сравнения написаний: строчные, кириллические двойники латинских
    букв — латиница (OCR путает «о» и «o»), знаки — один пробел."""
    return _NON_WORD_RE.sub(" ", text.lower().translate(TO_LAT)).strip()


def _within_edits(a: str, b: str, limit: int) -> bool:
    """Расстояние Левенштейна между a и b не больше limit."""
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return False
        previous = current
    return previous[-1] <= limit


def similar_spelling(a: str, b: str, type_: EntityType = "PERSON") -> bool:
    """Два написания похожи на одно значение с ошибками OCR или опечаткой.

    В адресе цифры должны совпадать: «12 Main Street» и «14 Main Street» — разные
    адреса. В имени и названии цифра обычно ошибка OCR: «DIALOQ LTD4» вместо «LTDA»."""
    fa, fb = _fold(a), _fold(b)
    if min(len(fa), len(fb)) < MIN_SIMILAR_LENGTH:
        return False
    if type_ == "ADDRESS" and _DIGITS_RE.findall(fa) != _DIGITS_RE.findall(fb):
        return False
    limit = max(1, max(len(fa), len(fb)) // SIMILAR_CHARS_PER_EDIT)
    return _within_edits(fa, fb, limit)


class BatchIndex:
    """Замены остальных документов пакета для объединения и подсказок.

    Строится один раз на открытый пакет; сравнение написаний кешируется по
    форме, поэтому перерисовка списка после каждой правки не пересчитывает его."""

    def __init__(self, others: Sequence[BatchMember]) -> None:
        self.targets = batch_targets(others)
        self._by_placeholder = {t.placeholder: t for t in self.targets}
        self._similar_cache: dict[tuple[str, str], tuple[str, ...]] = {}

    def target(self, placeholder: str) -> Optional[BatchTarget]:
        return self._by_placeholder.get(placeholder)

    def merge_targets(self, entity: Entity, entities: Iterable[Entity]) -> list[BatchTarget]:
        """Замены других документов того же типа, которых нет в этом документе.
        Метки, которые есть и здесь, объединяются обычным образом внутри документа."""
        local = {e.placeholder for e in entities if _is_main(e)}
        return [t for t in self.targets if t.type == entity.type and t.placeholder not in local]

    def similar(self, entity: Entity, source_text: str, entities: Iterable[Entity]) -> list[BatchTarget]:
        """Замены других документов, похожие на сущность по написанию."""
        if entity.type not in SIMILAR_TYPES or not _is_main(entity):
            return []
        available = {t.placeholder: t for t in self.merge_targets(entity, entities)}
        if not available:
            return []
        found: dict[str, None] = {}
        forms = [source_text[p.start:p.end] for p in entity.positions] + entity.batch_aliases
        for form in dict.fromkeys(" ".join(f.split()) for f in forms):
            key = (entity.type, form)
            if key not in self._similar_cache:
                self._similar_cache[key] = tuple(
                    t.placeholder for t in self.targets
                    if t.type == entity.type and any(similar_spelling(form, other, entity.type) for other in t.forms)
                )
            for placeholder in self._similar_cache[key]:
                if placeholder in available:
                    found[placeholder] = None
        return [available[p] for p in found]


# --- Выдача пакета ---

class BatchExportDoc(BatchMember, Protocol):
    name: str


def batch_to_markdown(docs: Sequence[BatchExportDoc]) -> str:
    """Документы пакета одним Markdown-файлом. Заголовки нейтральные: имя файла
    часто содержит название стороны («Договор с Гаммой.docx»), поэтому в
    обезличенный текст оно не попадает."""
    parts = [
        f"# Документ {i + 1}\n\n{build_anonymized_text(doc.source_text, doc.entities).strip()}\n"
        for i, doc in enumerate(docs)
    ]
    return "\n---\n\n".join(parts)


def batch_entities_to_csv(docs: Sequence[BatchExportDoc]) -> str:
    """Общая таблица замен пакета: одна строка на метку, все исходные формы и
    документы, где метка встречается. Содержит исходные данные."""
    rows: dict[str, dict] = {}
    for i, doc in enumerate(docs):
        label = f"Документ {i + 1} ({doc.name})"
        for e in doc.entities:
            if not e.anonymize:
                continue
            row = rows.setdefault(e.placeholder, {"type": e.type, "values": [], "docs": [], "count": 0})
            forms = [entity_current_value(doc.source_text, e)] + [doc.source_text[p.start:p.end] for p in e.positions]
            for form in forms:
                if form not in row["values"]:
                    row["values"].append(form)
            if label not in row["docs"]:
                row["docs"].append(label)
            row["count"] += len(e.positions)
    ordered = sorted(rows.items(), key=lambda kv: (kv[1]["type"], _placeholder_number(kv[0]) or 0))
    lines = [["Плейсхолдер", "Тип", "Оригинальные значения", "Документы", "Вхождений"]]
    lines += [[p, r["type"], "; ".join(r["values"]), "; ".join(r["docs"]), str(r["count"])] for p, r in ordered]
    return "sep=,\r\n" + "\r\n".join(",".join(_csv_escape(c) for c in row) for row in lines)


def batch_csv_bytes(docs: Sequence[BatchExportDoc]) -> bytes:
    """Файл таблицы замен пакета: UTF-8 с BOM, как у таблицы одного документа."""
    return _SURROGATE_RE.sub("\ufffd", "\ufeff" + batch_entities_to_csv(docs)).encode("utf-8")

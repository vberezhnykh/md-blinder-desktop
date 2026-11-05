"""Имя выгружаемого файла без персональных данных.

Имя исходника часто содержит сторону договора («Договор Иванов И.И.docx»,
«NDA_Acme_Corp.pdf»), и вместе с обезличенным текстом оно уходило бы наружу.
Найденные в имени значения документа заменяются теми же метками, что в тексте:
«Договор [PERSON_1].md». Ищутся те же формы, что при поиске повторов в тексте:
значение целиком, инициалы с фамилией, падежи фамилии, слова названия компании.
"""

from __future__ import annotations

from .related import _forms_for
from .types import Entity

# Подчеркивания в именах файлов стоят вместо пробелов («NDA_Acme_Corp»).
_NAME_SEPARATORS = str.maketrans({"_": " "})


def active_entities(entities: list[Entity]) -> list[Entity]:
    return [e for e in entities if e.anonymize and not e.candidate and not e.dismissed]


def anonymize_file_name(name: str, entities: list[Entity]) -> str:
    """Заменяет значения сущностей в имени файла их метками. Расширение и остальной
    текст имени не меняются; пересекающиеся совпадения берет более длинное."""
    searchable = name.translate(_NAME_SEPARATORS)
    spans: list[tuple[int, int, str]] = []
    for entity in active_entities(entities):
        for form in _forms_for(entity):
            for m in form.pattern.finditer(searchable):
                if m.end() > m.start() and len(m.group(0)) >= form.min_len:
                    spans.append((m.start(), m.end(), entity.placeholder))
    spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    taken: list[tuple[int, int, str]] = []
    for start, end, placeholder in spans:
        if not any(start < t_end and t_start < end for t_start, t_end, _ in taken):
            taken.append((start, end, placeholder))
    result = name
    for start, end, placeholder in sorted(taken, reverse=True):
        result = result[:start] + placeholder + result[end:]
    return result

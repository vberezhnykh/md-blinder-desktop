"""Ключ группировки значений (порт src/lib/anonymize/normalizeValue.ts)."""

from __future__ import annotations

import re

from .types import EntityType


def normalize_value(type_: EntityType, value: str) -> str:
    trimmed = re.sub(r"\s+", " ", value.strip())
    if type_ in ("CARD", "IBAN", "ACCOUNT", "REG_NUMBER", "SWIFT", "ID_DOC"):
        return re.sub(r"[\s-]", "", trimmed).upper()
    if type_ in ("PERSON", "COMPANY", "ADDRESS", "EMAIL"):
        return trimmed.lower()
    if type_ == "PHONE":
        return re.sub(r"[\s\-().]", "", trimmed)
    if type_ == "URL":
        url = trimmed.lower()
        url = re.sub(r"^[a-z]+://", "", url)
        url = re.sub(r"^www\.", "", url)
        return re.sub(r"/+$", "", url)
    return trimmed

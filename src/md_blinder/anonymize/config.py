"""Пороги уверенности NER (порт src/lib/anonymize/config.ts).

score ≥ NER_CONFIRM_THRESHOLD — замена без проверки; score в [NER_GRAY_ZONE_LOW,
NER_CONFIRM_THRESHOLD) — кандидат на ручную проверку; ниже — отбрасывается.
"""

NER_CONFIRM_THRESHOLD = 0.3
NER_GRAY_ZONE_LOW = 0.12

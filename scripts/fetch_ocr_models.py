"""Скачивает модели Indra OCR в каталог данных; для CI, где тесты OCR должны идти всегда."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from md_blinder.components.ocr_models import download_ocr_models  # noqa: E402

for attempt in range(3):
    try:
        print(download_ocr_models())
        break
    except Exception as error:  # noqa: BLE001
        print(f"Попытка {attempt + 1}: {error}")
        time.sleep(10)
else:
    sys.exit(1)

"""Модели Indra OCR (datalab-to/indra-ocr), скачиваются по запросу.

Ревизия Hugging Face, размеры и хеши закреплены, поэтому подмена файла на сервере
не пройдет проверку. Берутся только модели, нужные для латиницы и кириллицы:
поиск строк, распознавание, порядок чтения, ориентация страницы и языковая модель
для неуверенных строк. Распознаватели других письменностей (CJK, арабское письмо,
индийские) и классификатор письменности не скачиваются: без них все строки читает
основной распознаватель, на документах корпуса результат тот же.

Ревизия совпадает с моделями, под которые собран пакет indra-ocr из pyproject.toml.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from .. import paths
from ..cancel import CancelToken
from .downloader import download_file
from .model_asset import ModelAsset

OCR_REPO = "datalab-to/indra-ocr"
# Тег v1.1.0 (indra-ocr 1.1.x).
OCR_REVISION = "bd02167fdf2005a19962cc15188f8496a2c87d3b"
OCR_BASE_URL = f"https://huggingface.co/{OCR_REPO}/resolve/{OCR_REVISION}"


def _asset(name: str, size: int, sha256: str) -> ModelAsset:
    return ModelAsset(name, f"{OCR_BASE_URL}/{name}", size, sha256)


# Маленькие файлы первыми; языковая модель последней.
ASSETS: tuple[ModelAsset, ...] = (
    _asset("meta.json", 211_869, "4ce4c94b46f5d644033c02e718322014ff4081270f385038c571912b56696339"),
    _asset("det.onnx", 389_948, "f2bd4cba23e9c328cf7ec824634e423e191a2c6aacbbf35c62bd79dd70f62f59"),
    _asset("orient.onnx", 371_802, "20eaf670f55a7cf9facfe1bdd6c7b8e40be75bbf4d48a3df43f66aa3bbd874fd"),
    _asset("order.onnx", 2_973_138, "a42db2e8b536ad3d84045a4b7f9b0a72667cf8be4aa02d63f7b38004719fb180"),
    _asset("rec.onnx", 5_042_161, "475972a2710be4a3cbfeb2bc7f3624ffd563de07de42476fc7a290d4e1ece484"),
    _asset("lm.npz", 15_695_957, "6c0fb618a363a0acdd05d4f5b0edd9e8146d2d990541b87f6130bbfe17cea58d"),
    # Лицензия весов (OpenRAIL-M с ограничениями), хранится рядом с ними.
    _asset("LICENSE", 14_750, "50c88a018a7ee322e89f6b09401b237beb0cd331685879405b3db48993950a5f"),
)
TOTAL_BYTES = sum(asset.size for asset in ASSETS)
TOTAL_MB = round(TOTAL_BYTES / 1e6)


def ocr_models_dir() -> Path:
    return paths.models_dir() / f"indra-ocr-{OCR_REVISION}"


def _is_present(asset: ModelAsset, directory: Path) -> bool:
    # Как у модели NER: только размер, хеш проверяется при загрузке.
    try:
        return (directory / asset.name).stat().st_size == asset.size
    except OSError:
        return False


def missing_assets(directory: Optional[Path] = None) -> list[ModelAsset]:
    directory = directory or ocr_models_dir()
    return [asset for asset in ASSETS if not _is_present(asset, directory)]


def is_ocr_present(directory: Optional[Path] = None) -> bool:
    return not missing_assets(directory)


def download_ocr_models(
    on_progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[CancelToken] = None,
    directory: Optional[Path] = None,
) -> Path:
    """Докачивает недостающие файлы. Прогресс — суммарные байты (получено, всего)."""
    directory = directory or ocr_models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    done = sum(asset.size for asset in ASSETS if _is_present(asset, directory))
    for asset in missing_assets(directory):
        base = done

        def report(received: int, _total: Optional[int], base: int = base) -> None:
            if on_progress:
                on_progress(base + received, TOTAL_BYTES)

        download_file(
            asset.url, directory / asset.name, expected_size=asset.size, sha256=asset.sha256,
            on_progress=report, cancel=cancel,
        )
        done += asset.size
    if on_progress:
        on_progress(TOTAL_BYTES, TOTAL_BYTES)
    # Языковые файлы Tesseract из версий до 0.4.0 больше не нужны.
    shutil.rmtree(paths.data_dir() / "tessdata", ignore_errors=True)
    return directory

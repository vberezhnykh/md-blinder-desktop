"""Закрепленные файлы модели GLiNER (порт src/workers/modelAssets.ts).

Модель fp16 и токенизатор закреплены одной ревизией Hugging Face: смена ревизии
дает новый каталог, старые файлы не смешиваются с новыми. Вместо Cache Storage
браузера — каталог models/ в данных приложения.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .. import paths
from ..cancel import CancelToken, check
from .downloader import CHUNK_SIZE, DownloadError, download_file, part_path, sha256_file

MODEL_REPO = "onnx-community/gliner_multi-v2.1"
MODEL_REVISION = "6ddaeb9413b0e71ad8457da1aab378a165b24058"
MODEL_BASE_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}"
MODEL_FILE = "model_fp16.onnx"
MODEL_URL_FP16 = f"{MODEL_BASE_URL}/onnx/{MODEL_FILE}"
MODEL_BYTES_FP16 = 579_717_643
# SHA-256 из LFS-указателя Hugging Face (x-linked-etag) на той же ревизии.
MODEL_SHA256_FP16 = "9cec3c771bfa7e886e029cd013326b13ad7d18f58fac33824f43c106fbdfe920"
TOKENIZER_FILE = "tokenizer.json"
TOKENIZER_CONFIG_FILE = "tokenizer_config.json"


@dataclass(frozen=True)
class ModelAsset:
    name: str
    url: str
    size: int
    sha256: str


TOKENIZER_ASSET = ModelAsset(
    TOKENIZER_FILE, f"{MODEL_BASE_URL}/{TOKENIZER_FILE}", 16_331_948,
    "914bd3c8fb7b525af9e23b60d0ec7b1248ddb2b99014efd9c02ebeb022f8cab7",
)
TOKENIZER_CONFIG_ASSET = ModelAsset(
    TOKENIZER_CONFIG_FILE, f"{MODEL_BASE_URL}/{TOKENIZER_CONFIG_FILE}", 1_806,
    "78f866883daf7ee2bc400200a155cdbe9116ed0a6ed597ff573ea2c9862a89a6",
)
MODEL_ASSET = ModelAsset(MODEL_FILE, MODEL_URL_FP16, MODEL_BYTES_FP16, MODEL_SHA256_FP16)
# Маленькие файлы первыми: при обрыве на модели токенизатор уже на месте.
ASSETS: tuple[ModelAsset, ...] = (TOKENIZER_CONFIG_ASSET, TOKENIZER_ASSET, MODEL_ASSET)
TOTAL_BYTES = sum(asset.size for asset in ASSETS)

ByteProgress = Callable[[int, int], None]


def model_dir() -> Path:
    return paths.models_dir() / f"gliner_multi-v2.1-{MODEL_REVISION}"


def _is_present(asset: ModelAsset, directory: Path) -> bool:
    # Только размер: хеш 580 МБ на каждом запуске слишком дорог; хеш проверяется
    # при загрузке и импорте, а качество модели — калибровкой при старте.
    try:
        return (directory / asset.name).stat().st_size == asset.size
    except OSError:
        return False


def missing_assets(directory: Optional[Path] = None) -> list[ModelAsset]:
    directory = directory or model_dir()
    return [asset for asset in ASSETS if not _is_present(asset, directory)]


def is_model_present(directory: Optional[Path] = None) -> bool:
    return not missing_assets(directory)


def download_model(
    on_progress: Optional[ByteProgress] = None,
    cancel: Optional[CancelToken] = None,
    directory: Optional[Path] = None,
) -> Path:
    """Докачивает недостающие файлы. Прогресс — суммарные байты (получено, всего)."""
    directory = directory or model_dir()
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
    return directory


def _copy_verified(
    source: Path, asset: ModelAsset, directory: Path,
    on_progress: Optional[ByteProgress], cancel: Optional[CancelToken],
) -> None:
    size = source.stat().st_size
    if size != asset.size:
        raise DownloadError(f"Файл {source.name} не подходит: {size} байт вместо {asset.size}")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / asset.name
    part = part_path(target)
    digest = hashlib.sha256()
    copied = 0
    try:
        with open(source, "rb") as src, open(part, "wb") as dst:
            while chunk := src.read(CHUNK_SIZE):
                check(cancel)
                digest.update(chunk)
                dst.write(chunk)
                copied += len(chunk)
                if on_progress:
                    on_progress(copied, asset.size)
        if digest.hexdigest() != asset.sha256:
            raise DownloadError(f"Файл {source.name} поврежден или от другой версии модели")
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def import_model_file(
    path: Path | str,
    on_progress: Optional[ByteProgress] = None,
    cancel: Optional[CancelToken] = None,
    directory: Optional[Path] = None,
) -> list[ModelAsset]:
    """Импортирует выбранный пользователем model_fp16.onnx или папку с файлами модели.

    Возвращает файлы, которых все еще не хватает (их докачает download_model —
    токенизатор весит ~16 МБ)."""
    path = Path(path)
    directory = directory or model_dir()
    if path.is_dir():
        found = False
        for asset in ASSETS:
            candidates = [path / asset.name]
            if asset is MODEL_ASSET:
                candidates.append(path / "onnx" / asset.name)
            source = next((c for c in candidates if c.is_file()), None)
            if source is None:
                continue
            found = True
            if not _same_file(source, directory / asset.name):
                _copy_verified(source, asset, directory, on_progress, cancel)
        if not found:
            raise DownloadError(f"В папке {path} нет файлов модели NER")
    else:
        if path.name == TOKENIZER_FILE:
            asset = TOKENIZER_ASSET
        elif path.name == TOKENIZER_CONFIG_FILE:
            asset = TOKENIZER_CONFIG_ASSET
        else:
            asset = MODEL_ASSET
        if not _same_file(path, directory / asset.name):
            _copy_verified(path, asset, directory, on_progress, cancel)
    return missing_assets(directory)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.samefile(b)
    except OSError:
        return False


def remove_model(directory: Optional[Path] = None) -> None:
    shutil.rmtree(directory or model_dir(), ignore_errors=True)


__all__ = [
    "ASSETS", "MODEL_ASSET", "MODEL_BASE_URL", "MODEL_BYTES_FP16", "MODEL_FILE", "MODEL_REVISION",
    "MODEL_SHA256_FP16", "MODEL_URL_FP16", "ModelAsset", "TOKENIZER_ASSET", "TOKENIZER_CONFIG_ASSET",
    "download_model", "import_model_file", "is_model_present", "missing_assets", "model_dir",
    "remove_model", "sha256_file",
]

"""Языковые файлы OCR (rus, ukr, eng), скачиваются по запросу.

Источник тот же, что у браузерной версии: пакеты @tesseract.js-data на jsDelivr,
модели 4.0.0_best_int. Версия, размеры и хеши закреплены, поэтому подмена файла
на сервере не пройдет проверку.
"""

from __future__ import annotations

import gzip
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..cancel import CancelToken, check
from ..convert.ocr import OCR_LANGS
from ..paths import tessdata_dir
from .downloader import DownloadError, download_file, sha256_file


@dataclass(frozen=True)
class LangAsset:
    lang: str
    gz_size: int
    gz_sha256: str
    sha256: str  # распакованного .traineddata

    @property
    def url(self) -> str:
        return (
            f"https://cdn.jsdelivr.net/npm/@tesseract.js-data/{self.lang}@1.0.0/"
            f"4.0.0_best_int/{self.lang}.traineddata.gz"
        )


ASSETS = (
    LangAsset("rus", 2679598, "f51f5edc992249ff9b70a227b22f242dfa47b2b1bbc7ae0ea74908640c101f6a",
              "eb9be824435f6bb0f993925acb85fd842c8418d6db7613c818e749e619a1ad6d"),
    LangAsset("ukr", 2114206, "ea2789a0ad99ff84bfd252a7410a1e8661a7bc5acaa41573d23b54b71446aa4c",
              "c9bceda0ec7f1cd8dc7642ec62221395066b98359fa0e988d5db82ceae727892"),
    LangAsset("eng", 2952873, "45b4cb346724ac1774f1c36f42f182b887bcdb28ebe63e6fff90ac41f3fcff91",
              "5dc5d8d640a212c9d6184921ba103b186f50e0fed9ee716c53e6b312b400d747"),
)
assert tuple(a.lang for a in ASSETS) == OCR_LANGS
TOTAL_BYTES = sum(a.gz_size for a in ASSETS)


def missing_langs(directory: Optional[Path] = None) -> list[LangAsset]:
    directory = directory or tessdata_dir()
    return [a for a in ASSETS if not (directory / f"{a.lang}.traineddata").is_file()]


def is_tessdata_present(directory: Optional[Path] = None) -> bool:
    return not missing_langs(directory)


def download_tessdata(
    on_progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[CancelToken] = None,
    directory: Optional[Path] = None,
) -> Path:
    directory = directory or tessdata_dir()
    directory.mkdir(parents=True, exist_ok=True)
    done = sum(a.gz_size for a in ASSETS if a not in missing_langs(directory))
    for asset in missing_langs(directory):
        base = done

        def report(received: int, _total: Optional[int], base: int = base) -> None:
            if on_progress:
                on_progress(base + received, TOTAL_BYTES)

        gz = directory / f"{asset.lang}.traineddata.gz"
        download_file(asset.url, gz, expected_size=asset.gz_size, sha256=asset.gz_sha256,
                      on_progress=report, cancel=cancel)
        check(cancel)
        target = directory / f"{asset.lang}.traineddata"
        tmp = target.with_name(target.name + ".tmp")
        with gzip.open(gz, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        if sha256_file(tmp) != asset.sha256:
            tmp.unlink(missing_ok=True)
            raise DownloadError(f"Контрольная сумма {target.name} не совпала")
        os.replace(tmp, target)
        gz.unlink(missing_ok=True)
        done += asset.gz_size
    if on_progress:
        on_progress(TOTAL_BYTES, TOTAL_BYTES)
    return directory

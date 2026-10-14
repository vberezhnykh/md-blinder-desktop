"""Докачиваемая HTTP-загрузка больших файлов (модель NER, языки OCR).

Файл пишется в `<dest>.part`; при повторном запуске докачивается заголовком
Range. Размер (и, если задан, SHA-256) проверяется до атомарного переименования,
поэтому по пути `dest` никогда не лежит недокачанный или битый файл.

Сеть: urllib.request берет прокси из HTTPS_PROXY/NO_PROXY, на Windows — еще и из
системных настроек. Если установлен truststore, TLS проверяется по системному
хранилищу сертификатов (корпоративные корневые CA в Windows). Глобально
truststore.inject_into_ssl() не вызывается — контекст строится только здесь.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..cancel import CancelToken, check

ProgressCallback = Callable[[int, Optional[int]], None]

CHUNK_SIZE = 1 << 20
DEFAULT_TIMEOUT = 60.0
USER_AGENT = "md-blinder-desktop"

_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.I)


class DownloadError(Exception):
    """Загрузка не удалась: сеть, HTTP-ошибка, неверный размер или хеш."""


def ssl_context() -> ssl.SSLContext:
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _build_opener() -> urllib.request.OpenerDirector:
    # ProxyHandler добавляется build_opener'ом по умолчанию (getproxies()).
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()))


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def sha256_file(path: Path, cancel: Optional[CancelToken] = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            check(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def download_file(
    url: str,
    dest: Path | str,
    *,
    expected_size: Optional[int] = None,
    sha256: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    cancel: Optional[CancelToken] = None,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Optional[urllib.request.OpenerDirector] = None,
) -> Path:
    """Скачивает url в dest. Отмена (Cancelled) и обрыв сети оставляют .part для докачки;
    неверный размер или хеш удаляют его."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(dest)
    opener = opener or _build_opener()
    check(cancel)

    have = part.stat().st_size if part.exists() else 0
    if expected_size is not None and have > expected_size:
        _unlink(part)
        have = 0

    if expected_size is None or have < expected_size:
        have = _fetch(url, part, have, expected_size, on_progress, cancel, timeout, opener)

    size = part.stat().st_size
    if expected_size is not None and size != expected_size:
        if size > expected_size:
            _unlink(part)
            raise DownloadError(f"Неверный размер файла {dest.name}: {size} вместо {expected_size} байт")
        raise DownloadError(f"Загрузка {dest.name} прервалась до завершения ({size} из {expected_size} байт)")
    if sha256 is not None:
        actual = sha256_file(part, cancel)
        if actual.lower() != sha256.lower():
            _unlink(part)
            raise DownloadError(f"Контрольная сумма {dest.name} не совпала")
    if on_progress:
        on_progress(size, expected_size if expected_size is not None else size)
    os.replace(part, dest)
    return dest


def _fetch(
    url: str,
    part: Path,
    have: int,
    expected_size: Optional[int],
    on_progress: Optional[ProgressCallback],
    cancel: Optional[CancelToken],
    timeout: float,
    opener: urllib.request.OpenerDirector,
) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    if have:
        request.add_header("Range", f"bytes={have}-")
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        if error.code == 416 and have:
            # Диапазон за концом файла: либо .part уже полный (проверит вызывающий),
            # либо файл на сервере изменился — тогда начинаем заново.
            error.close()
            if expected_size is not None and have == expected_size:
                return have
            _unlink(part)
            return _fetch(url, part, 0, expected_size, on_progress, cancel, timeout, opener)
        raise DownloadError(f"Не удалось загрузить {part.name[:-5]}: HTTP {error.code}") from error
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        raise DownloadError(f"Не удалось загрузить {part.name[:-5]}: {getattr(error, 'reason', error)}") from error

    with response:
        status = getattr(response, "status", 200)
        mode = "ab"
        if have and status == 206:
            match = _CONTENT_RANGE_RE.match(response.headers.get("Content-Range", ""))
            if not match or int(match.group(1)) != have:
                raise DownloadError("Сервер вернул неожиданный диапазон при докачке")
        else:
            # Сервер проигнорировал Range (200) — качаем с нуля.
            have = 0
            mode = "wb"
        length = response.headers.get("Content-Length")
        total = expected_size
        if total is None and length is not None and length.isdigit():
            total = have + int(length)
        received = have
        if on_progress:
            on_progress(received, total)
        try:
            with open(part, mode) as f:
                while True:
                    check(cancel)
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if expected_size is not None and received + len(chunk) > expected_size:
                        f.close()
                        _unlink(part)
                        raise DownloadError(
                            f"Неверный размер файла {part.name[:-5]}: больше {expected_size} байт"
                        )
                    f.write(chunk)
                    received += len(chunk)
                    if on_progress:
                        on_progress(received, total)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            raise DownloadError(f"Загрузка {part.name[:-5]} прервалась: {getattr(error, 'reason', error)}") from error
    return received

"""Обновление собранной программы из релизов GitHub.

Проверка — один запрос к API GitHub за списком релизов; документы и история
никуда не отправляются. Архив скачивается с докачкой и проверкой SHA-256,
распаковывается в каталог данных, а замену файлов делает небольшой cmd-скрипт
после выхода программы: работающий exe и его библиотеки Windows перезаписать
не дает. Заменяются только MD Blinder.exe и папка _internal, остальные файлы в
папке программы не трогаются. Модель, языки OCR и история лежат отдельно, в
каталоге данных, и обновлением не затрагиваются.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .cancel import CancelToken, check
from .components.downloader import USER_AGENT, DownloadError, download_file, ssl_context
from .paths import data_dir

REPO = "vberezhnykh/md-blinder"
TAG_PREFIX = "desktop-v"
ASSET_NAME = "md-blinder-windows.zip"
EXE_NAME = "MD Blinder.exe"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    zip_url: str
    size: int
    sha256_url: Optional[str]
    page_url: str
    notes: str


def parse_version(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text)
    return tuple(int(p) for p in parts[:3]) if parts else (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()))


def _get(url: str, timeout: float, opener=None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with (opener or _opener()).open(request, timeout=timeout) as response:
        return response.read()


def pick_release(releases: list[dict]) -> Optional[Release]:
    """Самый новый опубликованный релиз настольной версии с архивом под Windows."""
    best: Optional[Release] = None
    for item in releases:
        tag = str(item.get("tag_name", ""))
        if not tag.startswith(TAG_PREFIX) or item.get("draft") or item.get("prerelease"):
            continue
        assets = {a.get("name"): a for a in item.get("assets", []) if isinstance(a, dict)}
        archive = assets.get(ASSET_NAME)
        if not archive:
            continue
        checksum = assets.get(ASSET_NAME + ".sha256")
        release = Release(
            version=tag[len(TAG_PREFIX):],
            tag=tag,
            zip_url=archive["browser_download_url"],
            size=int(archive.get("size") or 0),
            sha256_url=checksum["browser_download_url"] if checksum else None,
            page_url=str(item.get("html_url", "")),
            notes=str(item.get("body") or ""),
        )
        if best is None or parse_version(release.version) > parse_version(best.version):
            best = release
    return best


def fetch_latest(timeout: float = 10, opener=None) -> Optional[Release]:
    data = json.loads(_get(RELEASES_URL, timeout, opener).decode("utf-8"))
    if not isinstance(data, list):
        return None
    return pick_release(data)


def check_for_update(timeout: float = 10, opener=None) -> Optional[Release]:
    """Релиз новее текущей версии или None."""
    latest = fetch_latest(timeout, opener)
    return latest if latest is not None and is_newer(latest.version) else None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def can_self_update() -> tuple[bool, str]:
    if not is_frozen():
        return False, "Обновление устанавливается только в собранную программу (MD Blinder.exe)."
    if sys.platform != "win32":
        return False, "Автоматическая установка обновлений поддерживается только в Windows."
    target = install_dir()
    if not (target / EXE_NAME).exists() or not (target / "_internal").is_dir():
        return False, f"Не похоже на папку программы: {target}"
    try:
        probe = target / ".md-blinder-write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError:
        return False, f"Нет прав на запись в папку программы {target}. Переместите программу в свою папку."
    return True, ""


def updates_dir() -> Path:
    return data_dir() / "updates"


def _safe_extract(archive: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            dest = (target / member.filename).resolve()
            if root != dest and root not in dest.parents:
                raise DownloadError(f"Недопустимый путь в архиве: {member.filename}")
        zf.extractall(target)


def _find_app_root(extracted: Path) -> Path:
    """Папка с MD Blinder.exe и _internal: в корне архива или на уровень ниже."""
    for candidate in [extracted, *[p for p in extracted.iterdir() if p.is_dir()]]:
        if (candidate / EXE_NAME).is_file() and (candidate / "_internal").is_dir():
            return candidate
    raise DownloadError("В архиве обновления нет MD Blinder.exe и папки _internal")


def prepare(
    release: Release,
    on_progress: Optional[Callable[[int, Optional[int]], None]] = None,
    cancel: Optional[CancelToken] = None,
    opener=None,
) -> Path:
    """Скачивает и распаковывает обновление. Возвращает папку с новой версией."""
    base = updates_dir()
    base.mkdir(parents=True, exist_ok=True)
    sha256 = None
    if release.sha256_url:
        text = _get(release.sha256_url, 30, opener).decode("ascii", "replace")
        match = re.search(r"\b[0-9a-fA-F]{64}\b", text)
        if not match:
            raise DownloadError("Контрольная сумма обновления не найдена")
        sha256 = match.group(0).lower()
    archive = base / f"{release.tag}.zip"
    download_file(
        release.zip_url, archive, expected_size=release.size or None, sha256=sha256,
        on_progress=on_progress, cancel=cancel, opener=opener,
    )
    check(cancel)
    staged = base / release.tag
    shutil.rmtree(staged, ignore_errors=True)
    _safe_extract(archive, staged)
    archive.unlink(missing_ok=True)
    return _find_app_root(staged)


def write_script(staged: Path, target: Path, pid: int) -> Path:
    """cmd-скрипт: дождаться выхода программы, заменить exe и _internal, запустить заново."""
    exe = target / EXE_NAME
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        ":wait",
        f'tasklist /FI "PID eq {pid}" /NH 2>nul | find /I "{EXE_NAME}" >nul',
        # timeout не работает без консоли, поэтому пауза через ping.
        "if not errorlevel 1 (ping -n 2 127.0.0.1 >nul & goto wait)",
        f'robocopy "{staged / "_internal"}" "{target / "_internal"}" /MIR /R:10 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
        "if errorlevel 8 goto fail",
        f'copy /Y "{staged / EXE_NAME}" "{exe}" >nul',
        "if errorlevel 1 goto fail",
        f'start "" "{exe}" --updated',
        "exit /b 0",
        ":fail",
        f'start "" "{exe}" --update-failed',
        "exit /b 1",
    ]
    fd, name = tempfile.mkstemp(prefix="md-blinder-update-", suffix=".cmd")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return Path(name)


def launch_script(script: Path) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(  # noqa: S603 — скрипт создан программой выше
        ["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def cleanup_old_updates() -> None:
    """Удаляет распакованные обновления, оставшиеся от прошлых запусков."""
    shutil.rmtree(updates_dir(), ignore_errors=True)

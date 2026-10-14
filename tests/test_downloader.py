"""Докачиваемая загрузка: локальный http.server в потоке, без внешней сети."""

from __future__ import annotations

import hashlib
import os
import re
import ssl
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator, Optional

import pytest

from md_blinder.cancel import CancelToken, Cancelled
from md_blinder.components import downloader
from md_blinder.components.downloader import DownloadError, download_file, part_path

PAYLOAD = os.urandom(10_000)


class FileServer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[tuple[str, Optional[str]]] = []
        self.ignore_range = False
        self.server: Optional[ThreadingHTTPServer] = None

    def url(self, path: str) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"


@contextmanager
def serve(files: dict[str, bytes]) -> Iterator[FileServer]:
    state = FileServer()
    state.files.update(files)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            range_header = self.headers.get("Range")
            state.requests.append((self.path, range_header))
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", self.path[len("/redirect"):])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = state.files.get(self.path)
            if data is None:
                self.send_error(404)
                return
            match = re.fullmatch(r"bytes=(\d+)-", range_header or "")
            if match and not state.ignore_range:
                start = int(match.group(1))
                if start >= len(data):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = data[start:]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            else:
                body = data
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.server = server
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def no_proxy_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader, "CHUNK_SIZE", 1024)
    # Локальный сервер — мимо прокси окружения.
    monkeypatch.setattr(downloader, "_build_opener", no_proxy_opener)


def test_downloads_with_progress_and_atomic_rename(tmp_path: Path) -> None:
    dest = tmp_path / "sub" / "file.bin"
    progress: list[tuple[int, Optional[int]]] = []
    with serve({"/f": PAYLOAD}) as srv:
        download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD),
                      sha256=hashlib.sha256(PAYLOAD).hexdigest(), on_progress=lambda r, t: progress.append((r, t)))
    assert dest.read_bytes() == PAYLOAD
    assert not part_path(dest).exists()
    assert progress[0] == (0, len(PAYLOAD))
    assert progress[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert all(a[0] <= b[0] for a, b in zip(progress, progress[1:]))


def test_unknown_size_uses_content_length_and_follows_redirects(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    progress: list[tuple[int, Optional[int]]] = []
    with serve({"/f": PAYLOAD}) as srv:
        download_file(srv.url("/redirect/f"), dest, on_progress=lambda r, t: progress.append((r, t)))
    assert dest.read_bytes() == PAYLOAD
    assert progress[-1] == (len(PAYLOAD), len(PAYLOAD))


def test_resumes_partial_download_with_range(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part_path(dest).write_bytes(PAYLOAD[:4000])
    progress: list[int] = []
    with serve({"/f": PAYLOAD}) as srv:
        download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD), on_progress=lambda r, t: progress.append(r))
        assert srv.requests == [("/f", "bytes=4000-")]
    assert dest.read_bytes() == PAYLOAD
    assert progress[0] == 4000


def test_resume_keeps_range_header_across_redirect(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part_path(dest).write_bytes(PAYLOAD[:100])
    with serve({"/f": PAYLOAD}) as srv:
        download_file(srv.url("/redirect/f"), dest, expected_size=len(PAYLOAD))
        assert srv.requests == [("/redirect/f", "bytes=100-"), ("/f", "bytes=100-")]
    assert dest.read_bytes() == PAYLOAD


def test_restarts_when_server_ignores_range(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part_path(dest).write_bytes(b"x" * 4000)  # даже мусорный префикс перезаписывается
    with serve({"/f": PAYLOAD}) as srv:
        srv.ignore_range = True
        download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD))
    assert dest.read_bytes() == PAYLOAD


def test_complete_part_file_is_finalized_without_body(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part_path(dest).write_bytes(PAYLOAD)
    with serve({"/f": PAYLOAD}) as srv:
        download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD))
        assert srv.requests == []
    assert dest.read_bytes() == PAYLOAD


def test_416_restarts_from_scratch(tmp_path: Path) -> None:
    # Диапазон за концом файла, а размер не совпадает с ожидаемым (или неизвестен):
    # файл на сервере изменился — .part выбрасывается, загрузка идет с нуля.
    dest = tmp_path / "file.bin"
    part_path(dest).write_bytes(PAYLOAD)
    with serve({"/f": PAYLOAD}) as srv:
        download_file(srv.url("/f"), dest)
        assert srv.requests == [("/f", f"bytes={len(PAYLOAD)}-"), ("/f", None)]
    assert dest.read_bytes() == PAYLOAD

    dest2 = tmp_path / "file2.bin"
    part_path(dest2).write_bytes(PAYLOAD)
    with serve({"/f": PAYLOAD}) as srv:
        with pytest.raises(DownloadError, match="прервалась"):
            download_file(srv.url("/f"), dest2, expected_size=len(PAYLOAD) + 1)
    assert not dest2.exists()


def test_oversized_part_is_discarded(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    part_path(dest).write_bytes(PAYLOAD + b"extra")
    with serve({"/f": PAYLOAD}) as srv:
        download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD))
        assert srv.requests == [("/f", None)]
    assert dest.read_bytes() == PAYLOAD


def test_truncated_download_keeps_part_for_resume(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    with serve({"/f": PAYLOAD}) as srv:
        with pytest.raises(DownloadError, match="прервалась"):
            download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD) + 10)
    assert not dest.exists()
    assert part_path(dest).read_bytes() == PAYLOAD


def test_oversized_download_is_rejected_and_removed(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    with serve({"/f": PAYLOAD}) as srv:
        with pytest.raises(DownloadError, match="Неверный размер"):
            download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD) - 1)
    assert not dest.exists()
    assert not part_path(dest).exists()


def test_sha256_mismatch_removes_part(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    with serve({"/f": PAYLOAD}) as srv:
        with pytest.raises(DownloadError, match="Контрольная сумма"):
            download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD), sha256="0" * 64)
    assert not dest.exists()
    assert not part_path(dest).exists()


def test_http_error_is_reported(tmp_path: Path) -> None:
    with serve({}) as srv:
        with pytest.raises(DownloadError, match="HTTP 404"):
            download_file(srv.url("/missing"), tmp_path / "file.bin")


def test_cancel_keeps_part_and_resume_completes(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    token = CancelToken()

    def on_progress(received: int, _total: Optional[int]) -> None:
        if received >= 2048:
            token.cancel()

    with serve({"/f": PAYLOAD}) as srv:
        with pytest.raises(Cancelled):
            download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD), on_progress=on_progress, cancel=token)
        assert not dest.exists()
        kept = part_path(dest).stat().st_size
        assert 2048 <= kept < len(PAYLOAD)
        assert part_path(dest).read_bytes() == PAYLOAD[:kept]
        download_file(srv.url("/f"), dest, expected_size=len(PAYLOAD))
        assert srv.requests[-1] == ("/f", f"bytes={kept}-")
    assert dest.read_bytes() == PAYLOAD


def test_cancelled_before_start_makes_no_request(tmp_path: Path) -> None:
    token = CancelToken()
    token.cancel()
    with serve({"/f": PAYLOAD}) as srv:
        with pytest.raises(Cancelled):
            download_file(srv.url("/f"), tmp_path / "file.bin", cancel=token)
        assert srv.requests == []


def test_ssl_context_verifies_certificates() -> None:
    context = downloader.ssl_context()
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname

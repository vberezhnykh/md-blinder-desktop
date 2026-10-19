"""Проверка и подготовка обновления без сети: релизы и архив отдает локальный сервер."""

import hashlib
import http.server
import json
import threading
import zipfile
from pathlib import Path

import pytest

from md_blinder import __version__, updater


def release_json(tag, base_url, size, with_sha=True, draft=False, prerelease=False):
    assets = [{"name": "md-blinder-windows.zip", "browser_download_url": f"{base_url}/app.zip", "size": size}]
    if with_sha:
        assets.append({"name": "md-blinder-windows.zip.sha256", "browser_download_url": f"{base_url}/app.zip.sha256", "size": 64})
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets,
            "html_url": f"{base_url}/release/{tag}", "body": "notes"}


def test_versions():
    assert updater.parse_version("0.10.2") == (0, 10, 2)
    assert updater.is_newer("0.10.0", "0.9.9")
    assert not updater.is_newer("0.2.0", "0.2.0")
    assert not updater.is_newer("0.1.9", "0.2.0")


def test_pick_release_skips_drafts_web_tags_and_missing_assets():
    base = "https://example.test"
    releases = [
        release_json("desktop-v9.0.0", base, 1, draft=True),
        release_json("desktop-v8.0.0", base, 1, prerelease=True),
        release_json("web-v99", base, 1),
        {"tag_name": "desktop-v7.0.0", "assets": []},
        release_json("desktop-v0.3.0", base, 10),
        release_json("desktop-v0.4.1", base, 10),
    ]
    picked = updater.pick_release(releases)
    assert picked.version == "0.4.1" and picked.zip_url == f"{base}/app.zip"
    assert picked.sha256_url == f"{base}/app.zip.sha256"


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def make_app_zip(path: Path, nested: bool = False) -> bytes:
    prefix = "MD Blinder/" if nested else ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(prefix + "MD Blinder.exe", b"exe")
        zf.writestr(prefix + "_internal/lib.dll", b"dll")
    return path.read_bytes()


def test_check_and_prepare(server, tmp_path, monkeypatch):
    root, base = server
    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path / "data"))
    data = make_app_zip(root / "app.zip", nested=True)
    (root / "app.zip.sha256").write_text(hashlib.sha256(data).hexdigest() + "  md-blinder-windows.zip\n")
    newer = ".".join(str(n + (1 if i == 0 else 0)) for i, n in enumerate(updater.parse_version(__version__)))
    (root / "releases").write_text(json.dumps([release_json(f"desktop-v{newer}", base, len(data))]))
    monkeypatch.setattr(updater, "RELEASES_URL", f"{base}/releases")
    release = updater.check_for_update(timeout=5)
    assert release is not None and release.version == newer
    staged = updater.prepare(release)
    assert (staged / "MD Blinder.exe").read_bytes() == b"exe"
    assert (staged / "_internal" / "lib.dll").exists()


def test_prepare_rejects_bad_checksum(server, tmp_path, monkeypatch):
    root, base = server
    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path / "data"))
    data = make_app_zip(root / "app.zip")
    (root / "app.zip.sha256").write_text("0" * 64)
    release = updater.pick_release([release_json("desktop-v99.0.0", base, len(data))])
    with pytest.raises(updater.DownloadError):
        updater.prepare(release)


def test_zip_slip_rejected(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", b"x")
    with pytest.raises(updater.DownloadError):
        updater._safe_extract(bad, tmp_path / "out")


def test_not_frozen_cannot_self_update():
    ok, reason = updater.can_self_update()
    assert not ok and "собранную" in reason


def test_script_replaces_only_exe_and_internal(tmp_path):
    script = updater.write_script(tmp_path / "new", tmp_path / "app", 4242)
    text = script.read_text(encoding="utf-8")
    assert '"PID eq 4242"' in text
    assert f'robocopy "{tmp_path / "new" / "_internal"}" "{tmp_path / "app" / "_internal"}" /MIR' in text
    assert f'copy /Y "{tmp_path / "new" / "MD Blinder.exe"}"' in text
    assert "--updated" in text and "--update-failed" in text
    # exe заменяется раньше _internal: новые библиотеки со старым exe не запускаются.
    assert text.index("copy /Y") < text.index("robocopy")
    assert "goto copy_exe" in text
    # Без конвейера: find, получивший свою консоль, ждал ввода, и обновление висело.
    assert "|" not in text
    script.unlink()


def test_script_runs_in_hidden_console(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updater.sys, "platform", "win32")
    for name, value in (("CREATE_NEW_PROCESS_GROUP", 0x200), ("CREATE_NO_WINDOW", 0x8000000), ("DETACHED_PROCESS", 0x8)):
        monkeypatch.setattr(updater.subprocess, name, value, raising=False)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *args, **kwargs: calls.append(kwargs))
    updater.launch_script(tmp_path / "update.cmd")
    assert calls[0]["creationflags"] == 0x200 | 0x8000000

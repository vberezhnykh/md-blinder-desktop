"""Порт применимой части src/workers/modelAssets.test.ts: закрепленная ревизия,
проверка размера и хеша, докачка недостающих файлов, импорт файла модели."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from md_blinder import paths
from md_blinder.cancel import CancelToken, Cancelled
from md_blinder.components import downloader, model_asset
from md_blinder.components.downloader import DownloadError, part_path
from md_blinder.components.model_asset import ModelAsset
from test_downloader import no_proxy_opener, serve

REVISION = "6ddaeb9413b0e71ad8457da1aab378a165b24058"


def test_pinned_revision_and_urls() -> None:
    base = f"https://huggingface.co/onnx-community/gliner_multi-v2.1/resolve/{REVISION}"
    assert model_asset.MODEL_REVISION == REVISION
    assert model_asset.MODEL_URL_FP16 == f"{base}/onnx/model_fp16.onnx"
    assert model_asset.MODEL_BYTES_FP16 == 579_717_643
    assert model_asset.TOKENIZER_ASSET.url == f"{base}/tokenizer.json"
    assert model_asset.TOKENIZER_CONFIG_ASSET.url == f"{base}/tokenizer_config.json"
    assert [a.name for a in model_asset.ASSETS] == ["tokenizer_config.json", "tokenizer.json", "model_fp16.onnx"]


def test_model_dir_under_data_dir_and_pinned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path
    assert paths.models_dir() == tmp_path / "models"
    assert paths.history_db_path() == tmp_path / "history.sqlite"
    assert model_asset.model_dir().parent == tmp_path / "models"
    assert REVISION in model_asset.model_dir().name


def test_default_data_dir_without_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("MD_BLINDER_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths.data_dir() == tmp_path / "Local" / "md-blinder"
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert paths.data_dir() == Path.home() / ".local" / "share" / "md-blinder"


def fixture_asset(name: str, data: bytes, url: str = "") -> ModelAsset:
    return ModelAsset(name, url, len(data), hashlib.sha256(data).hexdigest())


FILES = {
    "tokenizer_config.json": b'{"sep_token": "[SEP]"}',
    "tokenizer.json": b'{"model": {}}' * 50,
    "model_fp16.onnx": bytes(range(256)) * 40,
}


@pytest.fixture
def fake_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, ModelAsset]:
    """Подменяет закрепленные файлы маленькими фикстурами на локальном сервере."""
    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(downloader, "_build_opener", no_proxy_opener)
    assets = {name: fixture_asset(name, data) for name, data in FILES.items()}
    monkeypatch.setattr(model_asset, "TOKENIZER_CONFIG_ASSET", assets["tokenizer_config.json"])
    monkeypatch.setattr(model_asset, "TOKENIZER_ASSET", assets["tokenizer.json"])
    monkeypatch.setattr(model_asset, "MODEL_ASSET", assets["model_fp16.onnx"])
    monkeypatch.setattr(model_asset, "ASSETS", tuple(assets.values()))
    monkeypatch.setattr(model_asset, "TOTAL_BYTES", sum(a.size for a in assets.values()))
    return assets


def with_urls(monkeypatch: pytest.MonkeyPatch, assets: dict[str, ModelAsset], base: str) -> None:
    updated = {n: ModelAsset(a.name, f"{base}/{n}", a.size, a.sha256) for n, a in assets.items()}
    monkeypatch.setattr(model_asset, "TOKENIZER_CONFIG_ASSET", updated["tokenizer_config.json"])
    monkeypatch.setattr(model_asset, "TOKENIZER_ASSET", updated["tokenizer.json"])
    monkeypatch.setattr(model_asset, "MODEL_ASSET", updated["model_fp16.onnx"])
    monkeypatch.setattr(model_asset, "ASSETS", tuple(updated.values()))


def test_download_model_fetches_missing_files_with_total_progress(
    fake_assets: dict[str, ModelAsset], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not model_asset.is_model_present()
    with serve({f"/{n}": d for n, d in FILES.items()}) as srv:
        with_urls(monkeypatch, fake_assets, srv.url(""))
        # Токенизатор уже лежит — качаются только недостающие файлы.
        target = model_asset.model_dir()
        target.mkdir(parents=True)
        (target / "tokenizer.json").write_bytes(FILES["tokenizer.json"])
        progress: list[tuple[int, int]] = []
        model_asset.download_model(lambda r, t: progress.append((r, t)))
        assert sorted(p for p, _ in srv.requests) == ["/model_fp16.onnx", "/tokenizer_config.json"]
    assert model_asset.is_model_present()
    total = sum(len(d) for d in FILES.values())
    assert progress[-1] == (total, total)
    assert all(t == total for _, t in progress)
    assert all(a[0] <= b[0] for a, b in zip(progress, progress[1:]))
    for name, data in FILES.items():
        assert (target / name).read_bytes() == data


def test_wrong_size_file_is_not_present(fake_assets: dict[str, ModelAsset]) -> None:
    target = model_asset.model_dir()
    target.mkdir(parents=True)
    for name, data in FILES.items():
        (target / name).write_bytes(data)
    assert model_asset.is_model_present()
    (target / "model_fp16.onnx").write_bytes(b"short")
    assert [a.name for a in model_asset.missing_assets()] == ["model_fp16.onnx"]


def test_download_model_rejects_corrupted_data(fake_assets: dict[str, ModelAsset], monkeypatch: pytest.MonkeyPatch) -> None:
    corrupted = dict(FILES, **{"model_fp16.onnx": b"\0" * len(FILES["model_fp16.onnx"])})
    with serve({f"/{n}": d for n, d in corrupted.items()}) as srv:
        with_urls(monkeypatch, fake_assets, srv.url(""))
        with pytest.raises(DownloadError, match="Контрольная сумма"):
            model_asset.download_model()
    assert [a.name for a in model_asset.missing_assets()] == ["model_fp16.onnx"]
    assert not part_path(model_asset.model_dir() / "model_fp16.onnx").exists()


def test_download_model_cancel(fake_assets: dict[str, ModelAsset], monkeypatch: pytest.MonkeyPatch) -> None:
    token = CancelToken()
    token.cancel()
    with serve({f"/{n}": d for n, d in FILES.items()}) as srv:
        with_urls(monkeypatch, fake_assets, srv.url(""))
        with pytest.raises(Cancelled):
            model_asset.download_model(cancel=token)
    assert not model_asset.is_model_present()


def test_import_model_file_validates_size_and_hash(fake_assets: dict[str, ModelAsset], tmp_path: Path) -> None:
    good = tmp_path / "picked" / "model_fp16.onnx"
    good.parent.mkdir()
    bad_size = tmp_path / "bad.onnx"
    bad_size.write_bytes(b"too short")
    with pytest.raises(DownloadError, match="не подходит"):
        model_asset.import_model_file(bad_size)
    bad_hash = tmp_path / "other.onnx"
    bad_hash.write_bytes(b"\1" * len(FILES["model_fp16.onnx"]))
    with pytest.raises(DownloadError, match="поврежден"):
        model_asset.import_model_file(bad_hash)
    assert not (model_asset.model_dir() / "model_fp16.onnx").exists()
    assert not part_path(model_asset.model_dir() / "model_fp16.onnx").exists()

    good.write_bytes(FILES["model_fp16.onnx"])
    missing = model_asset.import_model_file(good)
    assert (model_asset.model_dir() / "model_fp16.onnx").read_bytes() == FILES["model_fp16.onnx"]
    # Токенизатор еще нужно докачать.
    assert [a.name for a in missing] == ["tokenizer_config.json", "tokenizer.json"]


def test_import_model_folder(fake_assets: dict[str, ModelAsset], tmp_path: Path) -> None:
    folder = tmp_path / "hf-snapshot"
    (folder / "onnx").mkdir(parents=True)
    (folder / "onnx" / "model_fp16.onnx").write_bytes(FILES["model_fp16.onnx"])
    (folder / "tokenizer.json").write_bytes(FILES["tokenizer.json"])
    (folder / "tokenizer_config.json").write_bytes(FILES["tokenizer_config.json"])
    assert model_asset.import_model_file(folder) == []
    assert model_asset.is_model_present()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DownloadError):
        model_asset.import_model_file(empty)

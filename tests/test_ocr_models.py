"""Закрепленные модели Indra OCR."""

from pathlib import Path

import pytest

from md_blinder import paths
from md_blinder.components import ocr_models


def test_assets_pinned_to_revision() -> None:
    assert all(f"/resolve/{ocr_models.OCR_REVISION}/" in asset.url for asset in ocr_models.ASSETS)
    assert {asset.name for asset in ocr_models.ASSETS} == {
        "meta.json", "det.onnx", "orient.onnx", "order.onnx", "rec.onnx", "lm.npz", "LICENSE",
    }
    assert all(len(asset.sha256) == 64 for asset in ocr_models.ASSETS)
    assert 20 <= ocr_models.TOTAL_MB <= 30


def test_models_dir_and_presence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MD_BLINDER_DATA_DIR", str(tmp_path))
    directory = ocr_models.ocr_models_dir()
    assert directory.parent == paths.models_dir()
    assert ocr_models.OCR_REVISION in directory.name
    assert not ocr_models.is_ocr_present()
    directory.mkdir(parents=True)
    for asset in ocr_models.ASSETS:
        (directory / asset.name).write_bytes(b"\0" * (asset.size if asset.name != "lm.npz" else 10))
    assert [a.name for a in ocr_models.missing_assets()] == ["lm.npz"]

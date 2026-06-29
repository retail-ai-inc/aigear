from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aigear.common.run_log_context import RunLogContext
from aigear.db.bucket import LocalGCSMock
from aigear.management.registry import AssetRecord, AssetRef, FakeAssetRegistry
from aigear.management.versioned_asset import VersionedAssetManagement


class FakeBucketClient:
    def __init__(self):
        self.uploads: list[tuple[Path, str]] = []
        self.downloads: list[tuple[str, Path]] = []

    def upload(self, local_blob_name, bucket_blob_name):
        self.uploads.append((Path(local_blob_name), bucket_blob_name))

    def download(self, bucket_blob_name, local_blob_path):
        local_blob_path = Path(local_blob_path)
        local_blob_path.parent.mkdir(parents=True, exist_ok=True)
        local_blob_path.write_text(f"downloaded:{bucket_blob_name}", encoding="utf-8")
        self.downloads.append((bucket_blob_name, local_blob_path))


def _manager(
    tmp_path: Path,
    registry: FakeAssetRegistry | None = None,
    bucket: FakeBucketClient | None = None,
) -> VersionedAssetManagement:
    return VersionedAssetManagement(
        pipeline_version="logistic_regression",
        project_name="aigear_sklearn_pipeline",
        bucket_name="asset-bucket",
        registry=registry or FakeAssetRegistry(),
        bucket=bucket or FakeBucketClient(),
        local_asset_path=tmp_path / "asset",
        clock=lambda: datetime(2026, 6, 29, 1, 2, 3, tzinfo=timezone.utc),
    )


def test_get_run_asset_path_uses_unified_layout(tmp_path):
    manager = _manager(tmp_path)

    assert manager.get_run_asset_path(
        "run-1", "model", "logistic_regression", "model.pkl"
    ) == (
        "_aigear_runs/aigear_sklearn_pipeline/logistic_regression/"
        "run-1/model/logistic_regression/model.pkl"
    )


def test_get_local_path_creates_versioned_local_directory(tmp_path):
    manager = _manager(tmp_path)

    local_path = manager.get_local_path("model", "logistic_regression", "model.pkl")

    assert local_path == (
        tmp_path
        / "asset"
        / "logistic_regression"
        / "model"
        / "logistic_regression"
        / "model.pkl"
    )
    assert local_path.parent.exists()


def test_upload_version_resolves_file_from_managed_local_path(tmp_path, monkeypatch):
    registry = FakeAssetRegistry()
    bucket = FakeBucketClient()
    monkeypatch.setattr(RunLogContext, "current", classmethod(lambda cls: None))
    manager = _manager(tmp_path, registry=registry, bucket=bucket)
    local_path = manager.get_local_path("model", "logistic_regression", "model.pkl")
    local_path.write_text("model", encoding="utf-8")

    record = manager.upload_version("model.pkl", asset_type="model")

    assert record.file_name == "model.pkl"
    assert bucket.uploads[0][0] == local_path


def test_local_gcs_mock_is_created_under_local_asset_path(tmp_path):
    manager = VersionedAssetManagement(
        pipeline_version="logistic_regression",
        project_name="aigear_sklearn_pipeline",
        project_id="project-id",
        bucket_name="local-bucket",
        bucket_on=False,
        registry=FakeAssetRegistry(),
        local_asset_path=tmp_path / "asset",
        clock=lambda: datetime(2026, 6, 29, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert isinstance(manager.bucket_client, LocalGCSMock)
    assert manager.bucket_client.bucket_path == tmp_path / "asset" / "local-bucket"


def test_upload_version_uses_run_context_and_registers_auto_record(tmp_path, monkeypatch):
    registry = FakeAssetRegistry()
    bucket = FakeBucketClient()
    local_file = tmp_path / "model.pkl"
    local_file.write_bytes(b"model-bytes")
    ctx = RunLogContext(
        run_id="run-123",
        run_started_at_utc="2026-06-29T00:00:00Z",
        pipeline_version="logistic_regression",
        step_name="training",
        project_name="aigear_sklearn_pipeline",
    )
    monkeypatch.setattr(RunLogContext, "current", classmethod(lambda cls: ctx))
    manager = _manager(tmp_path, registry=registry, bucket=bucket)

    record = manager.upload_version(
        str(local_file),
        asset_type="model",
        metadata={"accuracy": 0.94},
    )

    expected_version = hashlib.sha256(b"model-bytes").hexdigest()[:16]
    assert record.version == expected_version
    assert record.asset_type == "model"
    assert record.name == "logistic_regression"
    assert record.created_by == "auto"
    assert record.run_id == "run-123"
    assert record.step_name == "training"
    assert record.metadata == {"accuracy": 0.94}
    assert record.uri == (
        "gs://asset-bucket/_aigear_runs/aigear_sklearn_pipeline/"
        "logistic_regression/run-123/model/logistic_regression/model.pkl"
    )
    assert bucket.uploads == [
        (
            local_file,
            (
                "_aigear_runs/aigear_sklearn_pipeline/logistic_regression/"
                "run-123/model/logistic_regression/model.pkl"
            ),
        )
    ]
    assert registry.runs["run-123"]["triggered_by"] == "scheduler"


def test_upload_version_without_context_registers_manual_record(tmp_path, monkeypatch):
    registry = FakeAssetRegistry()
    local_file = tmp_path / "dataset.pkl"
    local_file.write_text("dataset", encoding="utf-8")
    monkeypatch.setattr(RunLogContext, "current", classmethod(lambda cls: None))
    manager = _manager(tmp_path, registry=registry)

    record = manager.upload_version(
        str(local_file),
        asset_type="dataset",
        asset_name="breast_cancer",
    )

    assert record.created_by == "manual"
    assert record.run_id == "manual-20260629010203"
    assert registry.runs[record.run_id]["triggered_by"] == "manual"


def test_register_external_does_not_upload_file(tmp_path, monkeypatch):
    registry = FakeAssetRegistry()
    bucket = FakeBucketClient()
    monkeypatch.setattr(RunLogContext, "current", classmethod(lambda cls: None))
    manager = _manager(tmp_path, registry=registry, bucket=bucket)

    record = manager.register_external(
        asset_type="dataset",
        asset_name="raw_data",
        uri="gs://external-bucket/raw.csv",
        metadata={"source_type": "gcs"},
    )

    assert record.version == hashlib.sha256(b"gs://external-bucket/raw.csv").hexdigest()[:16]
    assert record.created_by == "manual"
    assert record.metadata == {"source_type": "gcs"}
    assert bucket.uploads == []


def test_download_version_uses_latest_record_and_downloads_blob(tmp_path):
    registry = FakeAssetRegistry()
    bucket = FakeBucketClient()
    record = registry.register(
        AssetRecord(
            asset_type="model",
            name="logistic_regression",
            version="model-v1",
            uri=(
                "gs://asset-bucket/_aigear_runs/aigear_sklearn_pipeline/"
                "logistic_regression/run-1/model/logistic_regression/model.pkl"
            ),
            file_name="model.pkl",
            created_at_utc="2026-06-29T00:00:00Z",
        )
    )
    manager = _manager(tmp_path, registry=registry, bucket=bucket)

    local_path = manager.download_version("model")

    assert local_path == (
        tmp_path
        / "asset"
        / "logistic_regression"
        / "model"
        / "logistic_regression"
        / "model.pkl"
    )
    assert local_path.read_text(encoding="utf-8").startswith("downloaded:")
    assert bucket.downloads == [
        (
            (
                "_aigear_runs/aigear_sklearn_pipeline/logistic_regression/"
                "run-1/model/logistic_regression/model.pkl"
            ),
            local_path,
        )
    ]
    assert record == registry.latest("model", "logistic_regression")


def test_upload_version_rejects_training_asset_type(tmp_path):
    local_file = tmp_path / "model.pkl"
    local_file.write_text("model", encoding="utf-8")
    manager = _manager(tmp_path)

    with pytest.raises(ValueError, match='asset_type="model"'):
        manager.upload_version(str(local_file), asset_type="training")  # type: ignore[arg-type]


def test_upload_version_accepts_input_refs(tmp_path, monkeypatch):
    registry = FakeAssetRegistry()
    local_file = tmp_path / "features.pkl"
    local_file.write_text("features", encoding="utf-8")
    dataset_ref = AssetRef("dataset", "breast_cancer", "dataset-v1")
    monkeypatch.setattr(RunLogContext, "current", classmethod(lambda cls: None))
    manager = _manager(tmp_path, registry=registry)

    record = manager.upload_version(
        str(local_file),
        asset_type="feature",
        asset_name="training_features",
        inputs=[dataset_ref],
    )

    assert record.inputs == [dataset_ref]

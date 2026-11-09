from pathlib import Path
from unittest.mock import Mock

import pytest

from aigear.db import bucket
from aigear.management import asset
from aigear.common.asset_runtime import workflow_step
from aigear.management.asset_errors import ExecutionConflict
from types import SimpleNamespace


def local_step(name):
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id=None,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='local-bucket'))))
    return workflow_step('v1', name, config)


def test_local_asset_roundtrip_and_blob_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = asset.AssetManagement("v1", "training", bucket_name="local-bucket", bucket_on=False)
    path = manager.get_local_path("model.bin")
    assert path == tmp_path / "asset/v1/training/model.bin"
    assert manager.get_bucket_blob("model.bin") == "running/v1/training/model.bin"
    path.write_bytes(b"model contents")
    with local_step('produce'):
        manager.upload("model.bin")
    assert manager.bucket_client.bucket_path == tmp_path / "asset/buckets/local-bucket"
    assert (manager.bucket_client.bucket_path / "running/v1/training/model.bin").read_bytes() == b"model contents"
    assert manager.bucket_client.read_bytes("running/v1/training/model.bin") == b"model contents"
    path.unlink()
    assert manager.download("model.bin").read_bytes() == b"model contents"
    with local_step('copy'):
        manager.copy_blob("model.bin", "copy.bin")
    assert manager.download("copy.bin").read_bytes() == b"model contents"


def test_remote_asset_selects_real_client_without_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    constructor = Mock()
    monkeypatch.setattr(asset, "RealGCS", constructor)
    manager = asset.AssetManagement("v1", "dataset", project_id="project", bucket_name="remote")
    constructor.assert_called_once_with("project", "remote")
    with pytest.raises(ExecutionConflict, match='execution entry point'):
        manager.upload("data.bin")
    constructor.return_value.upload.assert_not_called()


def test_local_missing_source_raises(tmp_path):
    client = bucket.LocalGCSMock("project", tmp_path / "bucket")
    with pytest.raises(FileNotFoundError):
        client.download("missing.bin", tmp_path / "output.bin")


@pytest.mark.parametrize("name", ["nested/bucket", Path("nested/bucket"), None, ""])
def test_local_bucket_accepts_string_and_default_name(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    client = bucket.bucket_client(bucket_name=name, bucket_on=False)
    assert isinstance(client.bucket_path, Path)
    assert client.bucket_path == Path(name or "gcs_mock")
    assert client.bucket_path.is_dir()
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")
    client.upload(source, "models/model.bin")
    target = tmp_path / "downloads/model.bin"
    client.download("models/model.bin", str(target))
    assert target.read_bytes() == b"data"


@pytest.mark.parametrize("name", [None, ""])
def test_local_asset_requires_bucket_name(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    constructor = Mock()
    monkeypatch.setattr(asset, "LocalGCSMock", constructor)
    with pytest.raises(ValueError, match="bucket_name.*bucket_on=False"):
        asset.AssetManagement("v1", "training", bucket_name=name, bucket_on=False)
    constructor.assert_not_called()
    assert not (tmp_path / "asset").exists()


def test_local_copy_creates_nested_destination(tmp_path):
    client = bucket.LocalGCSMock("project", tmp_path / "bucket")
    client.write_bytes("source.bin", b"data")
    client.copy_blob("source.bin", "nested/models/copy.bin")
    assert client.read_bytes("nested/models/copy.bin") == b"data"
    client.copy_blob("source.bin", "nested/models/copy.bin")
    assert client.read_bytes("nested/models/copy.bin") == b"data"


def test_real_gcs_download_upload_and_copy_use_expected_blob_names(tmp_path, monkeypatch):
    storage_client = Mock()
    monkeypatch.setattr(bucket.storage, "Client", Mock(return_value=storage_client))
    client = bucket.RealGCS("project", "models")
    remote = storage_client.get_bucket.return_value
    blob = remote.blob.return_value
    blob.generation = 1
    blob.metageneration = 1
    blob.size = 4
    blob.crc32c = "rth90Q=="
    blob.custom_time = None
    blob.metadata = {}
    blob.download_as_bytes.return_value = b"data"
    blob.rewrite.return_value = (None, 4, 4)
    target = tmp_path / "nested/model.bin"
    client.download("v1/model.bin", str(target))
    assert target.parent.is_dir()
    assert target.read_bytes() == b"data"
    blob.download_as_bytes.assert_called_once_with(if_generation_match=1, checksum="crc32c")
    client.upload(target, "v1/model.bin")
    assert blob.cache_control == "no-cache"
    blob.upload_from_string.assert_called_once_with(
        b"data", content_type="application/octet-stream", if_generation_match=None, checksum="crc32c")
    client.copy_blob("v1/model.bin", "v2/model.bin")
    blob.rewrite.assert_called_once_with(
        blob, token=None, if_generation_match=1,
        if_source_generation_match=1, if_source_metageneration_match=1)


def test_real_gcs_missing_copy_source_is_not_copied(monkeypatch):
    storage_client = Mock()
    monkeypatch.setattr(bucket.storage, "Client", Mock(return_value=storage_client))
    client = bucket.RealGCS("project", "models")
    remote = storage_client.get_bucket.return_value
    from google.api_core.exceptions import NotFound
    remote.blob.return_value.reload.side_effect = NotFound("missing")
    with pytest.raises(bucket.ObjectNotFound):
        client.copy_blob("missing", "target")
    remote.blob.return_value.rewrite.assert_not_called()

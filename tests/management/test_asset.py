from pathlib import Path
from unittest.mock import Mock

import pytest

from aigear.db import bucket
from aigear.management import asset


def test_local_asset_roundtrip_and_blob_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = asset.AssetManagement("v1", "training", bucket_name="local-bucket", bucket_on=False)
    path = manager.get_local_path("model.bin")
    assert path == tmp_path / "asset/v1/training/model.bin"
    assert manager.get_bucket_blob("model.bin") == "v1/training/model.bin"
    path.write_bytes(b"model contents")
    manager.upload("model.bin")
    assert (tmp_path / "asset/local-bucket/v1/training/model.bin").read_bytes() == b"model contents"
    path.unlink()
    assert manager.download("model.bin").read_bytes() == b"model contents"
    manager.copy_blob("model.bin", "copy.bin")
    assert manager.download("copy.bin").read_bytes() == b"model contents"


def test_remote_asset_selects_real_client_without_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    constructor = Mock()
    monkeypatch.setattr(asset, "RealGCS", constructor)
    manager = asset.AssetManagement("v1", "dataset", project_id="project", bucket_name="remote")
    constructor.assert_called_once_with("project", "remote")
    manager.upload("data.bin")
    constructor.return_value.upload.assert_called_once_with(tmp_path / "asset/v1/dataset/data.bin", "v1/dataset/data.bin")


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


@pytest.mark.xfail(strict=True, reason="Known defect: local AssetManagement cannot combine its default bucket_name=None with Path")
def test_local_asset_has_usable_default_bucket(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = asset.AssetManagement("v1", "training", bucket_on=False)
    path = manager.get_local_path("model.bin")
    path.write_bytes(b"model")
    manager.upload("model.bin")
    assert manager.download("model.bin").read_bytes() == b"model"


@pytest.mark.xfail(strict=True, reason="Known defect: local copy_blob does not create the destination parent")
def test_local_copy_creates_nested_destination(tmp_path):
    client = bucket.LocalGCSMock("project", tmp_path / "bucket")
    (client.bucket_path / "source.bin").write_bytes(b"data")
    client.copy_blob("source.bin", "nested/copy.bin")
    assert (client.bucket_path / "nested/copy.bin").read_bytes() == b"data"


def test_real_gcs_download_upload_and_copy_use_expected_blob_names(tmp_path, monkeypatch):
    storage_client = Mock()
    monkeypatch.setattr(bucket.storage, "Client", Mock(return_value=storage_client))
    client = bucket.RealGCS("project", "models")
    remote = storage_client.get_bucket.return_value
    blob = remote.blob.return_value
    blob.exists.return_value = True
    target = tmp_path / "nested/model.bin"
    client.download("v1/model.bin", str(target))
    assert target.parent.is_dir()
    blob.download_to_filename.assert_called_once_with(target)
    client.upload(target, "v1/model.bin")
    assert blob.cache_control == "no-cache"
    blob.upload_from_filename.assert_called_once_with(target)
    client.copy_blob("v1/model.bin", "v2/model.bin")
    remote.copy_blob.assert_called_once_with(blob, storage_client.bucket.return_value, "v2/model.bin")


def test_real_gcs_missing_copy_source_is_not_copied(monkeypatch):
    storage_client = Mock()
    monkeypatch.setattr(bucket.storage, "Client", Mock(return_value=storage_client))
    client = bucket.RealGCS("project", "models")
    remote = storage_client.get_bucket.return_value
    remote.blob.return_value.exists.return_value = False
    client.copy_blob("missing", "target")
    remote.copy_blob.assert_not_called()

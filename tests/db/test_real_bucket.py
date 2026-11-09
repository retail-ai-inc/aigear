from datetime import datetime, timezone

import pytest
from google.api_core import exceptions

from aigear.common.schema.bucket_schema import (
    ObjectNotFound, PreconditionFailed, StorageError, StoragePermissionDenied, VersionMismatch,
)
from aigear.db.bucket import RealGCS


def test_rewrite_request_pins_live_versions_and_replaces_metadata(gcs_service):
    source = RealGCS("p", "source")
    destination = RealGCS("p", "destination")
    original = source.write_bytes("original", b"data", custom_time=datetime.now(timezone.utc))
    target = destination.write_bytes("target", b"previous")
    copied = source.copy_blob("original", "target", destination_bucket=destination,
                              source_generation=original.generation, if_generation_match=target.generation)
    method, path, query, body = [r for r in gcs_service.requests if r[0] == "post"][-1]
    assert path == "/b/source/o/original/rewriteTo/b/destination/o/target"
    assert query["ifGenerationMatch"] == int(target.generation)
    assert query["ifSourceGenerationMatch"] == int(original.generation)
    assert query["ifSourceMetagenerationMatch"] == int(original.metageneration)
    assert "sourceGeneration" not in query
    assert body and "customTime" not in body
    assert copied.custom_time is None
    assert copied.generation != target.generation


def test_multipart_rewrite_repeats_conditions_and_tokens(gcs_service, monkeypatch):
    client = RealGCS("p", "source")
    original = client.write_bytes("original", b"data")
    post = gcs_service.post
    calls = []
    def partial(path, body, query_params=None, **kwargs):
        calls.append(dict(query_params))
        if len(calls) == 1:
            return {"done": False, "rewriteToken": "continue", "totalBytesRewritten": "2", "objectSize": "4"}
        return post(path, body, query_params=query_params, **kwargs)
    monkeypatch.setattr(gcs_service.client, "_post_resource", partial)
    result = client.copy_blob("original", "copy", source_generation=original.generation, if_generation_match=0)
    assert result.size == 4
    assert len(calls) == 2
    assert "rewriteToken" not in calls[0]
    assert calls[1]["rewriteToken"] == "continue"
    for query in calls:
        assert query["ifGenerationMatch"] == 0
        assert query["ifSourceGenerationMatch"] == int(original.generation)
        assert query["ifSourceMetagenerationMatch"] == 1


@pytest.mark.parametrize("change", ["source", "destination", "source_metadata"])
def test_change_during_rewrite_stops_without_overwriting(gcs_service, monkeypatch, change):
    client = RealGCS("p", "source")
    old = client.write_bytes("original", b"old")
    target = client.write_bytes("target", b"previous")
    post = gcs_service.post
    calls = 0
    def race(path, body, query_params=None, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            if change == "source_metadata":
                client.update_metadata("original", if_generation_match=old.generation,
                                       if_metageneration_match=old.metageneration, metadata={"changed": "yes"})
            else:
                client.write_bytes("original" if change == "source" else "target", b"external")
            return {"done": False, "rewriteToken": "continue", "totalBytesRewritten": "1", "objectSize": "3"}
        return post(path, body, query_params=query_params, **kwargs)
    monkeypatch.setattr(gcs_service.client, "_post_resource", race)
    with pytest.raises(PreconditionFailed) as failure:
        client.copy_blob("original", "target", source_generation=old.generation,
                         if_generation_match=target.generation)
    assert isinstance(failure.value.cause, exceptions.PreconditionFailed)
    assert client.read_bytes("target") == (b"external" if change == "destination" else b"previous")


def test_implicit_copy_conditions_are_also_pinned(gcs_service):
    client = RealGCS("p", "source")
    original = client.write_bytes("original", b"data")
    client.copy_blob("original", "target")
    first = [r for r in gcs_service.requests if r[0] == "post"][-1]
    assert first[2]["ifGenerationMatch"] == 0
    target = client.get_metadata("target")
    client.copy_blob("original", "target")
    second = [r for r in gcs_service.requests if r[0] == "post"][-1]
    assert second[2]["ifGenerationMatch"] == int(target.generation)
    assert second[2]["ifSourceGenerationMatch"] == int(original.generation)


def test_patch_delete_and_bucket_conditions_reach_sdk(gcs_service):
    client = RealGCS("p", "source")
    item = client.write_bytes("file", b"data")
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    result = client.update_metadata("file", if_generation_match=item.generation,
                                    if_metageneration_match=item.metageneration, custom_time=now)
    patch = [r for r in gcs_service.requests if r[0] == "patch"][-1]
    assert patch[2]["ifGenerationMatch"] == int(item.generation)
    assert patch[2]["ifMetagenerationMatch"] == 1
    assert "generation" not in patch[2]
    assert patch[3]["customTime"] == "2026-10-23T00:00:00.000000Z"
    client.delete("file", if_generation_match=result.generation)
    assert gcs_service.requests[-1][2] == {"ifGenerationMatch": int(item.generation)}
    before = client.get_lifecycle()
    client.update_lifecycle([], if_metageneration_match=before.metageneration)
    patch = gcs_service.requests[-1]
    assert patch[1] == "/b/source"
    assert patch[2]["ifMetagenerationMatch"] == int(before.metageneration)
    assert set(patch[3]) == {"lifecycle"}


@pytest.mark.parametrize("error,expected", [
    (exceptions.NotFound, ObjectNotFound),
    (exceptions.Forbidden, StoragePermissionDenied),
    (exceptions.Unauthorized, StoragePermissionDenied),
    (exceptions.PreconditionFailed, PreconditionFailed),
    (exceptions.ServiceUnavailable, StorageError),
])
def test_error_categories_preserve_original_cause(gcs_service, monkeypatch, caplog, error, expected):
    client = RealGCS("p", "source")
    cause = error("secret-signed-url")
    def fail(*args, **kwargs):
        raise cause
    monkeypatch.setattr(gcs_service.client, "_get_resource", fail)
    with pytest.raises(expected) as failure:
        client.get_metadata("file")
    assert failure.value.cause is cause
    assert failure.value.__cause__ is cause
    assert failure.value.phase == "metadata"
    assert "secret-signed-url" not in caplog.text
    assert "secret-signed-url" not in str(failure.value)


def test_fixed_read_race_does_not_retry_without_generation(gcs_service, monkeypatch):
    client = RealGCS("p", "source")
    old = client.write_bytes("file", b"old")
    calls = []
    def raced(blob, **kwargs):
        calls.append(kwargs)
        raise exceptions.PreconditionFailed("changed")
    from google.cloud import storage
    monkeypatch.setattr(storage.Blob, "download_as_bytes", raced)
    with pytest.raises(VersionMismatch):
        client.read_bytes("file", generation=old.generation)
    assert calls == [{"if_generation_match": int(old.generation), "checksum": "crc32c"}]

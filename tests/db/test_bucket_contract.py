from datetime import datetime, timedelta, timezone
import json

import pytest

from aigear.common.schema.bucket_schema import (
    ObjectNotFound, PreconditionFailed, VersionMismatch,
)
from aigear.management.asset_errors import InvalidAssetPath

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def test_json_metadata_and_fixed_download(stores, tmp_path):
    source, _ = stores
    info = source.write_json("running/v1/manifest.json", {"unicode": "数据", "values": [1, None]},
                             if_generation_match="0")
    assert isinstance(info.generation, str) and int(info.generation) > 0
    assert info.metageneration == "1"
    assert info.content_type == "application/json"
    assert info.size == len(source.read_bytes(info.name))
    assert source.read_json(info.name, generation=info.generation)["unicode"] == "数据"
    output = tmp_path / "cache/result.json"
    source.download(info.name, output, generation=info.generation)
    assert json.loads(output.read_bytes())["values"] == [1, None]
    assert source.list_metadata("running/") == [info]
    assert source.list_metadata("assets/") == []


def test_cas_conflicts_do_not_change_content_or_metadata(stores):
    source, _ = stores
    first = source.write_bytes("file", b"123456789", if_generation_match=0)
    assert first.crc32c == "4waSgw=="  # Published CRC32C check vector.
    with pytest.raises(PreconditionFailed):
        source.write_json("file", {"bad": True}, if_generation_match=0)
    assert source.get_metadata("file") == first
    second = source.write_bytes("file", b"next", if_generation_match=first.generation)
    assert second.generation != first.generation
    assert second.metageneration == "1"
    with pytest.raises(PreconditionFailed) as failure:
        source.write_bytes("file", b"bad", if_generation_match=first.generation)
    assert failure.value.cause is not None
    assert source.read_bytes("file") == b"next"
    with pytest.raises(VersionMismatch):
        source.read_bytes("file", generation=first.generation)


def test_failed_download_preserves_previous_cache(stores, tmp_path):
    source, _ = stores
    old = source.write_bytes("file", b"old")
    source.write_bytes("file", b"new")
    cache = tmp_path / "cache.bin"
    cache.write_bytes(b"cached")
    with pytest.raises(VersionMismatch):
        source.download("file", cache, generation=old.generation)
    assert cache.read_bytes() == b"cached"


@pytest.mark.parametrize("cross_bucket", [False, True])
def test_copy_pins_versions_and_omits_custom_time(stores, cross_bucket):
    source, other = stores
    target = other if cross_bucket else source
    info = source.write_bytes("source", b"contents", custom_time=NOW,
                              metadata={"producer": "run-A"}, content_type="text/plain")
    copied = source.copy_blob("source", "copy", destination_bucket=target,
                              source_generation=info.generation, if_generation_match=0)
    assert target.read_bytes("copy", generation=copied.generation) == b"contents"
    assert copied.custom_time is None
    assert copied.metadata == {"producer": "run-A"}
    assert copied.content_type == "text/plain"
    assert copied.crc32c == info.crc32c
    with pytest.raises(PreconditionFailed):
        source.copy_blob("source", "copy", destination_bucket=target,
                         source_generation=info.generation, if_generation_match=0)
    source.write_bytes("source", b"changed")
    with pytest.raises(VersionMismatch):
        source.copy_blob("source", "copy", destination_bucket=target,
                         source_generation=info.generation, if_generation_match=copied.generation)
    assert target.get_metadata("copy") == copied


def test_rewrite_resets_custom_time_and_returns_new_generation(stores):
    source, _ = stores
    old = source.write_bytes("file", b"old", custom_time=NOW)
    new = source.rewrite("file", "file", source_generation=old.generation,
                         if_generation_match=old.generation)
    assert new.generation != old.generation
    assert new.custom_time is None
    assert source.read_bytes("file") == b"old"
    later = source.rewrite("file", "file", source_generation=new.generation,
                           if_generation_match=new.generation, custom_time=NOW + timedelta(days=1))
    assert later.custom_time == NOW + timedelta(days=1)


def test_conditional_delete_never_deletes_new_generation(stores):
    source, _ = stores
    first = source.write_bytes("file", b"first")
    second = source.write_bytes("file", b"second")
    with pytest.raises(PreconditionFailed):
        source.delete("file", if_generation_match=first.generation)
    assert source.get_metadata("file") == second
    source.delete("file", if_generation_match=second.generation)
    with pytest.raises(ObjectNotFound):
        source.get_metadata("file")
    third = source.write_bytes("file", b"third", if_generation_match=0)
    assert third.generation != second.generation


def test_conditional_metadata_preserves_generation_and_custom_time(stores):
    source, _ = stores
    old = source.write_bytes("file", b"data", custom_time=NOW)
    new = source.update_metadata("file", if_generation_match=old.generation,
                                 if_metageneration_match=old.metageneration,
                                 metadata={"key": "value"})
    assert new.generation == old.generation
    assert new.metageneration != old.metageneration
    assert new.custom_time == NOW
    assert new.metadata == {"key": "value"}
    with pytest.raises(PreconditionFailed):
        source.update_metadata("file", if_generation_match=old.generation,
                               if_metageneration_match=old.metageneration, custom_time=NOW)
    with pytest.raises(ValueError, match="backwards"):
        source.update_metadata("file", if_generation_match=new.generation,
                               if_metageneration_match=new.metageneration,
                               custom_time=NOW - timedelta(days=1))
    assert source.get_metadata("file") == new


def test_lifecycle_metageneration_cas(stores):
    source, _ = stores
    old = source.get_lifecycle()
    rules = [{"action": {"type": "Delete"}, "condition": {
        "matchesPrefix": ["trash/"], "daysSinceCustomTime": 30}}]
    updated = source.update_lifecycle(rules, if_metageneration_match=old.metageneration)
    assert updated.rules == rules
    assert updated.metageneration != old.metageneration
    with pytest.raises(PreconditionFailed):
        source.update_lifecycle([], if_metageneration_match=old.metageneration)
    assert source.get_lifecycle() == updated


@pytest.mark.parametrize("operation", ["metadata", "read", "copy", "delete", "patch"])
def test_missing_objects_raise_with_cause(stores, operation):
    source, _ = stores
    methods = {
        "metadata": lambda: source.get_metadata("missing"),
        "read": lambda: source.read_json("missing", generation="1"),
        "copy": lambda: source.copy_blob("missing", "target"),
        "delete": lambda: source.delete("missing", if_generation_match="1"),
        "patch": lambda: source.update_metadata("missing", if_generation_match="1",
                                                 if_metageneration_match="1", custom_time=NOW),
    }
    with pytest.raises(ObjectNotFound) as failure:
        methods[operation]()
    assert failure.value.cause is not None
    assert source.list_metadata() == []


@pytest.mark.parametrize("path", ["../x", "/absolute", "gs://bucket/file", "a\\b", "a//b"])
def test_unsafe_keys_are_rejected(stores, path):
    source, _ = stores
    with pytest.raises(InvalidAssetPath):
        source.write_json(path, {})
    assert source.list_metadata() == []


@pytest.mark.parametrize("generation", [True, "-1", "1.2", "", "١", None])
def test_delete_requires_positive_generation(stores, generation):
    source, _ = stores
    info = source.write_bytes("file", b"data")
    with pytest.raises(ValueError):
        source.delete("file", if_generation_match=generation)
    assert source.get_metadata("file") == info


def test_invalid_json_never_overwrites(stores):
    source, _ = stores
    old = source.write_json("manifest.json", {"committed": True})
    with pytest.raises(ValueError):
        source.write_json("manifest.json", {"bad": float("nan")}, if_generation_match=old.generation)
    assert source.get_metadata("manifest.json") == old


def test_public_method_signatures_are_interchangeable():
    import inspect
    from aigear.db.bucket import LocalGCSMock, RealGCS
    def methods(backend):
        return {name: inspect.signature(method) for name, method in
                inspect.getmembers(backend, inspect.isfunction)
                if not name.startswith("_") or name == "__init__"}
    assert methods(LocalGCSMock) == methods(RealGCS)


def test_same_calling_code_and_result_shapes_for_both_backends(tmp_path, monkeypatch, gcs_service):
    from dataclasses import asdict
    from aigear.common.schema.bucket_schema import ObjectMetadata, BucketLifecycle
    from aigear.db.bucket import LocalGCSMock, RealGCS
    monkeypatch.chdir(tmp_path)
    input_file = tmp_path / "cache/input.bin"
    input_file.parent.mkdir()
    input_file.write_bytes(b"model bytes")

    def scenario(backend):
        # Only backend selection varies; construction and every operation use
        # the same argument names, values and return-value handling.
        source = backend(project_id="p", bucket_name="source")
        destination = backend(project_id="p", bucket_name="destination")
        uploaded = source.upload(local_blob_name=input_file, bucket_blob_name="training/model.bin",
                                 if_generation_match="0", custom_time=NOW,
                                 metadata={"producer": "run-1"}, content_type="application/octet-stream")
        assert type(uploaded) is ObjectMetadata
        recorded = source.write_json(name="manifest.json", value={"model": uploaded.name},
                                     if_generation_match="0", custom_time=NOW)
        assert type(recorded) is ObjectMetadata
        value = source.read_json(name="manifest.json", generation=recorded.generation)
        copied = source.copy_blob(source_blob_name=uploaded.name, destination_blob_name="model.bin",
                                  destination_bucket=destination, source_generation=uploaded.generation,
                                  if_generation_match="0", custom_time=NOW)
        patched = destination.update_metadata(name="model.bin", if_generation_match=copied.generation,
                                              if_metageneration_match=copied.metageneration,
                                              custom_time=NOW, metadata={"producer": "run-1"})
        rewritten = destination.rewrite(source_blob_name="model.bin", destination_blob_name="restored.bin",
                                        source_generation=patched.generation, if_generation_match="0")
        assert rewritten.custom_time is None
        output_file = tmp_path / "cache/output.bin"
        assert destination.download(bucket_blob_name="restored.bin", local_blob_path=output_file,
                                    generation=rewritten.generation) is None
        assert output_file.read_bytes() == b"model bytes"
        rules = [{"action": {"type": "Delete"}, "condition": {
            "matchesPrefix": ["model"], "daysSinceCustomTime": 30}}]
        before = destination.get_lifecycle()
        assert type(before) is BucketLifecycle
        after = destination.update_lifecycle(rules=rules, if_metageneration_match=before.metageneration)
        assert type(after) is BucketLifecycle
        monkeypatch.setattr(destination, "_clock", lambda: NOW + timedelta(days=30))
        assert [item.name for item in destination.lifecycle_candidates()] == ["model.bin"]
        descriptors = destination.list_metadata(prefix="")
        for item in descriptors:
            assert type(item) is ObjectMetadata
            assert type(item.generation) is str
            assert type(item.metageneration) is str
            assert type(item.size) is int
        assert destination.get_metadata(name="model.bin") == patched
        assert destination.read_bytes(name="model.bin", generation=patched.generation) == b"model bytes"
        assert destination.delete(name="restored.bin", if_generation_match=rewritten.generation) is None
        assert source.close() is None
        assert destination.close() is None
        # Opaque version tokens differ between physical stores; all other
        # returned fields and collection shapes must match.
        normalized = [{key: value for key, value in asdict(item).items()
                       if key not in {"generation", "metageneration"}} for item in descriptors]
        return value, normalized, after.rules

    assert scenario(LocalGCSMock) == scenario(RealGCS)

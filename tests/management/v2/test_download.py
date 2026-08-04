from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.download import (
    DownloadError,
    compute_local_cache_key,
    download_bundle_exact,
    download_exact,
    verify_cached_file,
)
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.resolver import ResolvedBlobHandle, ResolvedHandle, UsageContext

_FP = TypedId.from_bare("aa" * 32)
_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)


def _resolved_blob(gcs: FakeGcsClient, *, object_name="_objects/sha256/aa/blob-1", data=b"hello world"):
    snapshot = gcs.put_object(object_name, data, if_generation_match=0)
    return ResolvedBlobHandle(
        role="model",
        logical_name="weights",
        blob_id=TypedId.from_bare(hashlib.sha256(data).hexdigest()),
        bucket="test-bucket",
        object_name=object_name,
        generation=snapshot.generation,
        sha256=snapshot.sha256,
        size_bytes=snapshot.size_bytes,
        location_revision=1,
        location_attestation_ref=TypedId.from_bare("bb" * 32),
    )


def _handle(blobs, *, expires_at=_NOW + timedelta(minutes=5)) -> ResolvedHandle:
    return ResolvedHandle(
        usage_context=UsageContext.MANUAL_DOWNLOAD,
        resolved_at=_NOW,
        expires_at=expires_at,
        environment_fingerprint=_FP,
        registry_binding_id="binding-1",
        write_epoch=1,
        asset_version_id=TypedId.from_bare("cc" * 32),
        asset_record_revision=1,
        policy_decision_head_ref=None,
        blobs=tuple(blobs),
    )


def test_download_exact_writes_the_file_atomically(tmp_path):
    gcs = FakeGcsClient()
    blob = _resolved_blob(gcs, data=b"hello world")
    handle = _handle([blob])
    target = tmp_path / "out" / "model.bin"
    target.parent.mkdir()

    result = download_exact(handle, gcs, target)

    assert result == target
    assert target.read_bytes() == b"hello world"
    # No leftover temp files.
    assert list(target.parent.iterdir()) == [target]


def test_download_exact_rejects_expired_handle(tmp_path):
    gcs = FakeGcsClient()
    blob = _resolved_blob(gcs)
    handle = _handle([blob], expires_at=_NOW - timedelta(seconds=1))
    target = tmp_path / "model.bin"

    with pytest.raises(DownloadError, match="expired"):
        download_exact(handle, gcs, target, now=_NOW)
    assert not target.exists()


def test_download_exact_rejects_bundle_handles(tmp_path):
    gcs = FakeGcsClient()
    blob_a = _resolved_blob(gcs, object_name="_objects/sha256/aa/blob-a", data=b"a")
    blob_b = _resolved_blob(gcs, object_name="_objects/sha256/bb/blob-b", data=b"b")
    handle = _handle([blob_a, blob_b])
    target = tmp_path / "model.bin"

    with pytest.raises(DownloadError, match="single-component"):
        download_exact(handle, gcs, target)
    assert not target.exists()


def test_download_bundle_exact_publishes_complete_role_tree_atomically(tmp_path):
    gcs = FakeGcsClient()
    model = _resolved_blob(gcs, object_name="_objects/sha256/aa/model", data=b"model")
    schema = replace(
        _resolved_blob(gcs, object_name="_objects/sha256/bb/schema", data=b"schema"),
        role="schema",
        logical_name="contract.json",
    )
    handle = _handle([model, schema])
    target = tmp_path / "bundle"

    result = download_bundle_exact(handle, gcs, target)

    assert result == target
    assert (target / "model" / "weights").read_bytes() == b"model"
    assert (target / "schema" / "contract.json").read_bytes() == b"schema"
    assert [path for path in tmp_path.iterdir() if path.name.endswith(".tmp")] == []


def test_download_bundle_exact_keeps_target_hidden_and_cleans_temp_on_failure(tmp_path):
    gcs = FakeGcsClient()
    good = _resolved_blob(gcs, object_name="_objects/sha256/aa/good", data=b"good")
    bad = replace(
        _resolved_blob(gcs, object_name="_objects/sha256/bb/bad", data=b"bad"),
        role="schema",
        logical_name="contract.json",
        sha256="0" * 64,
    )
    target = tmp_path / "bundle"

    with pytest.raises(DownloadError, match="SHA-256 mismatch"):
        download_bundle_exact(_handle([good, bad]), gcs, target)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_bundle_exact_rejects_cross_platform_path_collisions(tmp_path):
    gcs = FakeGcsClient()
    first = _resolved_blob(gcs, object_name="_objects/sha256/aa/first", data=b"first")
    second = replace(
        _resolved_blob(gcs, object_name="_objects/sha256/bb/second", data=b"second"),
        role="MODEL",
        logical_name="other",
    )

    with pytest.raises(ValueError, match="collide"):
        download_bundle_exact(_handle([first, second]), gcs, tmp_path / "bundle")


def test_download_exact_rejects_oversized_blob(tmp_path):
    gcs = FakeGcsClient()
    blob = _resolved_blob(gcs, data=b"hello world")
    handle = _handle([blob])
    target = tmp_path / "model.bin"

    with pytest.raises(DownloadError, match="exceeds max_size_bytes"):
        download_exact(handle, gcs, target, max_size_bytes=len(b"hello world") - 1)
    assert not target.exists()


def test_download_exact_cleans_up_temp_file_on_size_mismatch(tmp_path, monkeypatch):
    gcs = FakeGcsClient()
    blob = _resolved_blob(gcs, data=b"hello world")
    # Simulate a data-consistency bug: the handle's declared size disagrees
    # with what is actually stored at that exact generation.
    tampered_blob = replace(blob, size_bytes=blob.size_bytes + 1)
    handle = _handle([tampered_blob])
    target = tmp_path / "model.bin"

    with pytest.raises(DownloadError, match="size mismatch"):
        download_exact(handle, gcs, target)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_exact_rejects_sha256_mismatch(tmp_path):
    gcs = FakeGcsClient()
    blob = _resolved_blob(gcs, data=b"hello world")
    tampered_blob = replace(blob, sha256="0" * 64)
    handle = _handle([tampered_blob])
    target = tmp_path / "model.bin"

    with pytest.raises(DownloadError, match="SHA-256 mismatch"):
        download_exact(handle, gcs, target)
    assert not target.exists()


def test_download_exact_overwrites_target_via_atomic_replace(tmp_path):
    gcs = FakeGcsClient()
    target = tmp_path / "model.bin"
    target.write_bytes(b"stale content")

    blob = _resolved_blob(gcs, data=b"fresh content")
    handle = _handle([blob])
    download_exact(handle, gcs, target)

    assert target.read_bytes() == b"fresh content"


# ── local cache key / verification ────────────────────────────────────────────────


def test_compute_local_cache_key_is_stable_and_distinguishes_generation_and_revision():
    blob_id = TypedId.from_bare("aa" * 32)
    key = compute_local_cache_key(blob_id, "12345", 1)
    assert key == compute_local_cache_key(blob_id, "12345", 1)
    assert key != compute_local_cache_key(blob_id, "67890", 1)
    assert key != compute_local_cache_key(blob_id, "12345", 2)


def test_verify_cached_file_accepts_matching_digest_and_size(tmp_path):
    path = tmp_path / "cached.bin"
    path.write_bytes(b"cached bytes")
    digest = hashlib.sha256(b"cached bytes").hexdigest()
    assert verify_cached_file(path, expected_sha256=digest, expected_size=len(b"cached bytes")) is True


def test_verify_cached_file_rejects_size_mismatch(tmp_path):
    path = tmp_path / "cached.bin"
    path.write_bytes(b"cached bytes")
    digest = hashlib.sha256(b"cached bytes").hexdigest()
    assert verify_cached_file(path, expected_sha256=digest, expected_size=999) is False


def test_verify_cached_file_rejects_digest_mismatch(tmp_path):
    path = tmp_path / "cached.bin"
    path.write_bytes(b"cached bytes")
    assert verify_cached_file(path, expected_sha256="0" * 64, expected_size=len(b"cached bytes")) is False


def test_verify_cached_file_returns_false_for_missing_file(tmp_path):
    assert verify_cached_file(tmp_path / "missing.bin", expected_sha256="0" * 64, expected_size=0) is False

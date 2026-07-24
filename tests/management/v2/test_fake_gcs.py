from __future__ import annotations

import pytest

from aigear.management.v2.fake_gcs import (
    FakeGcsClient,
    GenerationPreconditionError,
    ObjectNotFoundError,
)


def test_put_object_assigns_increasing_generations():
    gcs = FakeGcsClient()
    first = gcs.put_object("obj", b"hello")
    second = gcs.put_object("obj", b"world")
    assert int(second.generation) > int(first.generation)
    assert gcs.get_live_object("obj") == second


def test_create_only_precondition_rejects_existing_object():
    gcs = FakeGcsClient()
    gcs.put_object("obj", b"hello", if_generation_match=0)
    with pytest.raises(GenerationPreconditionError):
        gcs.put_object("obj", b"world", if_generation_match=0)


def test_create_only_precondition_allows_new_object():
    gcs = FakeGcsClient()
    snapshot = gcs.put_object("obj", b"hello", if_generation_match=0)
    assert snapshot.data == b"hello"


def test_get_object_by_exact_generation_survives_later_writes():
    gcs = FakeGcsClient()
    first = gcs.put_object("obj", b"hello")
    gcs.put_object("obj", b"world")
    assert gcs.get_object("obj", generation=first.generation).data == b"hello"


def test_get_object_rejects_unknown_generation():
    gcs = FakeGcsClient()
    gcs.put_object("obj", b"hello")
    with pytest.raises(ObjectNotFoundError):
        gcs.get_object("obj", generation="does-not-exist")


def test_copy_object_is_create_only_by_default():
    gcs = FakeGcsClient()
    source = gcs.put_object("src", b"payload")
    copied = gcs.copy_object("src", source.generation, "dst")
    assert copied.data == b"payload"

    with pytest.raises(GenerationPreconditionError):
        gcs.copy_object("src", source.generation, "dst")


def test_snapshot_digest_matches_real_sha256():
    import hashlib

    gcs = FakeGcsClient()
    snapshot = gcs.put_object("obj", b"payload")
    assert snapshot.sha256 == hashlib.sha256(b"payload").hexdigest()
    assert snapshot.size_bytes == len(b"payload")

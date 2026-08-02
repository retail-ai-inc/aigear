from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2, IdentityConflict
from aigear.management.v2.firestore_registry import FirestoreRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.release import (
    AliasRecord,
    ReleasePhase,
    ReleaseRecord,
    ServiceReleaseState,
)
from tests.management.v2.records.test_release import _operation
from tests.management.v2.records.test_runtime_evidence import _evidence, _lease

_FP = TypedId.from_bare("aa" * 32)
_RELEASE = TypedId.from_bare("bb" * 32)
_AT = "2026-07-28T00:00:00+00:00"


def _release(**overrides):
    values = dict(
        schema_version="2.0",
        environment_fingerprint=_FP,
        release_id=_RELEASE,
        service_name="predictor",
        deployment_target_id="prod-cluster",
        manifest_digest=TypedId.from_bare("dd" * 32),
        signature_attestation_id=TypedId.from_bare("ee" * 32),
        creation_operation_id="release-op-1",
        display_version="service-v1",
        created_at=_AT,
    )
    values.update(overrides)
    return ReleaseRecord(**values)


def _alias(revision=1):
    return AliasRecord(
        schema_version="2.0",
        environment_fingerprint=_FP,
        service_name="predictor",
        alias_name="champion",
        release_id=_RELEASE,
        revision=revision,
        updated_by_operation_id="release-op-1",
        updated_at=_AT,
    )


def _state(revision=1):
    return ServiceReleaseState(
        schema_version="2.0",
        environment_fingerprint=_FP,
        service_name="predictor",
        revision=revision,
        display_version_counter=0,
        updated_at=_AT,
    )


def test_release_repository_enforces_create_once_and_revision_cas():
    registry = FakeRegistryV2()
    release = _release()
    assert registry.put_release(release) == release
    assert registry.put_release(release) == release
    with pytest.raises(IdentityConflict):
        registry.put_release(_release(service_name="other"))

    registry.put_alias(_alias())
    with pytest.raises(IdentityConflict, match="does not belong"):
        registry.put_alias(replace(_alias(), service_name="other"))
    with pytest.raises(IdentityConflict, match="CAS"):
        registry.put_alias(_alias(revision=3))
    registry.put_alias(_alias(revision=2))

    registry.put_service_release_state(_state())
    with pytest.raises(IdentityConflict, match="CAS"):
        registry.put_service_release_state(_state(revision=3))

    operation = _operation(created_at=_AT)
    registry.put_release_operation(operation)
    advanced = replace(operation, phase=ReleasePhase.VERIFYING, revision=3)
    assert registry.put_release_operation(advanced) == advanced


def test_fake_release_queries_are_cutoff_cursor_and_limit_bounded():
    registry = FakeRegistryV2()
    registry.put_release(_release())
    registry.put_alias(_alias())
    registry.put_release_operation(_operation(created_at=_AT))
    registry.put_runtime_evidence("predictor", _evidence())
    registry.put_runtime_authorization_lease("predictor", _lease())
    with pytest.raises(IdentityConflict, match="create-only"):
        registry.put_runtime_authorization_lease(
            "predictor",
            replace(_lease(), expires_at="2026-07-28T00:04:00+00:00"),
        )

    assert registry.query_releases(
        service_name="predictor", cutoff=_AT, cursor=None, limit=1
    ) == (_release(),)
    assert registry.query_aliases(
        service_name="predictor", cutoff=_AT, cursor=None, limit=1
    ) == (_alias(),)
    assert len(registry.query_release_operations(
        service_name="predictor", cutoff=_AT, cursor=None, limit=1
    )) == 1
    assert len(registry.query_runtime_evidence(
        service_name="predictor", cutoff=_AT, cursor=None, limit=1
    )) == 1
    assert len(registry.query_runtime_authorization_leases(
        service_name="predictor", cutoff=_AT, cursor=None, limit=1
    )) == 1

    with pytest.raises(ValueError, match="canonical UTC"):
        registry.query_releases(
            service_name="predictor",
            cutoff="2026-07-28T08:00:00+08:00",
            cursor=None,
            limit=1,
        )


class _Snapshot:
    def __init__(self, value):
        self.value = value
        self.exists = value is not None

    def to_dict(self):
        return self.value


class _Document:
    def __init__(self, values, path):
        self.values = values
        self.path = path

    def get(self):
        return _Snapshot(self.values.get(self.path))

    def create(self, value):
        if self.path in self.values:
            raise RuntimeError("already exists")
        self.values[self.path] = value

    def set(self, value):
        self.values[self.path] = value


class _DocumentClient:
    def __init__(self):
        self.values = {}

    def document(self, path):
        return _Document(self.values, path)


def test_firestore_release_repository_matches_fake_create_once_and_cas():
    registry = FirestoreRegistryV2("proj", "v1", client=_DocumentClient())
    release = _release()
    registry.put_release(release)
    assert registry.get_release(release.release_id) == release
    with pytest.raises(IdentityConflict):
        registry.put_release(_release(service_name="other"))

    registry.put_alias(_alias())
    with pytest.raises(IdentityConflict, match="CAS"):
        registry.put_alias(_alias(revision=3))
    registry.put_alias(_alias(revision=2))

    registry.put_service_release_state(_state())
    with pytest.raises(IdentityConflict, match="CAS"):
        registry.put_service_release_state(_state(revision=3))

    operation = _operation(created_at=_AT)
    registry.put_release_operation(operation)
    advanced = replace(operation, phase=ReleasePhase.VERIFYING, revision=3)
    assert registry.put_release_operation(advanced) == advanced

    evidence = _evidence()
    registry.put_runtime_evidence("predictor", evidence)
    assert registry.get_runtime_evidence("predictor", evidence.evidence_id) == evidence

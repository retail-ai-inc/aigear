from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.runtime_evidence import (
    InvalidRuntimeEvidenceError,
    RuntimeAuthorizationLease,
    RuntimeEvidenceKind,
    RuntimeEvidenceRecord,
    compute_runtime_authorization_lease_id,
    compute_runtime_evidence_id,
)

_FP = TypedId.from_bare("aa" * 32)
_RELEASE = TypedId.from_bare("bb" * 32)
_BINDING = TypedId.from_bare("cc" * 32)
_POLICY = TypedId.from_bare("dd" * 32)
_PAYLOAD = TypedId.from_bare("ee" * 32)


def _evidence(**overrides) -> RuntimeEvidenceRecord:
    values = {
        "schema_version": "2.0",
        "environment_fingerprint": _FP,
        "kind": RuntimeEvidenceKind.POD_OBSERVED,
        "release_id": _RELEASE,
        "pod_uid": "pod-uid-1",
        "k8s_resource_version": "123",
        "binding_tuple": ("image=sha256:1", "model=sha256:2"),
        "policy_decision_epoch": 4,
        "security_watermark": 8,
        "fencing_token": 3,
        "issuer_principal": "runtime@example.iam.gserviceaccount.com",
        "payload_digest": _PAYLOAD,
        "issued_at": "2026-07-28T00:00:00+00:00",
        "expires_at": "2026-07-28T00:05:00+00:00",
    }
    values.update(overrides)
    values.setdefault(
        "evidence_id",
        compute_runtime_evidence_id(
            kind=values["kind"],
            release_id=values["release_id"],
            pod_uid=values["pod_uid"],
            k8s_resource_version=values["k8s_resource_version"],
            binding_tuple=values["binding_tuple"],
            fencing_token=values["fencing_token"],
            payload_digest=values["payload_digest"],
        ),
    )
    return RuntimeEvidenceRecord(**values)


def _lease(**overrides) -> RuntimeAuthorizationLease:
    values = {
        "schema_version": "2.0",
        "environment_fingerprint": _FP,
        "release_id": _RELEASE,
        "pod_uid": "pod-uid-1",
        "binding_digest": _BINDING,
        "policy_attestation_id": _POLICY,
        "policy_valid_until": "2026-07-28T00:10:00+00:00",
        "security_watermark": 8,
        "journal_fresh_until": "2026-07-28T00:06:00+00:00",
        "max_ttl_seconds": 300,
        "issuer_principal": "controller@example.iam.gserviceaccount.com",
        "issued_at": "2026-07-28T00:00:00+00:00",
        "expires_at": "2026-07-28T00:05:00+00:00",
    }
    values.update(overrides)
    values.setdefault(
        "lease_id",
        compute_runtime_authorization_lease_id(
            release_id=values["release_id"],
            pod_uid=values["pod_uid"],
            binding_digest=values["binding_digest"],
            policy_attestation_id=values["policy_attestation_id"],
            security_watermark=values["security_watermark"],
            issued_at=values["issued_at"],
        ),
    )
    return RuntimeAuthorizationLease(**values)


def test_runtime_evidence_is_bound_to_release_pod_and_fence():
    evidence = _evidence()
    assert evidence.evidence_id == compute_runtime_evidence_id(
        kind=evidence.kind,
        release_id=evidence.release_id,
        pod_uid=evidence.pod_uid,
        k8s_resource_version=evidence.k8s_resource_version,
        binding_tuple=evidence.binding_tuple,
        fencing_token=evidence.fencing_token,
        payload_digest=evidence.payload_digest,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("pod_uid", "pod-uid-2"),
        ("release_id", TypedId.from_bare("12" * 32)),
        ("fencing_token", 4),
    ],
)
def test_runtime_evidence_cannot_be_reused_for_other_binding(field, value):
    with pytest.raises(InvalidRuntimeEvidenceError, match="evidence_id"):
        replace(_evidence(), **{field: value})


def test_runtime_evidence_rejects_naive_time():
    with pytest.raises(InvalidRuntimeEvidenceError, match="timezone-aware"):
        _evidence(expires_at="2026-07-28T00:05:00")


def test_runtime_evidence_requires_canonical_utc_time():
    with pytest.raises(InvalidRuntimeEvidenceError, match="canonical UTC"):
        _evidence(expires_at="2026-07-28T08:05:00+08:00")


def test_runtime_lease_accepts_minimum_security_bound():
    lease = _lease()
    assert lease.expires_at == "2026-07-28T00:05:00+00:00"


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires_at": "2026-07-28T00:05:01+00:00"},
        {
            "journal_fresh_until": "2026-07-28T00:04:00+00:00",
            "expires_at": "2026-07-28T00:05:00+00:00",
        },
        {
            "policy_valid_until": "2026-07-28T00:04:00+00:00",
            "expires_at": "2026-07-28T00:05:00+00:00",
        },
    ],
)
def test_runtime_lease_cannot_outlive_any_security_bound(overrides):
    with pytest.raises(InvalidRuntimeEvidenceError, match="exceeds"):
        _lease(**overrides)


def test_runtime_lease_identity_prevents_pod_reuse():
    with pytest.raises(InvalidRuntimeEvidenceError, match="lease_id"):
        replace(_lease(), pod_uid="pod-uid-2")


def test_runtime_lease_rejects_naive_policy_time():
    with pytest.raises(InvalidRuntimeEvidenceError, match="timezone-aware"):
        _lease(policy_valid_until="2026-07-28T00:10:00")

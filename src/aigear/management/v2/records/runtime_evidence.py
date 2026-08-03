"""Runtime evidence and bounded authorization leases for exact releases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "InvalidRuntimeEvidenceError",
    "RuntimeEvidenceKind",
    "compute_runtime_evidence_id",
    "compute_runtime_authorization_lease_id",
    "RuntimeEvidenceRecord",
    "RuntimeAuthorizationLease",
]


class InvalidRuntimeEvidenceError(ValueError):
    pass


class RuntimeEvidenceKind(str, Enum):
    POD_OBSERVED = "pod_observed"
    SMOKE = "smoke"
    TRAFFIC = "traffic"
    DRAIN = "drain"


def _typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidRuntimeEvidenceError(f"{field_name} must be a TypedId")


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidRuntimeEvidenceError(f"{field_name} must be a non-empty str")


def _non_negative(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidRuntimeEvidenceError(f"{field_name} must be a non-negative int")


def _positive(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidRuntimeEvidenceError(f"{field_name} must be a positive int")


def _aware_timestamp(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidRuntimeEvidenceError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidRuntimeEvidenceError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
        raise InvalidRuntimeEvidenceError(
            f"{field_name} must be a canonical UTC timestamp"
        )
    return parsed


def _binding_tuple(value: object) -> Tuple[str, ...]:
    if isinstance(value, list):
        value = tuple(value)
    if (
        not isinstance(value, tuple)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise InvalidRuntimeEvidenceError(
            "binding_tuple must be a non-empty tuple of non-empty strings"
        )
    return value


def _policy_attestation_ids(value: object) -> Tuple[TypedId, ...]:
    if isinstance(value, list):
        value = tuple(value)
    if (
        not isinstance(value, tuple)
        or not value
        or not all(isinstance(item, TypedId) for item in value)
    ):
        raise InvalidRuntimeEvidenceError(
            "policy_attestation_ids must be a non-empty tuple of TypedId"
        )
    if value != tuple(sorted(value, key=lambda item: item.typed)) or len(set(value)) != len(value):
        raise InvalidRuntimeEvidenceError(
            "policy_attestation_ids must be sorted and unique"
        )
    return value


def compute_runtime_evidence_id(
    *,
    kind: RuntimeEvidenceKind,
    release_id: TypedId,
    pod_uid: str,
    k8s_resource_version: str,
    binding_tuple: Tuple[str, ...],
    fencing_token: int,
    payload_digest: TypedId,
) -> TypedId:
    if not isinstance(kind, RuntimeEvidenceKind):
        raise InvalidRuntimeEvidenceError("kind must be a RuntimeEvidenceKind")
    _typed_id("release_id", release_id)
    _non_empty("pod_uid", pod_uid)
    _non_empty("k8s_resource_version", k8s_resource_version)
    binding_tuple = _binding_tuple(binding_tuple)
    _non_negative("fencing_token", fencing_token)
    _typed_id("payload_digest", payload_digest)
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            [
                "aigear.runtime-evidence.v2",
                kind.value,
                release_id.typed,
                pod_uid,
                k8s_resource_version,
                list(binding_tuple),
                fencing_token,
                payload_digest.typed,
            ]
        )
    )


def compute_runtime_authorization_lease_id(
    *,
    release_id: TypedId,
    pod_uid: str,
    binding_digest: TypedId,
    policy_attestation_ids: Tuple[TypedId, ...],
    security_watermark: int,
    issued_at: str,
) -> TypedId:
    _typed_id("release_id", release_id)
    _non_empty("pod_uid", pod_uid)
    _typed_id("binding_digest", binding_digest)
    policy_attestation_ids = _policy_attestation_ids(policy_attestation_ids)
    _non_negative("security_watermark", security_watermark)
    _aware_timestamp("issued_at", issued_at)
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            [
                "aigear.runtime-authorization-lease.v2",
                release_id.typed,
                pod_uid,
                binding_digest.typed,
                [value.typed for value in policy_attestation_ids],
                security_watermark,
                issued_at,
            ]
        )
    )


@dataclass(frozen=True)
class RuntimeEvidenceRecord:
    schema_version: str
    environment_fingerprint: TypedId
    evidence_id: TypedId
    kind: RuntimeEvidenceKind
    release_id: TypedId
    pod_uid: str
    k8s_resource_version: str
    binding_tuple: Tuple[str, ...]
    policy_decision_epoch: int
    security_watermark: int
    fencing_token: int
    issuer_principal: str
    payload_digest: TypedId
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        if isinstance(self.binding_tuple, list):
            object.__setattr__(self, "binding_tuple", tuple(self.binding_tuple))
        parse_schema_version(self.schema_version)
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        _typed_id("evidence_id", self.evidence_id)
        if not isinstance(self.kind, RuntimeEvidenceKind):
            raise InvalidRuntimeEvidenceError("kind must be a RuntimeEvidenceKind")
        _typed_id("release_id", self.release_id)
        _non_empty("pod_uid", self.pod_uid)
        _non_empty("k8s_resource_version", self.k8s_resource_version)
        _binding_tuple(self.binding_tuple)
        _positive("policy_decision_epoch", self.policy_decision_epoch)
        _non_negative("security_watermark", self.security_watermark)
        _non_negative("fencing_token", self.fencing_token)
        _non_empty("issuer_principal", self.issuer_principal)
        _typed_id("payload_digest", self.payload_digest)
        issued_at = _aware_timestamp("issued_at", self.issued_at)
        expires_at = _aware_timestamp("expires_at", self.expires_at)
        if expires_at <= issued_at:
            raise InvalidRuntimeEvidenceError("expires_at must be later than issued_at")
        expected = compute_runtime_evidence_id(
            kind=self.kind,
            release_id=self.release_id,
            pod_uid=self.pod_uid,
            k8s_resource_version=self.k8s_resource_version,
            binding_tuple=self.binding_tuple,
            fencing_token=self.fencing_token,
            payload_digest=self.payload_digest,
        )
        if expected != self.evidence_id:
            raise InvalidRuntimeEvidenceError("evidence_id does not match evidence identity")


@dataclass(frozen=True)
class RuntimeAuthorizationLease:
    schema_version: str
    environment_fingerprint: TypedId
    lease_id: TypedId
    release_id: TypedId
    pod_uid: str
    binding_digest: TypedId
    policy_attestation_ids: Tuple[TypedId, ...]
    policy_valid_until: str
    security_watermark: int
    journal_fresh_until: str
    max_ttl_seconds: int
    issuer_principal: str
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        for field_name in (
            "environment_fingerprint",
            "lease_id",
            "release_id",
            "binding_digest",
        ):
            _typed_id(field_name, getattr(self, field_name))
        if isinstance(self.policy_attestation_ids, list):
            object.__setattr__(
                self, "policy_attestation_ids", tuple(self.policy_attestation_ids)
            )
        _policy_attestation_ids(self.policy_attestation_ids)
        _non_empty("pod_uid", self.pod_uid)
        policy_valid_until = _aware_timestamp("policy_valid_until", self.policy_valid_until)
        _non_negative("security_watermark", self.security_watermark)
        journal_fresh_until = _aware_timestamp(
            "journal_fresh_until", self.journal_fresh_until
        )
        _positive("max_ttl_seconds", self.max_ttl_seconds)
        _non_empty("issuer_principal", self.issuer_principal)
        issued_at = _aware_timestamp("issued_at", self.issued_at)
        expires_at = _aware_timestamp("expires_at", self.expires_at)
        upper_bound = min(
            policy_valid_until,
            journal_fresh_until,
            issued_at + timedelta(seconds=self.max_ttl_seconds),
        )
        if expires_at <= issued_at:
            raise InvalidRuntimeEvidenceError("expires_at must be later than issued_at")
        if expires_at > upper_bound:
            raise InvalidRuntimeEvidenceError(
                "expires_at exceeds policy, journal freshness or maximum TTL"
            )
        expected = compute_runtime_authorization_lease_id(
            release_id=self.release_id,
            pod_uid=self.pod_uid,
            binding_digest=self.binding_digest,
            policy_attestation_ids=self.policy_attestation_ids,
            security_watermark=self.security_watermark,
            issued_at=self.issued_at,
        )
        if expected != self.lease_id:
            raise InvalidRuntimeEvidenceError(
                "lease_id does not match authorization lease identity"
            )

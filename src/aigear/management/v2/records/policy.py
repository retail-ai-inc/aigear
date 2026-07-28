"""Immutable policy-decision records and the monotonic subject head."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidPolicyRecordError",
    "PolicyDecisionConflictError",
    "PolicyDecision",
    "compute_subject_epoch_key",
    "PolicyDecisionUnsignedEnvelope",
    "PolicyDecisionRequest",
    "PolicyDecisionEpochBinding",
    "PolicyDecisionHead",
    "require_effective_policy_head",
]


class InvalidPolicyRecordError(ValueError):
    """Raised when a policy record is malformed or not currently effective."""


class PolicyDecisionConflictError(ValueError):
    """Raised when one subject epoch is bound to different attestations."""


class PolicyDecision(str, Enum):
    APPROVED = "approved"
    REVOKED = "revoked"


def _typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidPolicyRecordError(f"{field_name} must be a TypedId")


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidPolicyRecordError(f"{field_name} must be a non-empty str")


def _positive(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidPolicyRecordError(f"{field_name} must be a positive int")


def _non_negative(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidPolicyRecordError(f"{field_name} must be a non-negative int")


def _aware_timestamp(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidPolicyRecordError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidPolicyRecordError(f"{field_name} must be timezone-aware")
    return parsed


def _optional_aware_timestamp(field_name: str, value: Optional[str]) -> None:
    if value is not None:
        _aware_timestamp(field_name, value)


def compute_subject_epoch_key(subject_asset_version_id: TypedId, decision_epoch: int) -> TypedId:
    _typed_id("subject_asset_version_id", subject_asset_version_id)
    _positive("decision_epoch", decision_epoch)
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            [
                "aigear.policy-decision-epoch.v2",
                subject_asset_version_id.typed,
                decision_epoch,
            ]
        )
    )


@dataclass(frozen=True)
class PolicyDecisionUnsignedEnvelope:
    schema_version: str
    environment_id: str
    environment_fingerprint: TypedId
    subject_asset_version_id: TypedId
    decision: PolicyDecision
    decision_epoch: int
    policy_version: str
    evidence_digests: Tuple[TypedId, ...]
    evidence_closure_digest: TypedId
    firestore_read_time: str
    issued_at: str
    not_before: str
    valid_until: str
    key_version: str

    def __post_init__(self) -> None:
        if isinstance(self.evidence_digests, list):
            object.__setattr__(self, "evidence_digests", tuple(self.evidence_digests))
        parse_schema_version(self.schema_version)
        object.__setattr__(
            self,
            "environment_id",
            validate_segment(self.environment_id, field_name="environment_id"),
        )
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        _typed_id("subject_asset_version_id", self.subject_asset_version_id)
        if not isinstance(self.decision, PolicyDecision):
            raise InvalidPolicyRecordError("decision must be a PolicyDecision")
        _positive("decision_epoch", self.decision_epoch)
        _non_empty("policy_version", self.policy_version)
        if not self.evidence_digests or not all(
            isinstance(value, TypedId) for value in self.evidence_digests
        ):
            raise InvalidPolicyRecordError(
                "evidence_digests must be a non-empty tuple of TypedId values"
            )
        ordered = tuple(sorted(self.evidence_digests, key=lambda value: value.typed.encode("utf-8")))
        if ordered != self.evidence_digests:
            raise InvalidPolicyRecordError("evidence_digests must be sorted by UTF-8 bytes")
        if len(set(self.evidence_digests)) != len(self.evidence_digests):
            raise InvalidPolicyRecordError("evidence_digests must not contain duplicates")
        _typed_id("evidence_closure_digest", self.evidence_closure_digest)
        read_time = _aware_timestamp("firestore_read_time", self.firestore_read_time)
        issued_at = _aware_timestamp("issued_at", self.issued_at)
        not_before = _aware_timestamp("not_before", self.not_before)
        valid_until = _aware_timestamp("valid_until", self.valid_until)
        if read_time > issued_at:
            raise InvalidPolicyRecordError("firestore_read_time cannot be later than issued_at")
        if not_before < issued_at:
            raise InvalidPolicyRecordError("not_before cannot be earlier than issued_at")
        if valid_until <= not_before:
            raise InvalidPolicyRecordError("valid_until must be later than not_before")
        _non_empty("key_version", self.key_version)

    def to_jcs_dict(self) -> dict:
        return {
            "domain": "aigear.attestation.policy_decision.v2",
            "schema_version": self.schema_version,
            "attestation_kind": "policy_decision",
            "environment_id": self.environment_id,
            "environment_fingerprint": self.environment_fingerprint.typed,
            "subject_asset_version_id": self.subject_asset_version_id.typed,
            "decision": self.decision.value,
            "decision_epoch": self.decision_epoch,
            "policy_version": self.policy_version,
            "evidence_digests": [value.typed for value in self.evidence_digests],
            "evidence_closure_digest": self.evidence_closure_digest.typed,
            "firestore_read_time": self.firestore_read_time,
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "valid_until": self.valid_until,
            "key_version": self.key_version,
        }

    @property
    def digest(self) -> TypedId:
        return TypedId.from_bare(digest_sha256_of_jcs(self.to_jcs_dict()))


@dataclass(frozen=True)
class PolicyDecisionRequest:
    schema_version: str
    operation_id: str
    request_fingerprint: TypedId
    expected_head_revision: int
    unsigned_envelope: PolicyDecisionUnsignedEnvelope
    unsigned_envelope_digest: TypedId

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        object.__setattr__(
            self, "operation_id", validate_segment(self.operation_id, field_name="operation_id")
        )
        _typed_id("request_fingerprint", self.request_fingerprint)
        _non_negative("expected_head_revision", self.expected_head_revision)
        if not isinstance(self.unsigned_envelope, PolicyDecisionUnsignedEnvelope):
            raise InvalidPolicyRecordError(
                "unsigned_envelope must be a PolicyDecisionUnsignedEnvelope"
            )
        if self.unsigned_envelope.schema_version != self.schema_version:
            raise InvalidPolicyRecordError(
                "request and unsigned envelope schema_version must match"
            )
        _typed_id("unsigned_envelope_digest", self.unsigned_envelope_digest)
        if self.unsigned_envelope.digest != self.unsigned_envelope_digest:
            raise InvalidPolicyRecordError(
                "unsigned_envelope_digest does not match unsigned_envelope"
            )


@dataclass(frozen=True)
class PolicyDecisionEpochBinding:
    schema_version: str
    environment_fingerprint: TypedId
    subject_epoch_key: TypedId
    subject_asset_version_id: TypedId
    decision_epoch: int
    attestation_id: TypedId
    decision: PolicyDecision
    policy_version: str
    not_before: str
    valid_until: str
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        _typed_id("subject_epoch_key", self.subject_epoch_key)
        _typed_id("subject_asset_version_id", self.subject_asset_version_id)
        _positive("decision_epoch", self.decision_epoch)
        if (
            compute_subject_epoch_key(self.subject_asset_version_id, self.decision_epoch)
            != self.subject_epoch_key
        ):
            raise InvalidPolicyRecordError(
                "subject_epoch_key does not match subject and decision_epoch"
            )
        _typed_id("attestation_id", self.attestation_id)
        if not isinstance(self.decision, PolicyDecision):
            raise InvalidPolicyRecordError("decision must be a PolicyDecision")
        _non_empty("policy_version", self.policy_version)
        not_before = _aware_timestamp("not_before", self.not_before)
        valid_until = _aware_timestamp("valid_until", self.valid_until)
        if valid_until <= not_before:
            raise InvalidPolicyRecordError("valid_until must be later than not_before")
        _optional_aware_timestamp("created_at", self.created_at)

    def assert_same_identity(self, other: "PolicyDecisionEpochBinding") -> None:
        if not isinstance(other, PolicyDecisionEpochBinding):
            raise PolicyDecisionConflictError("epoch binding type mismatch")
        if self == other:
            return
        if (
            self.subject_asset_version_id == other.subject_asset_version_id
            and self.decision_epoch == other.decision_epoch
        ):
            raise PolicyDecisionConflictError(
                "subject decision epoch is already bound to different evidence"
            )
        raise PolicyDecisionConflictError("epoch binding identity mismatch")


@dataclass(frozen=True)
class PolicyDecisionHead:
    schema_version: str
    environment_fingerprint: TypedId
    subject_asset_version_id: TypedId
    current_epoch: int
    revision: int
    attestation_id: Optional[TypedId] = None
    decision: Optional[PolicyDecision] = None
    policy_version: Optional[str] = None
    not_before: Optional[str] = None
    valid_until: Optional[str] = None
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        _typed_id("subject_asset_version_id", self.subject_asset_version_id)
        _non_negative("current_epoch", self.current_epoch)
        _positive("revision", self.revision)
        decision_fields = (
            self.attestation_id,
            self.decision,
            self.policy_version,
            self.not_before,
            self.valid_until,
        )
        if self.current_epoch == 0:
            if any(value is not None for value in decision_fields):
                raise InvalidPolicyRecordError(
                    "epoch zero policy head cannot contain decision fields"
                )
        else:
            if any(value is None for value in decision_fields):
                raise InvalidPolicyRecordError(
                    "non-zero policy head requires all decision fields"
                )
            _typed_id("attestation_id", self.attestation_id)
            if not isinstance(self.decision, PolicyDecision):
                raise InvalidPolicyRecordError("decision must be a PolicyDecision")
            _non_empty("policy_version", self.policy_version)
            not_before = _aware_timestamp("not_before", self.not_before)
            valid_until = _aware_timestamp("valid_until", self.valid_until)
            if valid_until <= not_before:
                raise InvalidPolicyRecordError("valid_until must be later than not_before")
        _optional_aware_timestamp("updated_at", self.updated_at)


def require_effective_policy_head(
    head: PolicyDecisionHead,
    *,
    at: str,
    required_policy_version: Optional[str] = None,
) -> None:
    if not isinstance(head, PolicyDecisionHead):
        raise InvalidPolicyRecordError("head must be a PolicyDecisionHead")
    instant = _aware_timestamp("at", at)
    if head.current_epoch == 0 or head.decision != PolicyDecision.APPROVED:
        raise InvalidPolicyRecordError("policy head is not approved")
    if required_policy_version is not None and head.policy_version != required_policy_version:
        raise InvalidPolicyRecordError("policy head version does not satisfy current policy")
    if not (_aware_timestamp("not_before", head.not_before) <= instant):
        raise InvalidPolicyRecordError("policy decision is not active yet")
    if not (instant < _aware_timestamp("valid_until", head.valid_until)):
        raise InvalidPolicyRecordError("policy decision is expired")

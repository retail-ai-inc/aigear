"""Closed records for immutable releases and the service release Saga."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidReleaseRecordError",
    "InvalidReleasePhaseTransitionError",
    "ReleasePhase",
    "validate_release_phase_transition",
    "ReleaseRecord",
    "AliasRecord",
    "ServiceReleaseState",
    "ReleaseOperationRecord",
]


class InvalidReleaseRecordError(ValueError):
    pass


class InvalidReleasePhaseTransitionError(ValueError):
    pass


class ReleasePhase(str, Enum):
    RESERVED = "reserved"
    PREPARING = "preparing"
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    SWITCHING_TRAFFIC = "switching_traffic"
    FINALIZING = "finalizing"
    DRAINING = "draining"
    SUCCEEDED = "succeeded"
    COMPENSATING = "compensating"
    RECONCILING = "reconciling"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


_FORWARD = {
    ReleasePhase.RESERVED: ReleasePhase.PREPARING,
    ReleasePhase.PREPARING: ReleasePhase.DEPLOYING,
    ReleasePhase.DEPLOYING: ReleasePhase.VERIFYING,
    ReleasePhase.VERIFYING: ReleasePhase.SWITCHING_TRAFFIC,
    ReleasePhase.SWITCHING_TRAFFIC: ReleasePhase.FINALIZING,
    ReleasePhase.FINALIZING: ReleasePhase.DRAINING,
    ReleasePhase.DRAINING: ReleasePhase.SUCCEEDED,
}
_NON_TERMINAL = frozenset((*_FORWARD, ReleasePhase.COMPENSATING, ReleasePhase.RECONCILING))
_VALID_TRANSITIONS = {
    phase: frozenset({_FORWARD[phase], ReleasePhase.COMPENSATING, ReleasePhase.RECONCILING})
    for phase in _FORWARD
}
_VALID_TRANSITIONS.update(
    {
        ReleasePhase.COMPENSATING: frozenset(
            {ReleasePhase.ROLLED_BACK, ReleasePhase.RECONCILING}
        ),
        ReleasePhase.RECONCILING: frozenset(
            {
                ReleasePhase.PREPARING,
                ReleasePhase.DEPLOYING,
                ReleasePhase.VERIFYING,
                ReleasePhase.SWITCHING_TRAFFIC,
                ReleasePhase.FINALIZING,
                ReleasePhase.DRAINING,
                ReleasePhase.SUCCEEDED,
                ReleasePhase.COMPENSATING,
                ReleasePhase.ROLLED_BACK,
                ReleasePhase.FAILED,
            }
        ),
        ReleasePhase.SUCCEEDED: frozenset(),
        ReleasePhase.ROLLED_BACK: frozenset(),
        ReleasePhase.FAILED: frozenset(),
    }
)


def validate_release_phase_transition(current: ReleasePhase, target: ReleasePhase) -> None:
    if not isinstance(current, ReleasePhase) or not isinstance(target, ReleasePhase):
        raise InvalidReleasePhaseTransitionError("current and target must be ReleasePhase values")
    if target not in _VALID_TRANSITIONS[current]:
        raise InvalidReleasePhaseTransitionError(
            f"illegal release phase transition: {current.value!r} -> {target.value!r}"
        )


def _typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidReleaseRecordError(f"{field_name} must be a TypedId")


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidReleaseRecordError(f"{field_name} must be a non-empty str")


def _positive(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidReleaseRecordError(f"{field_name} must be a positive int")


def _non_negative(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidReleaseRecordError(f"{field_name} must be a non-negative int")


def _optional_timestamp(field_name: str, value: Optional[str]) -> None:
    if value is None:
        return
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidReleaseRecordError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidReleaseRecordError(f"{field_name} must be timezone-aware")
    if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
        raise InvalidReleaseRecordError(
            f"{field_name} must be a canonical UTC timestamp"
        )


def _sorted_typed_ids(field_name: str, value: object) -> Tuple[TypedId, ...]:
    if isinstance(value, list):
        value = tuple(value)
    if not isinstance(value, tuple) or not all(
        isinstance(item, TypedId) for item in value
    ):
        raise InvalidReleaseRecordError(f"{field_name} must be a tuple of TypedId")
    if tuple(item.typed for item in value) != tuple(
        sorted(item.typed for item in value)
    ) or len(set(value)) != len(value):
        raise InvalidReleaseRecordError(f"{field_name} must be sorted and unique")
    return value


@dataclass(frozen=True)
class ReleaseRecord:
    schema_version: str
    environment_fingerprint: TypedId
    release_id: TypedId
    service_name: str
    deployment_target_id: str
    manifest_digest: TypedId
    signature_attestation_id: TypedId
    creation_operation_id: str
    display_version: str
    asset_version_ids: Tuple[TypedId, ...] = ()
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        for field_name in (
            "environment_fingerprint",
            "release_id",
            "manifest_digest",
            "signature_attestation_id",
        ):
            _typed_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self, "service_name", validate_segment(self.service_name, field_name="service_name")
        )
        object.__setattr__(
            self,
            "deployment_target_id",
            validate_segment(self.deployment_target_id, field_name="deployment_target_id"),
        )
        object.__setattr__(
            self,
            "creation_operation_id",
            validate_segment(self.creation_operation_id, field_name="creation_operation_id"),
        )
        object.__setattr__(
            self,
            "display_version",
            validate_segment(self.display_version, field_name="display_version"),
        )
        object.__setattr__(
            self,
            "asset_version_ids",
            _sorted_typed_ids("asset_version_ids", self.asset_version_ids),
        )
        if self.asset_version_ids and self.created_at is None:
            raise InvalidReleaseRecordError(
                "release asset index requires created_at"
            )
        _optional_timestamp("created_at", self.created_at)

    @property
    def immutable_identity(self) -> tuple:
        return (
            self.schema_version,
            self.environment_fingerprint,
            self.release_id,
            self.service_name,
            self.deployment_target_id,
            self.manifest_digest,
            self.signature_attestation_id,
            self.display_version,
            self.asset_version_ids,
        )


@dataclass(frozen=True)
class AliasRecord:
    schema_version: str
    environment_fingerprint: TypedId
    service_name: str
    alias_name: str
    release_id: TypedId
    revision: int
    updated_by_operation_id: str
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        for field_name in ("service_name", "alias_name", "updated_by_operation_id"):
            object.__setattr__(
                self,
                field_name,
                validate_segment(getattr(self, field_name), field_name=field_name),
            )
        _typed_id("release_id", self.release_id)
        _positive("revision", self.revision)
        _optional_timestamp("updated_at", self.updated_at)


@dataclass(frozen=True)
class ServiceReleaseState:
    schema_version: str
    environment_fingerprint: TypedId
    service_name: str
    revision: int
    display_version_counter: int
    desired_release_id: Optional[TypedId] = None
    desired_revision: int = 0
    observed_release_id: Optional[TypedId] = None
    observed_evidence_revision: int = 0
    traffic_release_id: Optional[TypedId] = None
    traffic_k8s_resource_version: Optional[str] = None
    champion_release_id: Optional[TypedId] = None
    previous_release_id: Optional[TypedId] = None
    active_operation_id: Optional[str] = None
    active_operation_phase: Optional[ReleasePhase] = None
    fencing_token: int = 0
    security_watermark: int = 0
    updated_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        object.__setattr__(
            self, "service_name", validate_segment(self.service_name, field_name="service_name")
        )
        _positive("revision", self.revision)
        for field_name in (
            "display_version_counter",
            "desired_revision",
            "observed_evidence_revision",
            "fencing_token",
            "security_watermark",
        ):
            _non_negative(field_name, getattr(self, field_name))
        for field_name in (
            "desired_release_id",
            "observed_release_id",
            "traffic_release_id",
            "champion_release_id",
            "previous_release_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _typed_id(field_name, value)
        operation_values = (self.active_operation_id, self.active_operation_phase)
        if any(value is not None for value in operation_values) and any(
            value is None for value in operation_values
        ):
            raise InvalidReleaseRecordError(
                "active_operation_id and active_operation_phase must be set together"
            )
        if self.active_operation_id is not None:
            object.__setattr__(
                self,
                "active_operation_id",
                validate_segment(self.active_operation_id, field_name="active_operation_id"),
            )
        if self.active_operation_phase is not None and not isinstance(
            self.active_operation_phase, ReleasePhase
        ):
            raise InvalidReleaseRecordError(
                "active_operation_phase must be a ReleasePhase"
            )
        if self.traffic_release_id is None and self.traffic_k8s_resource_version is not None:
            raise InvalidReleaseRecordError(
                "traffic resourceVersion requires traffic_release_id"
            )
        if self.traffic_release_id is not None:
            _non_empty("traffic_k8s_resource_version", self.traffic_k8s_resource_version)
        if self.champion_release_id is not None and self.traffic_release_id is None:
            raise InvalidReleaseRecordError("champion requires traffic_release_id")
        if self.active_operation_phase == ReleasePhase.SUCCEEDED:
            if (
                self.desired_release_id is None
                or self.desired_release_id != self.observed_release_id
                or self.desired_release_id != self.traffic_release_id
                or self.desired_release_id != self.champion_release_id
            ):
                raise InvalidReleaseRecordError(
                    "succeeded release requires desired, observed, traffic and champion equality"
                )
        _optional_timestamp("updated_at", self.updated_at)


@dataclass(frozen=True)
class ReleaseOperationRecord:
    schema_version: str
    environment_fingerprint: TypedId
    operation_id: str
    idempotency_key_hash: str
    request_fingerprint: TypedId
    service_name: str
    target_release_id: TypedId
    phase: ReleasePhase
    owner_principal: str
    fencing_token: int
    revision: int
    expected_service_revision: int
    lease_expires_at: Optional[str] = None
    expected_deployment_uid: Optional[str] = None
    expected_deployment_resource_version: Optional[str] = None
    expected_service_resource_version: Optional[str] = None
    traffic_evidence_id: Optional[TypedId] = None
    error_class: Optional[str] = None
    error_summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    finished_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        for field_name in ("operation_id", "idempotency_key_hash", "service_name"):
            object.__setattr__(
                self,
                field_name,
                validate_segment(getattr(self, field_name), field_name=field_name),
            )
        _typed_id("request_fingerprint", self.request_fingerprint)
        _typed_id("target_release_id", self.target_release_id)
        if self.traffic_evidence_id is not None:
            _typed_id("traffic_evidence_id", self.traffic_evidence_id)
        if not isinstance(self.phase, ReleasePhase):
            raise InvalidReleaseRecordError("phase must be a ReleasePhase")
        _non_empty("owner_principal", self.owner_principal)
        _non_negative("fencing_token", self.fencing_token)
        _positive("revision", self.revision)
        _positive("expected_service_revision", self.expected_service_revision)
        for field_name in (
            "lease_expires_at",
            "created_at",
            "updated_at",
            "finished_at",
        ):
            _optional_timestamp(field_name, getattr(self, field_name))
        if (self.error_class is None) != (self.error_summary is None):
            raise InvalidReleaseRecordError(
                "error_class and error_summary must both be set or both be None"
            )
        terminal = self.phase in (
            ReleasePhase.SUCCEEDED,
            ReleasePhase.ROLLED_BACK,
            ReleasePhase.FAILED,
        )
        if terminal != (self.finished_at is not None):
            raise InvalidReleaseRecordError(
                "terminal release operations require finished_at and non-terminal forbid it"
            )
        if self.phase in _NON_TERMINAL and self.finished_at is not None:
            raise InvalidReleaseRecordError("non-terminal release cannot set finished_at")

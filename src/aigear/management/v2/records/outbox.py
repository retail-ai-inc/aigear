"""Transactional projection outbox records for Pipeline V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "InvalidOutboxRecordError",
    "ProjectionKind",
    "OutboxStatus",
    "compute_projection_event_id",
    "OutboxEventRecord",
]


class InvalidOutboxRecordError(ValueError):
    pass


class ProjectionKind(str, Enum):
    ASSET_MANIFEST = "asset_manifest"
    COMMITTED_RUN_OUTPUT = "committed_run_output"
    POLICY_DECISION_AUDIT = "policy_decision_audit"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


def compute_projection_event_id(
    kind: ProjectionKind,
    subject_id: TypedId,
    projection_schema_version: str,
    projection_source_revision: int,
    projection_repair_epoch: int,
) -> TypedId:
    payload = [
        "aigear.projection-event.v2",
        kind.value,
        subject_id.typed,
        projection_schema_version,
        projection_source_revision,
        projection_repair_epoch,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def _non_negative(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidOutboxRecordError(f"{field_name} must be a non-negative int")


def _optional_aware_timestamp(field_name: str, value: Optional[str]) -> None:
    if value is None:
        return
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidOutboxRecordError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidOutboxRecordError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class OutboxEventRecord:
    schema_version: str
    event_id: TypedId
    kind: ProjectionKind
    subject_id: TypedId
    projection_schema_version: str
    projection_source_revision: int
    projection_repair_epoch: int
    status: OutboxStatus
    delivery_attempts: int = 0
    delivery_fencing_token: int = 0
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[str] = None
    next_attempt_at: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    delivered_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        parse_schema_version(self.projection_schema_version)
        if not isinstance(self.event_id, TypedId) or not isinstance(self.subject_id, TypedId):
            raise InvalidOutboxRecordError("event_id and subject_id must be TypedId values")
        if not isinstance(self.kind, ProjectionKind):
            raise InvalidOutboxRecordError("kind must be ProjectionKind")
        if not isinstance(self.status, OutboxStatus):
            raise InvalidOutboxRecordError("status must be OutboxStatus")
        _non_negative("projection_source_revision", self.projection_source_revision)
        _non_negative("projection_repair_epoch", self.projection_repair_epoch)
        _non_negative("delivery_attempts", self.delivery_attempts)
        _non_negative("delivery_fencing_token", self.delivery_fencing_token)
        for field_name in (
            "lease_expires_at",
            "next_attempt_at",
            "created_at",
            "delivered_at",
        ):
            _optional_aware_timestamp(field_name, getattr(self, field_name))
        expected = compute_projection_event_id(
            self.kind,
            self.subject_id,
            self.projection_schema_version,
            self.projection_source_revision,
            self.projection_repair_epoch,
        )
        if expected != self.event_id:
            raise InvalidOutboxRecordError("event_id does not match the projection event identity")
        lease_values = (self.lease_owner, self.lease_expires_at)
        if any(value is not None for value in lease_values) and any(
            value is None for value in lease_values
        ):
            raise InvalidOutboxRecordError(
                "lease_owner and lease_expires_at must be set together"
            )
        if self.status == OutboxStatus.DELIVERING and self.lease_owner is None:
            raise InvalidOutboxRecordError("delivering outbox events require an active lease")
        if self.status != OutboxStatus.DELIVERING and self.lease_owner is not None:
            raise InvalidOutboxRecordError("only delivering outbox events may hold a lease")
        if self.status == OutboxStatus.FAILED and self.next_attempt_at is None:
            raise InvalidOutboxRecordError("failed outbox events require next_attempt_at")
        if self.status in (
            OutboxStatus.DELIVERING,
            OutboxStatus.DELIVERED,
            OutboxStatus.DEAD_LETTER,
        ) and self.next_attempt_at is not None:
            raise InvalidOutboxRecordError(
                f"{self.status.value} outbox events cannot retain next_attempt_at"
            )
        if self.status == OutboxStatus.DELIVERED and self.delivered_at is None:
            raise InvalidOutboxRecordError("delivered outbox events require delivered_at")
        if self.status != OutboxStatus.DELIVERED and self.delivered_at is not None:
            raise InvalidOutboxRecordError("only delivered outbox events may set delivered_at")

    @property
    def immutable_identity(self) -> tuple:
        return (
            self.schema_version,
            self.event_id,
            self.kind,
            self.subject_id,
            self.projection_schema_version,
            self.projection_source_revision,
            self.projection_repair_epoch,
        )

    @classmethod
    def pending(
        cls,
        *,
        schema_version: str,
        kind: ProjectionKind,
        subject_id: TypedId,
        projection_schema_version: str,
        projection_source_revision: int,
        projection_repair_epoch: int = 0,
        created_at: Optional[str] = None,
    ) -> "OutboxEventRecord":
        event_id = compute_projection_event_id(
            kind,
            subject_id,
            projection_schema_version,
            projection_source_revision,
            projection_repair_epoch,
        )
        return cls(
            schema_version=schema_version,
            event_id=event_id,
            kind=kind,
            subject_id=subject_id,
            projection_schema_version=projection_schema_version,
            projection_source_revision=projection_source_revision,
            projection_repair_epoch=projection_repair_epoch,
            status=OutboxStatus.PENDING,
            next_attempt_at=created_at,
            created_at=created_at,
        )

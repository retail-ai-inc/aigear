"""Closed records for the Phase C external-import lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidImportRecordError",
    "InvalidImportPhaseTransitionError",
    "ImportPhase",
    "validate_import_phase_transition",
    "ExactImportSource",
    "ImportControlSnapshot",
    "ImportTicketRecord",
    "ImportCompletionRecord",
    "ImportOperationRecord",
]


class InvalidImportRecordError(ValueError):
    """Raised when an import lifecycle record is malformed."""


class InvalidImportPhaseTransitionError(ValueError):
    """Raised when an import operation attempts an illegal phase transition."""


class ImportPhase(str, Enum):
    RESERVED = "reserved"
    TICKETED = "ticketed"
    QUARANTINING = "quarantining"
    INSPECTING = "inspecting"
    PREPARING = "preparing"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    COMPENSATING = "compensating"
    RECONCILING = "reconciling"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ACTIVE_PHASES = (
    ImportPhase.RESERVED,
    ImportPhase.TICKETED,
    ImportPhase.QUARANTINING,
    ImportPhase.INSPECTING,
    ImportPhase.PREPARING,
    ImportPhase.COMMITTING,
)
_VALID_PHASE_TRANSITIONS = {
    ImportPhase.RESERVED: frozenset(
        {ImportPhase.TICKETED, ImportPhase.COMPENSATING, ImportPhase.RECONCILING}
    ),
    ImportPhase.TICKETED: frozenset(
        {ImportPhase.QUARANTINING, ImportPhase.COMPENSATING, ImportPhase.RECONCILING}
    ),
    ImportPhase.QUARANTINING: frozenset(
        {ImportPhase.INSPECTING, ImportPhase.COMPENSATING, ImportPhase.RECONCILING}
    ),
    ImportPhase.INSPECTING: frozenset(
        {ImportPhase.PREPARING, ImportPhase.COMPENSATING, ImportPhase.RECONCILING}
    ),
    ImportPhase.PREPARING: frozenset(
        {ImportPhase.COMMITTING, ImportPhase.COMPENSATING, ImportPhase.RECONCILING}
    ),
    ImportPhase.COMMITTING: frozenset(
        {ImportPhase.SUCCEEDED, ImportPhase.COMPENSATING, ImportPhase.RECONCILING}
    ),
    ImportPhase.COMPENSATING: frozenset(
        {ImportPhase.FAILED, ImportPhase.CANCELLED, ImportPhase.RECONCILING}
    ),
    ImportPhase.RECONCILING: frozenset(
        {
            ImportPhase.TICKETED,
            ImportPhase.QUARANTINING,
            ImportPhase.INSPECTING,
            ImportPhase.PREPARING,
            ImportPhase.COMMITTING,
            ImportPhase.SUCCEEDED,
            ImportPhase.COMPENSATING,
            ImportPhase.FAILED,
            ImportPhase.CANCELLED,
        }
    ),
    ImportPhase.SUCCEEDED: frozenset(),
    ImportPhase.FAILED: frozenset(),
    ImportPhase.CANCELLED: frozenset(),
}


def validate_import_phase_transition(current: ImportPhase, target: ImportPhase) -> None:
    if not isinstance(current, ImportPhase) or not isinstance(target, ImportPhase):
        raise InvalidImportPhaseTransitionError("current and target must be ImportPhase values")
    if target not in _VALID_PHASE_TRANSITIONS[current]:
        raise InvalidImportPhaseTransitionError(
            f"illegal import phase transition: {current.value!r} -> {target.value!r}"
        )


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidImportRecordError(f"{field_name} must be a non-empty str")


def _positive(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidImportRecordError(f"{field_name} must be a positive int")


def _non_negative(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidImportRecordError(f"{field_name} must be a non-negative int")


def _typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidImportRecordError(f"{field_name} must be a TypedId")


def _aware_timestamp(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidImportRecordError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidImportRecordError(f"{field_name} must be timezone-aware")
    return parsed


def _optional_aware_timestamp(field_name: str, value: Optional[str]) -> None:
    if value is not None:
        _aware_timestamp(field_name, value)


@dataclass(frozen=True)
class ExactImportSource:
    environment_id: str
    project_id: str
    bucket: str
    object_name: str
    generation: str
    region: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment_id",
            validate_segment(self.environment_id, field_name="source.environment_id"),
        )
        object.__setattr__(
            self, "project_id", validate_segment(self.project_id, field_name="source.project_id")
        )
        object.__setattr__(
            self, "bucket", validate_segment(self.bucket, field_name="source.bucket")
        )
        _non_empty("source.object_name", self.object_name)
        if self.object_name.startswith("/") or "\x00" in self.object_name:
            raise InvalidImportRecordError(
                "source.object_name must be a relative, non-NUL object name"
            )
        if not isinstance(self.generation, str) or not self.generation.isascii():
            raise InvalidImportRecordError("source.generation must be an ASCII decimal string")
        if not self.generation.isdigit() or int(self.generation) < 1:
            raise InvalidImportRecordError("source.generation must be a positive decimal string")
        object.__setattr__(
            self, "region", validate_segment(self.region, field_name="source.region")
        )
        _non_negative("source.size_bytes", self.size_bytes)


@dataclass(frozen=True)
class ImportControlSnapshot:
    environment_id: str
    environment_fingerprint: TypedId
    firestore_database_id: str
    registry_binding_id: str
    registry_binding_epoch: int
    write_epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment_id",
            validate_segment(self.environment_id, field_name="control.environment_id"),
        )
        _typed_id("control.environment_fingerprint", self.environment_fingerprint)
        _non_empty("control.firestore_database_id", self.firestore_database_id)
        _non_empty("control.registry_binding_id", self.registry_binding_id)
        _positive("control.registry_binding_epoch", self.registry_binding_epoch)
        _positive("control.write_epoch", self.write_epoch)


@dataclass(frozen=True)
class ImportTicketRecord:
    schema_version: str
    ticket_digest: TypedId
    operation_id: str
    request_fingerprint: TypedId
    source: ExactImportSource
    target_environment_id: str
    target_quarantine_prefix: str
    audience: str
    executor_principal: str
    fencing_token: int
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _typed_id("ticket_digest", self.ticket_digest)
        object.__setattr__(
            self, "operation_id", validate_segment(self.operation_id, field_name="operation_id")
        )
        _typed_id("request_fingerprint", self.request_fingerprint)
        if not isinstance(self.source, ExactImportSource):
            raise InvalidImportRecordError("source must be an ExactImportSource")
        object.__setattr__(
            self,
            "target_environment_id",
            validate_segment(self.target_environment_id, field_name="target_environment_id"),
        )
        if self.source.environment_id != self.target_environment_id:
            raise InvalidImportRecordError(
                "source and target environment_id must match for Phase C imports"
            )
        _non_empty("target_quarantine_prefix", self.target_quarantine_prefix)
        if not self.target_quarantine_prefix.endswith("/"):
            raise InvalidImportRecordError("target_quarantine_prefix must end with '/'")
        _non_empty("audience", self.audience)
        _non_empty("executor_principal", self.executor_principal)
        _non_negative("fencing_token", self.fencing_token)
        issued_at = _aware_timestamp("issued_at", self.issued_at)
        expires_at = _aware_timestamp("expires_at", self.expires_at)
        if expires_at <= issued_at:
            raise InvalidImportRecordError("expires_at must be later than issued_at")


@dataclass(frozen=True)
class ImportCompletionRecord:
    schema_version: str
    operation_id: str
    ticket_digest: TypedId
    environment_fingerprint: TypedId
    executor_principal: str
    fencing_token: int
    payload_set_digest: TypedId
    completed_at: str

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        object.__setattr__(
            self, "operation_id", validate_segment(self.operation_id, field_name="operation_id")
        )
        _typed_id("ticket_digest", self.ticket_digest)
        _typed_id("environment_fingerprint", self.environment_fingerprint)
        _non_empty("executor_principal", self.executor_principal)
        _non_negative("fencing_token", self.fencing_token)
        _typed_id("payload_set_digest", self.payload_set_digest)
        _aware_timestamp("completed_at", self.completed_at)


@dataclass(frozen=True)
class ImportOperationRecord:
    schema_version: str
    operation_id: str
    idempotency_key_hash: str
    request_fingerprint: TypedId
    source: ExactImportSource
    target_environment_id: str
    target_quarantine_prefix: str
    control_snapshot: ImportControlSnapshot
    owner_principal: str
    write_budget: int
    fencing_token: int
    phase: ImportPhase
    revision: int
    lease_expires_at: Optional[str] = None
    ticket: Optional[ImportTicketRecord] = None
    completion: Optional[ImportCompletionRecord] = None
    result_asset_version_id: Optional[TypedId] = None
    error_class: Optional[str] = None
    error_summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    finished_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        object.__setattr__(
            self, "operation_id", validate_segment(self.operation_id, field_name="operation_id")
        )
        object.__setattr__(
            self,
            "idempotency_key_hash",
            validate_segment(self.idempotency_key_hash, field_name="idempotency_key_hash"),
        )
        _typed_id("request_fingerprint", self.request_fingerprint)
        if not isinstance(self.source, ExactImportSource):
            raise InvalidImportRecordError("source must be an ExactImportSource")
        object.__setattr__(
            self,
            "target_environment_id",
            validate_segment(self.target_environment_id, field_name="target_environment_id"),
        )
        if self.source.environment_id != self.target_environment_id:
            raise InvalidImportRecordError(
                "source and target environment_id must match for Phase C imports"
            )
        _non_empty("target_quarantine_prefix", self.target_quarantine_prefix)
        if not self.target_quarantine_prefix.endswith("/"):
            raise InvalidImportRecordError("target_quarantine_prefix must end with '/'")
        if not isinstance(self.control_snapshot, ImportControlSnapshot):
            raise InvalidImportRecordError("control_snapshot must be an ImportControlSnapshot")
        if self.control_snapshot.environment_id != self.target_environment_id:
            raise InvalidImportRecordError(
                "control snapshot environment_id must match target_environment_id"
            )
        _non_empty("owner_principal", self.owner_principal)
        _positive("write_budget", self.write_budget)
        if self.write_budget >= 500:
            raise InvalidImportRecordError("write_budget must be below Firestore's 500-write limit")
        _non_negative("fencing_token", self.fencing_token)
        if not isinstance(self.phase, ImportPhase):
            raise InvalidImportRecordError("phase must be an ImportPhase")
        _positive("revision", self.revision)
        for field_name in (
            "lease_expires_at",
            "created_at",
            "updated_at",
            "finished_at",
        ):
            _optional_aware_timestamp(field_name, getattr(self, field_name))
        if (self.error_class is None) != (self.error_summary is None):
            raise InvalidImportRecordError(
                "error_class and error_summary must both be set or both be None"
            )
        if self.result_asset_version_id is not None:
            _typed_id("result_asset_version_id", self.result_asset_version_id)
        self._validate_ticket()
        self._validate_completion()
        if self.phase == ImportPhase.SUCCEEDED and self.result_asset_version_id is None:
            raise InvalidImportRecordError(
                "succeeded imports require result_asset_version_id"
            )
        if self.phase != ImportPhase.SUCCEEDED and self.result_asset_version_id is not None:
            raise InvalidImportRecordError(
                "only succeeded imports may set result_asset_version_id"
            )
        terminal = self.phase in (
            ImportPhase.SUCCEEDED,
            ImportPhase.FAILED,
            ImportPhase.CANCELLED,
        )
        if terminal != (self.finished_at is not None):
            raise InvalidImportRecordError(
                "terminal imports require finished_at and non-terminal imports forbid it"
            )
        if self.phase in _ACTIVE_PHASES and self.error_class is not None:
            raise InvalidImportRecordError("active imports cannot retain terminal error fields")

    def _validate_ticket(self) -> None:
        if self.ticket is None:
            if self.phase not in (
                ImportPhase.RESERVED,
                ImportPhase.COMPENSATING,
                ImportPhase.RECONCILING,
                ImportPhase.FAILED,
                ImportPhase.CANCELLED,
            ):
                raise InvalidImportRecordError(f"{self.phase.value} imports require a ticket")
            return
        if not isinstance(self.ticket, ImportTicketRecord):
            raise InvalidImportRecordError("ticket must be an ImportTicketRecord")
        if (
            self.ticket.operation_id != self.operation_id
            or self.ticket.request_fingerprint != self.request_fingerprint
            or self.ticket.source != self.source
            or self.ticket.target_environment_id != self.target_environment_id
            or self.ticket.target_quarantine_prefix != self.target_quarantine_prefix
            or self.ticket.fencing_token != self.fencing_token
        ):
            raise InvalidImportRecordError("ticket is not bound to this import operation")

    def _validate_completion(self) -> None:
        if self.completion is None:
            if self.phase in (
                ImportPhase.INSPECTING,
                ImportPhase.PREPARING,
                ImportPhase.COMMITTING,
                ImportPhase.SUCCEEDED,
            ):
                raise InvalidImportRecordError(f"{self.phase.value} imports require completion")
            return
        if self.ticket is None:
            raise InvalidImportRecordError("completion requires a ticket")
        if not isinstance(self.completion, ImportCompletionRecord):
            raise InvalidImportRecordError("completion must be an ImportCompletionRecord")
        if (
            self.completion.operation_id != self.operation_id
            or self.completion.ticket_digest != self.ticket.ticket_digest
            or self.completion.environment_fingerprint
            != self.control_snapshot.environment_fingerprint
            or self.completion.executor_principal != self.ticket.executor_principal
            or self.completion.fencing_token != self.fencing_token
        ):
            raise InvalidImportRecordError("completion is not bound to this import operation")

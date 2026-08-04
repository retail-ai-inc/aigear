"""Operation state machine skeleton (spec section 10.2).

An Operation is the idempotent envelope for one long-running side-effecting
action (run trigger, finalize, import, restore, release, export, ...); the
random ``operation_id`` is not the idempotency key -- callers must reuse a
stable ``idempotency_key`` across retries, and its digest becomes the
Firestore document ID (see :meth:`aigear.management.v2.firestore_paths.
FirestorePathsV2.operation_document`).

This module models only the shared phase state machine and the minimal
"at least" field set from spec 10.2. ``operation_type`` is deliberately left
as an open, non-empty string rather than a closed enum: the spec describes
several independent operation families (pipeline finalize, run trigger,
external import, restore, release, export, ...) without giving one shared
closed vocabulary, and inventing one here would be a guess. Tighten it once
a concrete operation family is implemented and its exact type strings are
fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidOperationRecordError",
    "InvalidOperationPhaseTransitionError",
    "OperationPhase",
    "validate_operation_phase_transition",
    "OperationRecord",
]


class InvalidOperationRecordError(ValueError):
    """Raised when an OperationRecord field is malformed."""


class InvalidOperationPhaseTransitionError(ValueError):
    """Raised when an Operation ``phase`` transition is not allowed (spec 10.2)."""


class OperationPhase(str, Enum):
    RESERVED = "reserved"
    STAGING = "staging"
    UPLOADED = "uploaded"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    MANUAL_INTERVENTION = "manual_intervention"


_VALID_PHASE_TRANSITIONS = {
    OperationPhase.RESERVED: frozenset({OperationPhase.STAGING, OperationPhase.FAILED}),
    OperationPhase.STAGING: frozenset({OperationPhase.UPLOADED, OperationPhase.FAILED}),
    OperationPhase.UPLOADED: frozenset({OperationPhase.FINALIZING, OperationPhase.FAILED}),
    OperationPhase.FINALIZING: frozenset({OperationPhase.SUCCEEDED, OperationPhase.FAILED}),
    OperationPhase.FAILED: frozenset({OperationPhase.COMPENSATING}),
    OperationPhase.COMPENSATING: frozenset(
        {OperationPhase.COMPENSATED, OperationPhase.MANUAL_INTERVENTION}
    ),
    OperationPhase.SUCCEEDED: frozenset(),
    OperationPhase.COMPENSATED: frozenset(),
    OperationPhase.MANUAL_INTERVENTION: frozenset(),
}


def validate_operation_phase_transition(current: OperationPhase, target: OperationPhase) -> None:
    """Reject any edge not in spec 10.2; terminal operations never reopen."""
    allowed = _VALID_PHASE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidOperationPhaseTransitionError(
            f"illegal Operation phase transition: {current.value!r} -> {target.value!r}"
        )


def _require_non_empty_str(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidOperationRecordError(f"{field_name} must be a non-empty str, got {value!r}")


def _require_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidOperationRecordError(f"{field_name} must be a positive int, got {value!r}")


def _require_non_negative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidOperationRecordError(
            f"{field_name} must be a non-negative int, got {value!r}"
        )


@dataclass(frozen=True)
class OperationRecord:
    """The minimal "at least" Operation field set from spec 10.2.

    ``run_id``/``step_name``/``attempt_no``/``output_name`` are optional
    because not every operation family references a run (e.g. an external
    import operation has none).
    """

    idempotency_key_hash: str
    request_fingerprint: str
    operation_type: str
    owner_principal: str
    write_epoch: int
    fencing_token: int
    phase: OperationPhase
    revision: int
    run_id: Optional[str] = None
    step_name: Optional[str] = None
    attempt_no: Optional[int] = None
    output_name: Optional[str] = None
    lease_expires_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    last_error: Optional[str] = None
    error_class: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    finished_at: Optional[str] = None
    firestore_database_id: Optional[str] = None
    firestore_database_resource: Optional[str] = None
    registry_binding_id: Optional[str] = None
    registry_binding_epoch: Optional[int] = None
    security_watermark: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "idempotency_key_hash",
            validate_segment(self.idempotency_key_hash, field_name="idempotency_key_hash"),
        )
        _require_non_empty_str("request_fingerprint", self.request_fingerprint)
        _require_non_empty_str("operation_type", self.operation_type)
        _require_non_empty_str("owner_principal", self.owner_principal)
        _require_positive_int("write_epoch", self.write_epoch)
        _require_non_negative_int("fencing_token", self.fencing_token)
        if not isinstance(self.phase, OperationPhase):
            raise InvalidOperationRecordError(f"phase must be an OperationPhase, got {self.phase!r}")
        _require_positive_int("revision", self.revision)

        if self.run_id is not None:
            object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        if self.step_name is not None:
            object.__setattr__(
                self, "step_name", validate_segment(self.step_name, field_name="step_name")
            )
        if self.attempt_no is not None:
            _require_positive_int("attempt_no", self.attempt_no)
        if self.output_name is not None:
            object.__setattr__(
                self, "output_name", validate_segment(self.output_name, field_name="output_name")
            )

        if (self.last_error is None) != (self.error_class is None):
            raise InvalidOperationRecordError(
                "last_error and error_class must both be set or both be None"
            )
        binding_values = (
            self.firestore_database_id,
            self.registry_binding_id,
            self.registry_binding_epoch,
        )
        if any(value is not None for value in binding_values) and any(
            value is None for value in binding_values
        ):
            raise InvalidOperationRecordError(
                "firestore_database_id and registry_binding_id/epoch must be set together"
            )
        if self.firestore_database_id is not None:
            _require_non_empty_str("firestore_database_id", self.firestore_database_id)
            _require_non_empty_str("registry_binding_id", self.registry_binding_id)
            _require_positive_int("registry_binding_epoch", self.registry_binding_epoch)
        if self.firestore_database_resource is not None:
            _require_non_empty_str("firestore_database_resource", self.firestore_database_resource)
        if self.security_watermark is not None:
            _require_non_negative_int("security_watermark", self.security_watermark)

"""Run / Step / Attempt state machine skeleton (spec section 9.1).

This is intentionally a minimal skeleton: identity + status only. The full
``RunSpec`` (trigger principal, graph/code/config digests, seed inputs, output
slot declarations, retry/cancel policy -- spec 9.2) and the lease/fencing
machinery (spec 10.3) are Phase B scope and are not modeled here.

Transition sets below are a literal reading of the ASCII state diagrams in
spec 9.1. The Step and Attempt diagrams use a "joined vertical bar" drawing
convention (see the Attempt diagram, where a single horizontal line spans the
``running``/``committing`` columns before terminating in ``-> failed``) to
show that either predecessor state can feed into the same successor; the
column alignment was read carefully but is a shared/ambiguous ASCII
convention, so double-check against spec 9.1 directly before relying on any
edge here for Phase B's real lease/fencing implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidRunRecordError",
    "InvalidRunStatusTransitionError",
    "InvalidStepStatusTransitionError",
    "InvalidAttemptStatusTransitionError",
    "RunStatus",
    "StepStatus",
    "AttemptStatus",
    "validate_run_status_transition",
    "validate_step_status_transition",
    "validate_attempt_status_transition",
    "RunRecord",
    "StepRecord",
    "AttemptRecord",
]


class InvalidRunRecordError(ValueError):
    """Raised when a Run/Step/Attempt record field is malformed."""


class InvalidRunStatusTransitionError(ValueError):
    """Raised when a Run ``status`` transition is not allowed (spec 9.1)."""


class InvalidStepStatusTransitionError(ValueError):
    """Raised when a Step ``status`` transition is not allowed (spec 9.1)."""


class InvalidAttemptStatusTransitionError(ValueError):
    """Raised when an Attempt ``status`` transition is not allowed (spec 9.1)."""


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    BLOCKED = "blocked"
    READY = "ready"
    LEASED = "leased"
    RUNNING = "running"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    LEASED = "leased"
    RUNNING = "running"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_VALID_RUN_TRANSITIONS = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLING}
    ),
    RunStatus.CANCELLING: frozenset({RunStatus.CANCELLED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

_VALID_STEP_TRANSITIONS = {
    StepStatus.BLOCKED: frozenset({StepStatus.READY}),
    StepStatus.READY: frozenset({StepStatus.LEASED}),
    StepStatus.LEASED: frozenset({StepStatus.RUNNING}),
    StepStatus.RUNNING: frozenset(
        {StepStatus.COMMITTING, StepStatus.RETRY_WAIT, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.COMMITTING: frozenset({StepStatus.SUCCEEDED, StepStatus.RETRY_WAIT}),
    StepStatus.RETRY_WAIT: frozenset({StepStatus.READY}),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.FAILED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}

_VALID_ATTEMPT_TRANSITIONS = {
    AttemptStatus.LEASED: frozenset(
        {AttemptStatus.RUNNING, AttemptStatus.EXPIRED, AttemptStatus.CANCELLED}
    ),
    AttemptStatus.RUNNING: frozenset({AttemptStatus.COMMITTING, AttemptStatus.FAILED}),
    AttemptStatus.COMMITTING: frozenset({AttemptStatus.SUCCEEDED, AttemptStatus.FAILED}),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.FAILED: frozenset(),
    AttemptStatus.EXPIRED: frozenset(),
    AttemptStatus.CANCELLED: frozenset(),
}


def validate_run_status_transition(current: RunStatus, target: RunStatus) -> None:
    allowed = _VALID_RUN_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidRunStatusTransitionError(
            f"illegal Run status transition: {current.value!r} -> {target.value!r}"
        )


def validate_step_status_transition(current: StepStatus, target: StepStatus) -> None:
    allowed = _VALID_STEP_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidStepStatusTransitionError(
            f"illegal Step status transition: {current.value!r} -> {target.value!r}"
        )


def validate_attempt_status_transition(current: AttemptStatus, target: AttemptStatus) -> None:
    allowed = _VALID_ATTEMPT_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidAttemptStatusTransitionError(
            f"illegal Attempt status transition: {current.value!r} -> {target.value!r}"
        )


def _require_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidRunRecordError(f"{field_name} must be a positive int, got {value!r}")


def _require_non_negative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidRunRecordError(f"{field_name} must be a non-negative int, got {value!r}")


@dataclass(frozen=True)
class RunRecord:
    """Minimal Run identity + status (full RunSpec is Phase B scope)."""

    run_id: str
    status: RunStatus

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        if not isinstance(self.status, RunStatus):
            raise InvalidRunRecordError(f"status must be a RunStatus, got {self.status!r}")


@dataclass(frozen=True)
class StepRecord:
    """Minimal Step identity + status + current attempt pointer."""

    run_id: str
    step_name: str
    status: StepStatus
    current_attempt_no: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        object.__setattr__(
            self, "step_name", validate_segment(self.step_name, field_name="step_name")
        )
        if not isinstance(self.status, StepStatus):
            raise InvalidRunRecordError(f"status must be a StepStatus, got {self.status!r}")
        if self.current_attempt_no is not None:
            _require_positive_int("current_attempt_no", self.current_attempt_no)


@dataclass(frozen=True)
class AttemptRecord:
    """Minimal Attempt identity + status + fencing token."""

    run_id: str
    step_name: str
    attempt_no: int
    status: AttemptStatus
    fencing_token: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        object.__setattr__(
            self, "step_name", validate_segment(self.step_name, field_name="step_name")
        )
        _require_positive_int("attempt_no", self.attempt_no)
        if not isinstance(self.status, AttemptStatus):
            raise InvalidRunRecordError(f"status must be an AttemptStatus, got {self.status!r}")
        _require_non_negative_int("fencing_token", self.fencing_token)

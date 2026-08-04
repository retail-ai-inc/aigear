"""Run / Step / Attempt records (spec sections 9.1/9.2/10.3).

T9 (Phase A) modeled only identity + status. This module additionally
carries the RunSpec linkage and lease/fencing fields spec 9.2/10.3 require
once a Run actually executes: ``RunRecord.run_spec_digest``/
``remaining_required_steps``/``parent_run_id``/``backfill_of``;
``StepRecord.resolved_inputs_digest``/``resolved_at``/``source_step_revision``
(``current_attempt_no`` already existed); ``AttemptRecord.owner_principal``/
``lease_expires_at``/``heartbeat_at`` (``fencing_token`` already existed).
All of the new fields default to ``None``/``()`` so existing T9 construction
sites keep working unchanged.

T28 (spec 24.1's ``resolve_inputs``) adds ``StepRecord.resolved_inputs``: spec
9.2 says a Step's symbolic dependency resolution must save both
``resolved_inputs`` and ``resolved_inputs_digest``, but only the digest was
modeled here originally -- the actual sealed
:class:`~aigear.management.v2.records.occurrence.ResolvedInputBinding` tuple
is needed later, unchanged, as the input to ``acquire_step_lease`` (T20) and
the eventual committed Occurrence (T22), so it must be retrievable from the
Step record rather than recomputed.

Transition sets below are a literal reading of the ASCII state diagrams in
spec 9.1. The Step and Attempt diagrams use a "joined vertical bar" drawing
convention (see the Attempt diagram, where a single horizontal line spans the
``running``/``committing`` columns before terminating in ``-> failed``) to
show that either predecessor state can feed into the same successor; the
column alignment was read carefully but is a shared/ambiguous ASCII
convention, so double-check against spec 9.1 directly before relying on any
edge here for Phase B's real lease/fencing implementation.

Phase B's real lease/fencing implementation (T20, spec 10.3 step 4) found
exactly the gap that note warned about: the diagram only literally draws
``leased -> expired``, but 10.3's prose is unambiguous that *any* takeover --
including one that supersedes an Attempt already ``running`` -- must set the
superseded Attempt to ``expired`` in the same transaction. ``running ->
expired`` is therefore added to ``_VALID_ATTEMPT_TRANSITIONS`` on top of the
diagram's literal edges, since the prose is more specific than the diagram
and directly describes this operational requirement. The same reasoning adds
``running -> leased`` to ``_VALID_STEP_TRANSITIONS``: taking over a
``running`` Step's lease creates a fresh Attempt that starts at ``leased``,
so the Step must be able to move back there too.

Run cancellation (T23, spec 10.5) found the same kind of gap again: the
diagram only draws ``running -> cancelled`` for Step and
``leased -> cancelled`` for Attempt, but 10.5's cancel flow must terminate
every Step of a Run, and most Steps in a real DAG are still
``blocked``/``ready``/``retry_wait`` (never leased) when a cancel request
arrives. ``cancelled`` is therefore added as a valid target from
``blocked``/``ready``/``leased``/``retry_wait`` in
``_VALID_STEP_TRANSITIONS``, and from ``running`` in
``_VALID_ATTEMPT_TRANSITIONS`` (mirroring the existing
``leased -> cancelled`` edge). ``committing`` is deliberately left out of
both: it is only ever set transiently inside a single synchronous
``finalize_step_outputs`` call (T22), which always advances it further to
``succeeded`` before returning, so it is never observed at rest by a
concurrent cancel.

T28's ``fail_attempt`` wiring (spec 24.1) found one more instance of the
same pattern: today nothing in this package ever moves an Attempt from
``leased`` to ``running`` (``finalize_step_outputs`` already treats
``leased``/``running``/``committing`` as equally "still owns execution",
spec 10.4), so a worker that dies before finalizing is stuck ``leased``
forever unless ``leased -> failed`` also exists directly -- the diagram's
literal ``running``/``committing -> failed`` edge alone is unreachable in
that (common) case. ``leased -> failed`` is therefore added to
``_VALID_ATTEMPT_TRANSITIONS``, mirroring the existing ``leased ->
expired``/``leased -> cancelled`` edges it sits next to. The Step side
needs the matching ``leased -> failed``/``leased -> retry_wait`` (today
only reachable from ``running``) for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.occurrence import ResolvedInputBinding

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
    StepStatus.BLOCKED: frozenset({StepStatus.READY, StepStatus.CANCELLED}),
    StepStatus.READY: frozenset({StepStatus.LEASED, StepStatus.CANCELLED}),
    StepStatus.LEASED: frozenset(
        {StepStatus.RUNNING, StepStatus.RETRY_WAIT, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.RUNNING: frozenset(
        {
            StepStatus.COMMITTING,
            StepStatus.RETRY_WAIT,
            StepStatus.FAILED,
            StepStatus.CANCELLED,
            StepStatus.LEASED,
        }
    ),
    StepStatus.COMMITTING: frozenset({StepStatus.SUCCEEDED, StepStatus.RETRY_WAIT}),
    StepStatus.RETRY_WAIT: frozenset({StepStatus.READY, StepStatus.CANCELLED}),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.FAILED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}

_VALID_ATTEMPT_TRANSITIONS = {
    AttemptStatus.LEASED: frozenset(
        {AttemptStatus.RUNNING, AttemptStatus.FAILED, AttemptStatus.EXPIRED, AttemptStatus.CANCELLED}
    ),
    AttemptStatus.RUNNING: frozenset(
        {
            AttemptStatus.COMMITTING,
            AttemptStatus.FAILED,
            AttemptStatus.EXPIRED,
            AttemptStatus.CANCELLED,
        }
    ),
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
    """Run identity + status, plus its RunSpec linkage (spec 9.2)."""

    run_id: str
    status: RunStatus
    run_spec_digest: Optional[TypedId] = None
    remaining_required_steps: Optional[int] = None
    parent_run_id: Optional[str] = None
    backfill_of: Optional[str] = None
    failure_reason: Optional[str] = None
    cancel_reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        if not isinstance(self.status, RunStatus):
            raise InvalidRunRecordError(f"status must be a RunStatus, got {self.status!r}")
        if self.run_spec_digest is not None and not isinstance(self.run_spec_digest, TypedId):
            raise InvalidRunRecordError(
                f"run_spec_digest must be a TypedId or None, got {type(self.run_spec_digest)!r}"
            )
        if self.remaining_required_steps is not None:
            _require_non_negative_int("remaining_required_steps", self.remaining_required_steps)
        if self.parent_run_id is not None:
            object.__setattr__(
                self, "parent_run_id", validate_segment(self.parent_run_id, field_name="parent_run_id")
            )
        if self.backfill_of is not None:
            object.__setattr__(
                self, "backfill_of", validate_segment(self.backfill_of, field_name="backfill_of")
            )


@dataclass(frozen=True)
class StepRecord:
    """Step identity + status + current attempt pointer + resolved inputs (spec 9.2)."""

    run_id: str
    step_name: str
    status: StepStatus
    current_attempt_no: Optional[int] = None
    resolved_inputs: Tuple[ResolvedInputBinding, ...] = ()
    resolved_inputs_digest: Optional[TypedId] = None
    resolved_at: Optional[str] = None
    source_step_revision: Optional[int] = None
    next_attempt_at: Optional[str] = None
    failure_reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        object.__setattr__(
            self, "step_name", validate_segment(self.step_name, field_name="step_name")
        )
        if not isinstance(self.status, StepStatus):
            raise InvalidRunRecordError(f"status must be a StepStatus, got {self.status!r}")
        if self.current_attempt_no is not None:
            _require_positive_int("current_attempt_no", self.current_attempt_no)
        if isinstance(self.resolved_inputs, list):
            object.__setattr__(self, "resolved_inputs", tuple(self.resolved_inputs))
        if not all(isinstance(binding, ResolvedInputBinding) for binding in self.resolved_inputs):
            raise InvalidRunRecordError("resolved_inputs must be a sequence of ResolvedInputBinding")
        if self.resolved_inputs_digest is not None and not isinstance(
            self.resolved_inputs_digest, TypedId
        ):
            raise InvalidRunRecordError(
                "resolved_inputs_digest must be a TypedId or None, got "
                f"{type(self.resolved_inputs_digest)!r}"
            )
        if self.source_step_revision is not None:
            _require_positive_int("source_step_revision", self.source_step_revision)


@dataclass(frozen=True)
class AttemptRecord:
    """Attempt identity + status + fencing token + lease bookkeeping (spec 10.3)."""

    run_id: str
    step_name: str
    attempt_no: int
    status: AttemptStatus
    fencing_token: int
    owner_principal: Optional[str] = None
    lease_expires_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    failure_reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        object.__setattr__(
            self, "step_name", validate_segment(self.step_name, field_name="step_name")
        )
        _require_positive_int("attempt_no", self.attempt_no)
        if not isinstance(self.status, AttemptStatus):
            raise InvalidRunRecordError(f"status must be an AttemptStatus, got {self.status!r}")
        _require_non_negative_int("fencing_token", self.fencing_token)
        if self.owner_principal is not None and (
            not isinstance(self.owner_principal, str) or not self.owner_principal
        ):
            raise InvalidRunRecordError(
                f"owner_principal must be a non-empty str or None, got {self.owner_principal!r}"
            )

"""Explicit Attempt failure (spec section 24.1's ``PipelineAssetManagement.fail_attempt``).

The spec's Run/Step/Attempt state diagram (9.1) draws ``failed`` as a
terminal Attempt/Step outcome but never specifies the operation that reports
one, nor how a Step's retry policy decides whether a failed Attempt gets
another try (``retry_wait -> ready``, spec 9.1's literal edge) or gives up
for good (``failed``). ``RunSpec.retry_policy`` is deliberately left an
uninterpreted opaque dict (T14) -- no code in this package reads it -- so
this module does not either: the caller (whoever already evaluated the
policy, e.g. the controller loop driving retries) passes the outcome in
directly as ``retryable``, and this function only does the mechanical part:
fencing check, then the matching Attempt/Step state transition.

Only ``leased``/``running`` Attempts are failable here, mirroring
``run_cancel.py``'s identical ``_ATTEMPT_ACTIVE_STATUSES`` choice and for the
same reason: ``committing`` is only ever set transiently inside a single
synchronous ``finalize_step_outputs`` call (T22), which always advances it
further to ``succeeded`` before returning, so no concurrent caller can ever
observe an Attempt at rest in ``committing``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, Sequence

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    compute_occurrence_id,
)

from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    StepRecord,
    StepStatus,
    RunRecord,
    RunStatus,
)

__all__ = ["FailAttemptError", "FailAttemptStore", "fail_attempt"]


class FailAttemptError(ValueError):
    """Raised when an Attempt cannot be failed as requested."""


class FailAttemptStore(Protocol):
    def get_run(self, run_id: str) -> Optional[RunRecord]: ...

    def update_run_status(self, run_id: str, target_status: RunStatus, **field_updates) -> RunRecord: ...

    def get_attempt(self, run_id: str, step_name: str, attempt_no: int) -> Optional[AttemptRecord]: ...

    def update_attempt_status(
        self, run_id: str, step_name: str, attempt_no: int, target_status: AttemptStatus, **field_updates
    ) -> AttemptRecord: ...

    def get_step(self, run_id: str, step_name: str) -> Optional[StepRecord]: ...

    def update_step_status(
        self, run_id: str, step_name: str, target_status: StepStatus, **field_updates
    ) -> StepRecord: ...

    def get_occurrence(self, occurrence_id: TypedId) -> Optional[OccurrenceRecord]: ...

    def put_occurrence(self, record: OccurrenceRecord) -> OccurrenceRecord: ...


_ATTEMPT_FAILABLE_STATUSES = frozenset({AttemptStatus.LEASED, AttemptStatus.RUNNING})


def fail_attempt(
    store: FailAttemptStore,
    *,
    run_id: str,
    step_name: str,
    attempt_no: int,
    fencing_token: int,
    retryable: bool,
    reason: str,
    output_names: Sequence[str] = (),
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0,
    now: Optional[datetime] = None,
) -> StepRecord:
    """Report that Attempt ``attempt_no`` failed, fencing out a stale caller.

    ``fencing_token`` must match the Attempt's current token exactly (spec
    10.3's fencing rule): a superseded owner (e.g. a worker whose lease was
    already taken over) reporting failure late must not affect the Step a
    newer Attempt now owns. Returns the updated Step: ``retry_wait`` if
    ``retryable``, else the terminal ``failed``.
    """
    if not isinstance(reason, str) or not reason:
        raise FailAttemptError(f"reason must be a non-empty str, got {reason!r}")

    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise FailAttemptError(f"max_attempts must be a positive int, got {max_attempts!r}")
    if isinstance(retry_backoff_seconds, bool) or not isinstance(
        retry_backoff_seconds, (int, float)
    ) or retry_backoff_seconds < 0:
        raise FailAttemptError(
            f"retry_backoff_seconds must be a non-negative number, got {retry_backoff_seconds!r}"
        )

    run = store.get_run(run_id)
    if run is None:
        raise FailAttemptError(f"no Run registered for run_id {run_id!r}")
    attempt = store.get_attempt(run_id, step_name, attempt_no)
    if attempt is None:
        raise FailAttemptError(
            f"no Attempt registered for (run_id={run_id!r}, step_name={step_name!r}, "
            f"attempt_no={attempt_no!r})"
        )
    if attempt.status not in _ATTEMPT_FAILABLE_STATUSES:
        raise FailAttemptError(
            f"Attempt (run_id={run_id!r}, step_name={step_name!r}, attempt_no={attempt_no!r}) is "
            f"not eligible to fail (status={attempt.status.value!r})"
        )
    if attempt.fencing_token != fencing_token:
        raise FailAttemptError(
            f"fencing_token mismatch for (run_id={run_id!r}, step_name={step_name!r}, "
            f"attempt_no={attempt_no!r}): expected {attempt.fencing_token!r}, got {fencing_token!r}; "
            "refusing to act on a superseded Attempt"
        )

    step = store.get_step(run_id, step_name)
    if step is None:
        raise FailAttemptError(f"no Step registered for (run_id={run_id!r}, step_name={step_name!r})")
    if step.current_attempt_no != attempt_no:
        raise FailAttemptError(
            f"Attempt {attempt_no!r} is no longer current for step {step_name!r}"
        )
    if run.status != RunStatus.RUNNING:
        raise FailAttemptError(
            f"Run {run_id!r} is not running (status={run.status.value!r})"
        )

    store.update_attempt_status(
        run_id,
        step_name,
        attempt_no,
        AttemptStatus.FAILED,
        owner_principal=None,
        lease_expires_at=None,
        heartbeat_at=None,
        failure_reason=reason,
    )

    for output_name in output_names:
        occurrence = store.get_occurrence(
            compute_occurrence_id(run_id, step_name, attempt_no, output_name)
        )
        if occurrence is not None and occurrence.status == OccurrenceStatus.PROVISIONAL:
            store.put_occurrence(replace(occurrence, status=OccurrenceStatus.ABORTED))

    may_retry = retryable and attempt_no < max_attempts
    if may_retry:
        base_time = now or datetime.now(timezone.utc)
        next_attempt_at = (base_time + timedelta(seconds=float(retry_backoff_seconds))).isoformat()
        return store.update_step_status(
            run_id,
            step_name,
            StepStatus.RETRY_WAIT,
            next_attempt_at=next_attempt_at,
            failure_reason=reason,
        )

    updated_step = store.update_step_status(
        run_id,
        step_name,
        StepStatus.FAILED,
        next_attempt_at=None,
        failure_reason=reason,
    )
    store.update_run_status(run_id, RunStatus.FAILED, failure_reason=reason)
    return updated_step

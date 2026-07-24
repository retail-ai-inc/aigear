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

from typing import Optional, Protocol

from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    StepRecord,
    StepStatus,
)

__all__ = ["FailAttemptError", "FailAttemptStore", "fail_attempt"]


class FailAttemptError(ValueError):
    """Raised when an Attempt cannot be failed as requested."""


class FailAttemptStore(Protocol):
    def get_attempt(self, run_id: str, step_name: str, attempt_no: int) -> Optional[AttemptRecord]: ...

    def update_attempt_status(
        self, run_id: str, step_name: str, attempt_no: int, target_status: AttemptStatus, **field_updates
    ) -> AttemptRecord: ...

    def get_step(self, run_id: str, step_name: str) -> Optional[StepRecord]: ...

    def update_step_status(
        self, run_id: str, step_name: str, target_status: StepStatus, **field_updates
    ) -> StepRecord: ...


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

    store.update_attempt_status(
        run_id,
        step_name,
        attempt_no,
        AttemptStatus.FAILED,
        owner_principal=None,
        lease_expires_at=None,
        heartbeat_at=None,
    )

    step = store.get_step(run_id, step_name)
    if step is None:
        raise FailAttemptError(f"no Step registered for (run_id={run_id!r}, step_name={step_name!r})")
    target_status = StepStatus.RETRY_WAIT if retryable else StepStatus.FAILED
    return store.update_step_status(run_id, step_name, target_status)

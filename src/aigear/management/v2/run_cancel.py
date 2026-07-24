"""Run cancellation (spec section 10.5).

Implements the parts of spec 10.5's cancellation flow that do not require
real VM lifecycle management:

1. CAS the Run to ``cancelling``.
2. Fence every non-terminal Step's current Attempt to ``cancelled``
   (bumping its ``fencing_token`` and clearing lease bookkeeping) and the
   Step itself to ``cancelled``.
3. Move the Run to ``cancelled`` once every Step has reached a terminal
   status (``reconcile_cancelled_run``, split out so a controller can retry
   just the reconcile step without re-fencing already-cancelled Steps).

Late ``finalize_step_outputs`` calls (spec 10.5 step 5) need no separate
check here: T22 already rejects them once the Run is no longer ``running``
or the Attempt is no longer in an eligible status, and this module puts the
Run into ``cancelling`` and every Attempt it touches into ``cancelled``
before returning.

``reason`` is accepted, matching spec 2181's ``PipelineAssetManagement.
cancel_run(run_id, reason)`` signature, and validated, but not persisted
anywhere: Phase B has no audit-log sink for it (the spec's audit-trail
chapter is future scope) and no field reserved for it on ``RunRecord``.

Deliberately out of scope: stopping/deleting the deterministic VM (spec
10.5 step 4), which needs real infrastructure this fake registry has no
notion of.

Callers must pass every ``step_name`` declared in the Run's RunSpec: this
module has no way to enumerate a Run's Steps on its own (the fake registry
is deliberately keyed by ``(run_id, step_name)`` with no reverse index; see
T27 for read-side iteration helpers).
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence

from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
)

__all__ = [
    "RunCancelError",
    "RunCancelStore",
    "cancel_run",
    "reconcile_cancelled_run",
]


class RunCancelError(ValueError):
    """Raised when a Run cannot be cancelled or reconciled as requested."""


class RunCancelStore(Protocol):
    def get_run(self, run_id: str) -> Optional[RunRecord]: ...

    def update_run_status(self, run_id: str, target_status: RunStatus, **field_updates) -> RunRecord: ...

    def get_step(self, run_id: str, step_name: str) -> Optional[StepRecord]: ...

    def update_step_status(
        self, run_id: str, step_name: str, target_status: StepStatus, **field_updates
    ) -> StepRecord: ...

    def get_attempt(self, run_id: str, step_name: str, attempt_no: int) -> Optional[AttemptRecord]: ...

    def update_attempt_status(
        self, run_id: str, step_name: str, attempt_no: int, target_status: AttemptStatus, **field_updates
    ) -> AttemptRecord: ...


_STEP_TERMINAL_STATUSES = frozenset({StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED})
_ATTEMPT_ACTIVE_STATUSES = frozenset({AttemptStatus.LEASED, AttemptStatus.RUNNING})


def cancel_run(
    store: RunCancelStore, *, run_id: str, step_names: Sequence[str], reason: str
) -> RunRecord:
    """Begin cancelling ``run_id``: CAS it to ``cancelling`` and fence every
    non-terminal Step (and its current Attempt, if any) to ``cancelled``."""
    if not isinstance(reason, str) or not reason:
        raise RunCancelError(f"reason must be a non-empty str, got {reason!r}")

    run = store.get_run(run_id)
    if run is None:
        raise RunCancelError(f"no Run registered for run_id {run_id!r}")
    if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED):
        raise RunCancelError(
            f"Run {run_id!r} is already terminal (status={run.status.value!r}); cannot cancel"
        )
    if run.status == RunStatus.CANCELLED:
        return run
    if run.status == RunStatus.RUNNING:
        store.update_run_status(run_id, RunStatus.CANCELLING)

    for step_name in step_names:
        step = store.get_step(run_id, step_name)
        if step is None or step.status in _STEP_TERMINAL_STATUSES:
            continue

        if step.current_attempt_no is not None:
            attempt = store.get_attempt(run_id, step_name, step.current_attempt_no)
            if attempt is not None and attempt.status in _ATTEMPT_ACTIVE_STATUSES:
                store.update_attempt_status(
                    run_id,
                    step_name,
                    attempt.attempt_no,
                    AttemptStatus.CANCELLED,
                    fencing_token=attempt.fencing_token + 1,
                    owner_principal=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )

        store.update_step_status(run_id, step_name, StepStatus.CANCELLED)

    return reconcile_cancelled_run(store, run_id=run_id, step_names=step_names)


def reconcile_cancelled_run(
    store: RunCancelStore, *, run_id: str, step_names: Sequence[str]
) -> RunRecord:
    """Move a ``cancelling`` Run to ``cancelled`` once every one of its
    Steps has reached a terminal status; a no-op otherwise (spec 10.5 step 6)."""
    run = store.get_run(run_id)
    if run is None:
        raise RunCancelError(f"no Run registered for run_id {run_id!r}")
    if run.status != RunStatus.CANCELLING:
        return run

    all_terminal = all(
        (step := store.get_step(run_id, step_name)) is not None
        and step.status in _STEP_TERMINAL_STATUSES
        for step_name in step_names
    )
    if not all_terminal:
        return run
    return store.update_run_status(run_id, RunStatus.CANCELLED)

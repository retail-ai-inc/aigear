"""Step lease acquisition (spec section 10.3).

Implements the parts of spec 10.3's step-lease transaction that do not
require real GCP infrastructure: verifying the Run is running, verifying the
Step is eligible (fresh acquisition from ``ready``/``retry_wait``, or
takeover of an expired lease from ``leased``/``running``), expiring the old
Attempt and aborting its provisional Occurrences on takeover, incrementing
the monotonic ``fencing_token``, creating the new Attempt, and creating a
deterministic provisional Occurrence (``asset_version_id=None``) for every
declared output slot.

Deliberately out of scope (real infrastructure, not this package's fake
in-memory registry): deterministic VM naming/creation, and validating the
real Firestore database resource / ``registry_binding_id``/``epoch`` /
write epoch (spec 10.3 steps 1 and 7's VM/credential half).

Two modeling decisions worth calling out explicitly:

- A takeover always leaves the Step in ``leased`` (the new Attempt starts
  there), which needed ``running -> leased`` added to
  ``_VALID_STEP_TRANSITIONS`` in ``records/run.py`` -- the same gap, and
  the same fix, as ``running -> expired`` for ``AttemptStatus`` (see that
  module's docstring): the diagram never draws a takeover edge, but spec
  10.3's takeover procedure requires one regardless of whether the
  superseded Attempt was ``leased`` or already ``running``.
- Every provisional Occurrence needs a non-empty ``operation_id`` (spec
  8.4), but the finalize Operation for an Attempt is not created until a
  worker actually completes it (T22). Spec 10.2/10.4 do not give a formula
  for this operation's idempotency key, so this module defines one:
  ``compute_attempt_finalize_operation_id(run_id, step_name, attempt_no)``,
  deterministic and 1:1 with the Attempt. T22's finalize operation should
  reuse this same function so its idempotency key matches the identifier a
  provisional Occurrence already carries.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional, Protocol, Sequence, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    ResolvedInputBinding,
    compute_committed_output_key,
    compute_metrics_digest,
    compute_occurrence_id,
    compute_resolved_inputs_digest,
)
from aigear.management.v2.records.run import AttemptRecord, AttemptStatus, RunStatus, StepStatus

__all__ = [
    "StepLeaseError",
    "DEFAULT_LEASE_TTL",
    "DEFAULT_CLOCK_SKEW_MARGIN",
    "StepLeaseStore",
    "compute_attempt_finalize_operation_id",
    "acquire_step_lease",
]


class StepLeaseError(ValueError):
    """Raised when a Step is not eligible for lease acquisition (spec 10.3)."""


DEFAULT_LEASE_TTL = timedelta(seconds=120)
# Spec 10.3 gives explicit suggested defaults for lease_ttl (120s) and
# heartbeat_interval (30s), but not for the clock-skew margin itself; zero is
# the conservative choice absent a specified value.
DEFAULT_CLOCK_SKEW_MARGIN = timedelta(seconds=0)


class StepLeaseStore(Protocol):
    def get_run(self, run_id: str): ...

    def get_step(self, run_id: str, step_name: str): ...

    def get_attempt(self, run_id: str, step_name: str, attempt_no: int) -> Optional[AttemptRecord]: ...

    def update_attempt_status(
        self, run_id: str, step_name: str, attempt_no: int, target_status, **field_updates
    ) -> AttemptRecord: ...

    def create_attempt(self, record: AttemptRecord) -> AttemptRecord: ...

    def update_step_status(self, run_id: str, step_name: str, target_status, **field_updates): ...

    def get_occurrence(self, occurrence_id: TypedId) -> Optional[OccurrenceRecord]: ...

    def put_occurrence(self, record: OccurrenceRecord) -> OccurrenceRecord: ...


def compute_attempt_finalize_operation_id(run_id: str, step_name: str, attempt_no: int) -> TypedId:
    """Deterministic idempotency key for the Operation that will eventually finalize
    this Attempt's outputs. Not specified by the spec as a formula; defined here
    because every provisional Occurrence (spec 8.4) requires a non-empty
    ``operation_id`` before any real completion message exists."""
    payload = ["aigear.attempt-finalize-operation.v2", run_id, step_name, attempt_no]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def acquire_step_lease(
    store: StepLeaseStore,
    *,
    run_id: str,
    step_name: str,
    output_names: Sequence[str],
    resolved_input_bindings: Tuple[ResolvedInputBinding, ...],
    environment_fingerprint: TypedId,
    schema_version: str,
    owner_principal: str,
    now: datetime,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
    clock_skew_margin: timedelta = DEFAULT_CLOCK_SKEW_MARGIN,
) -> AttemptRecord:
    """Acquire (or take over) the lease for one Step, creating its new Attempt and
    a provisional Occurrence for every declared output slot."""
    run = store.get_run(run_id)
    if run is None:
        raise StepLeaseError(f"no Run registered for run_id {run_id!r}")
    if run.status != RunStatus.RUNNING:
        raise StepLeaseError(f"Run {run_id!r} is not running (status={run.status.value!r})")

    step = store.get_step(run_id, step_name)
    if step is None:
        raise StepLeaseError(f"no Step registered for (run_id={run_id!r}, step_name={step_name!r})")

    is_takeover = step.status in (StepStatus.LEASED, StepStatus.RUNNING)
    if step.status not in (
        StepStatus.READY,
        StepStatus.RETRY_WAIT,
        StepStatus.LEASED,
        StepStatus.RUNNING,
    ):
        raise StepLeaseError(
            f"Step (run_id={run_id!r}, step_name={step_name!r}) is not eligible for lease "
            f"acquisition (status={step.status.value!r})"
        )

    # A Step's current_attempt_no can be set even when it is *not* a takeover:
    # a plain retry (spec 9.1's retry_wait -> ready edge) leaves the failed
    # Attempt's number on the Step so the next Attempt continues the sequence
    # instead of restarting it at 1.
    previous_attempt: Optional[AttemptRecord] = None
    if step.current_attempt_no is not None:
        previous_attempt = store.get_attempt(run_id, step_name, step.current_attempt_no)
        if previous_attempt is None:
            raise StepLeaseError(
                f"no Attempt registered for (run_id={run_id!r}, step_name={step_name!r}, "
                f"attempt_no={step.current_attempt_no!r})"
            )

    if is_takeover:
        if previous_attempt is None:
            raise StepLeaseError(
                f"Step (run_id={run_id!r}, step_name={step_name!r}) is "
                f"{step.status.value!r} but has no current_attempt_no"
            )
        if previous_attempt.lease_expires_at is None:
            raise StepLeaseError(
                f"current Attempt for (run_id={run_id!r}, step_name={step_name!r}) has no "
                "lease_expires_at; cannot take over"
            )
        expires_at = datetime.fromisoformat(previous_attempt.lease_expires_at)
        if now < expires_at + clock_skew_margin:
            raise StepLeaseError(
                f"current Attempt lease for (run_id={run_id!r}, step_name={step_name!r}) has "
                "not expired yet; refusing takeover"
            )

        store.update_attempt_status(run_id, step_name, previous_attempt.attempt_no, AttemptStatus.EXPIRED)
        for output_name in output_names:
            stale_occurrence_id = compute_occurrence_id(
                run_id, step_name, previous_attempt.attempt_no, output_name
            )
            stale_occurrence = store.get_occurrence(stale_occurrence_id)
            if stale_occurrence is not None and stale_occurrence.status == OccurrenceStatus.PROVISIONAL:
                store.put_occurrence(replace(stale_occurrence, status=OccurrenceStatus.ABORTED))

    new_attempt_no = previous_attempt.attempt_no + 1 if previous_attempt is not None else 1
    new_fencing_token = previous_attempt.fencing_token + 1 if previous_attempt is not None else 1
    lease_expires_at = now + lease_ttl

    attempt = AttemptRecord(
        run_id=run_id,
        step_name=step_name,
        attempt_no=new_attempt_no,
        status=AttemptStatus.LEASED,
        fencing_token=new_fencing_token,
        owner_principal=owner_principal,
        lease_expires_at=lease_expires_at.isoformat(),
        heartbeat_at=now.isoformat(),
    )
    store.create_attempt(attempt)

    operation_id = compute_attempt_finalize_operation_id(run_id, step_name, new_attempt_no).bare
    resolved_inputs_digest = compute_resolved_inputs_digest(resolved_input_bindings)
    metrics_digest = compute_metrics_digest({})

    for output_name in output_names:
        occurrence_id = compute_occurrence_id(run_id, step_name, new_attempt_no, output_name)
        provisional = OccurrenceRecord(
            schema_version=schema_version,
            environment_fingerprint=environment_fingerprint,
            occurrence_id=occurrence_id,
            run_id=run_id,
            step_name=step_name,
            attempt_no=new_attempt_no,
            fencing_token=new_fencing_token,
            output_name=output_name,
            committed_output_key=compute_committed_output_key(run_id, step_name, output_name),
            resolved_input_bindings=resolved_input_bindings,
            resolved_inputs_digest=resolved_inputs_digest,
            metrics={},
            metrics_digest=metrics_digest,
            status=OccurrenceStatus.PROVISIONAL,
            operation_id=operation_id,
        )
        store.put_occurrence(provisional)

    store.update_step_status(
        run_id, step_name, StepStatus.LEASED, current_attempt_no=new_attempt_no
    )

    return attempt

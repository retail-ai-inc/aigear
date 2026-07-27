"""Lease heartbeat with owner and fencing validation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Protocol

from aigear.management.v2.records.run import AttemptRecord, AttemptStatus, RunRecord, RunStatus, StepRecord

__all__ = ["AttemptHeartbeatError", "heartbeat_attempt"]


class AttemptHeartbeatError(ValueError):
    pass


class AttemptHeartbeatStore(Protocol):
    def get_run(self, run_id: str) -> Optional[RunRecord]: ...
    def get_step(self, run_id: str, step_name: str) -> Optional[StepRecord]: ...
    def get_attempt(self, run_id: str, step_name: str, attempt_no: int) -> Optional[AttemptRecord]: ...
    def update_attempt_status(
        self, run_id: str, step_name: str, attempt_no: int, target_status: AttemptStatus, **field_updates
    ) -> AttemptRecord: ...


def heartbeat_attempt(
    store: AttemptHeartbeatStore,
    *,
    run_id: str,
    step_name: str,
    attempt_no: int,
    fencing_token: int,
    owner_principal: str,
    now: datetime,
    lease_ttl: timedelta = timedelta(seconds=120),
) -> AttemptRecord:
    if now.tzinfo is None or now.utcoffset() is None:
        raise AttemptHeartbeatError("now must be timezone-aware")
    if lease_ttl <= timedelta(0):
        raise AttemptHeartbeatError("lease_ttl must be positive")
    run = store.get_run(run_id)
    if run is None or run.status != RunStatus.RUNNING:
        raise AttemptHeartbeatError("Run is missing or not running")
    step = store.get_step(run_id, step_name)
    if step is None or step.current_attempt_no != attempt_no:
        raise AttemptHeartbeatError("Attempt is no longer the Step's current owner")
    attempt = store.get_attempt(run_id, step_name, attempt_no)
    if attempt is None:
        raise AttemptHeartbeatError("Attempt does not exist")
    if attempt.status not in (AttemptStatus.LEASED, AttemptStatus.RUNNING):
        raise AttemptHeartbeatError(f"Attempt status {attempt.status.value!r} cannot heartbeat")
    if attempt.fencing_token != fencing_token:
        raise AttemptHeartbeatError("fencing_token mismatch")
    if attempt.owner_principal != owner_principal:
        raise AttemptHeartbeatError("owner_principal mismatch")
    if attempt.lease_expires_at is None:
        raise AttemptHeartbeatError("Attempt has no lease_expires_at")
    expires_at = datetime.fromisoformat(attempt.lease_expires_at)
    if now >= expires_at:
        raise AttemptHeartbeatError("Attempt lease has expired; it must be reacquired")
    return store.update_attempt_status(
        run_id,
        step_name,
        attempt_no,
        attempt.status,
        heartbeat_at=now.isoformat(),
        lease_expires_at=(now + lease_ttl).isoformat(),
    )

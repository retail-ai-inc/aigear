from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.attempt_heartbeat import AttemptHeartbeatError, heartbeat_attempt
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.run import RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.step_lease import acquire_step_lease


_NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _leased():
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))
    registry.update_run_status("run-1", RunStatus.RUNNING)
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.READY))
    attempt = acquire_step_lease(
        registry,
        run_id="run-1",
        step_name="train",
        output_names=["model"],
        resolved_input_bindings=(),
        environment_fingerprint=TypedId.from_bare("aa" * 32),
        schema_version="2.0",
        owner_principal="worker@example.com",
        now=_NOW,
    )
    return registry, attempt


def test_heartbeat_extends_current_owned_lease():
    registry, attempt = _leased()
    updated = heartbeat_attempt(
        registry,
        run_id="run-1",
        step_name="train",
        attempt_no=attempt.attempt_no,
        fencing_token=attempt.fencing_token,
        owner_principal="worker@example.com",
        now=_NOW + timedelta(seconds=30),
        lease_ttl=timedelta(minutes=5),
    )
    assert updated.heartbeat_at == (_NOW + timedelta(seconds=30)).isoformat()
    assert updated.lease_expires_at == (_NOW + timedelta(minutes=5, seconds=30)).isoformat()


def test_heartbeat_rejects_wrong_fence_or_expired_lease():
    registry, attempt = _leased()
    with pytest.raises(AttemptHeartbeatError, match="fencing_token"):
        heartbeat_attempt(
            registry,
            run_id="run-1",
            step_name="train",
            attempt_no=attempt.attempt_no,
            fencing_token=attempt.fencing_token + 1,
            owner_principal="worker@example.com",
            now=_NOW + timedelta(seconds=1),
        )
    with pytest.raises(AttemptHeartbeatError, match="expired"):
        heartbeat_attempt(
            registry,
            run_id="run-1",
            step_name="train",
            attempt_no=attempt.attempt_no,
            fencing_token=attempt.fencing_token,
            owner_principal="worker@example.com",
            now=datetime.fromisoformat(attempt.lease_expires_at),
        )

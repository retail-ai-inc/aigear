from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.occurrence import OccurrenceStatus, compute_occurrence_id
from aigear.management.v2.records.run import (
    AttemptStatus,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
)
from aigear.management.v2.step_lease import StepLeaseError, acquire_step_lease

_FP = TypedId.from_bare("aa" * 32)
_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)


def _running_registry_with_ready_step(run_id="run-1", step_name="train") -> FakeRegistryV2:
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id=run_id, status=RunStatus.PENDING))
    registry.update_run_status(run_id, RunStatus.RUNNING)
    registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=StepStatus.READY))
    return registry


def _acquire(registry, run_id="run-1", step_name="train", now=_NOW, **overrides):
    defaults = dict(
        run_id=run_id,
        step_name=step_name,
        output_names=["model"],
        resolved_input_bindings=(),
        environment_fingerprint=_FP,
        schema_version="2.0",
        owner_principal="worker-vm-1@aigear",
        now=now,
    )
    defaults.update(overrides)
    return acquire_step_lease(registry, **defaults)


# ── Fresh acquisition ──────────────────────────────────────────────────────


def test_fresh_acquisition_creates_attempt_one_with_fencing_token_one():
    registry = _running_registry_with_ready_step()
    attempt = _acquire(registry)
    assert attempt.attempt_no == 1
    assert attempt.fencing_token == 1
    assert attempt.status == AttemptStatus.LEASED
    assert attempt.owner_principal == "worker-vm-1@aigear"


def test_fresh_acquisition_updates_step_to_leased_with_current_attempt_no():
    registry = _running_registry_with_ready_step()
    _acquire(registry)
    step = registry.get_step("run-1", "train")
    assert step.status == StepStatus.LEASED
    assert step.current_attempt_no == 1


def test_fresh_acquisition_creates_provisional_occurrence_per_output():
    registry = _running_registry_with_ready_step()
    _acquire(registry, output_names=["model", "metrics"])
    for output_name in ("model", "metrics"):
        occurrence_id = compute_occurrence_id("run-1", "train", 1, output_name)
        occurrence = registry.get_occurrence(occurrence_id)
        assert occurrence is not None
        assert occurrence.status == OccurrenceStatus.PROVISIONAL
        assert occurrence.asset_version_id is None


def test_retry_after_failure_continues_the_attempt_sequence():
    registry = _running_registry_with_ready_step()
    first_attempt = _acquire(registry)
    assert first_attempt.attempt_no == 1

    # Simulate the worker failing: Step moves running -> retry_wait -> ready,
    # carrying the failed attempt_no forward (spec 9.1's retry_wait -> ready edge).
    registry.update_step_status("run-1", "train", StepStatus.RUNNING)
    registry.update_attempt_status("run-1", "train", 1, AttemptStatus.RUNNING)
    registry.update_attempt_status("run-1", "train", 1, AttemptStatus.FAILED)
    registry.update_step_status("run-1", "train", StepStatus.RETRY_WAIT)
    registry.update_step_status("run-1", "train", StepStatus.READY)

    second_attempt = _acquire(registry)
    assert second_attempt.attempt_no == 2
    assert second_attempt.fencing_token == 2


def test_acquisition_rejects_run_not_running():
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.READY))
    with pytest.raises(StepLeaseError):
        _acquire(registry)


def test_acquisition_rejects_blocked_step():
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))
    registry.update_run_status("run-1", RunStatus.RUNNING)
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.BLOCKED))
    with pytest.raises(StepLeaseError):
        _acquire(registry)


def test_acquisition_rejects_unknown_run():
    registry = FakeRegistryV2()
    with pytest.raises(StepLeaseError):
        _acquire(registry)


def test_acquisition_rejects_unknown_step():
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))
    registry.update_run_status("run-1", RunStatus.RUNNING)
    with pytest.raises(StepLeaseError):
        _acquire(registry)


# ── Takeover ─────────────────────────────────────────────────────────────────


def test_takeover_after_expiry_increments_attempt_and_fencing_token():
    registry = _running_registry_with_ready_step()
    _acquire(registry, now=_NOW)

    later = _NOW + timedelta(seconds=200)
    second_attempt = _acquire(registry, now=later, owner_principal="worker-vm-2@aigear")

    assert second_attempt.attempt_no == 2
    assert second_attempt.fencing_token == 2
    assert second_attempt.owner_principal == "worker-vm-2@aigear"


def test_takeover_expires_old_attempt_and_aborts_its_provisional_occurrences():
    registry = _running_registry_with_ready_step()
    _acquire(registry, now=_NOW, output_names=["model"])

    later = _NOW + timedelta(seconds=200)
    _acquire(registry, now=later, output_names=["model"])

    old_attempt = registry.get_attempt("run-1", "train", 1)
    assert old_attempt.status == AttemptStatus.EXPIRED

    old_occurrence_id = compute_occurrence_id("run-1", "train", 1, "model")
    old_occurrence = registry.get_occurrence(old_occurrence_id)
    assert old_occurrence.status == OccurrenceStatus.ABORTED


def test_takeover_updates_step_current_attempt_no_and_stays_leased():
    registry = _running_registry_with_ready_step()
    _acquire(registry, now=_NOW)
    later = _NOW + timedelta(seconds=200)
    _acquire(registry, now=later)

    step = registry.get_step("run-1", "train")
    assert step.status == StepStatus.LEASED
    assert step.current_attempt_no == 2


def test_takeover_rejected_before_lease_expires():
    registry = _running_registry_with_ready_step()
    _acquire(registry, now=_NOW)

    soon = _NOW + timedelta(seconds=1)
    with pytest.raises(StepLeaseError):
        _acquire(registry, now=soon)


def test_takeover_allowed_from_running_step():
    registry = _running_registry_with_ready_step()
    _acquire(registry, now=_NOW)
    registry.update_step_status("run-1", "train", StepStatus.RUNNING)
    registry.update_attempt_status("run-1", "train", 1, AttemptStatus.RUNNING)

    later = _NOW + timedelta(seconds=200)
    second_attempt = _acquire(registry, now=later)
    assert second_attempt.attempt_no == 2

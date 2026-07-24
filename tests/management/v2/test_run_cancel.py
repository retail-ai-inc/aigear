from __future__ import annotations

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
)
from aigear.management.v2.run_cancel import RunCancelError, cancel_run, reconcile_cancelled_run


def _registry_with_run(run_id="run-1", *, steps):
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id=run_id, status=RunStatus.PENDING))
    registry.update_run_status(run_id, RunStatus.RUNNING)
    for step_name, status in steps.items():
        registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=status))
    return registry


def _lease(registry, run_id, step_name, attempt_no, status=AttemptStatus.LEASED, fencing_token=1):
    registry.create_attempt(
        AttemptRecord(
            run_id=run_id,
            step_name=step_name,
            attempt_no=attempt_no,
            status=status,
            fencing_token=fencing_token,
            owner_principal="worker-vm-1@aigear",
            lease_expires_at="2026-07-24T00:02:00Z",
            heartbeat_at="2026-07-24T00:00:00Z",
        )
    )
    registry.update_step_status(run_id, step_name, StepStatus.LEASED, current_attempt_no=attempt_no)


def test_cancel_run_moves_run_to_cancelling_then_cancelled_when_no_active_step():
    registry = _registry_with_run(steps={"prep": StepStatus.BLOCKED})
    run = cancel_run(registry, run_id="run-1", step_names=["prep"], reason="user requested")
    assert run.status == RunStatus.CANCELLED
    assert registry.get_step("run-1", "prep").status == StepStatus.CANCELLED


def test_cancel_run_fences_the_active_attempt_and_revokes_its_lease():
    registry = _registry_with_run(steps={"train": StepStatus.READY})
    _lease(registry, "run-1", "train", attempt_no=1)

    cancel_run(registry, run_id="run-1", step_names=["train"], reason="user requested")

    attempt = registry.get_attempt("run-1", "train", 1)
    assert attempt.status == AttemptStatus.CANCELLED
    assert attempt.fencing_token == 2
    assert attempt.owner_principal is None
    assert attempt.lease_expires_at is None
    assert registry.get_step("run-1", "train").status == StepStatus.CANCELLED


def test_cancel_run_cancels_every_non_terminal_step_in_one_pass():
    # This fake registry has no real VM to wait on, so unlike the real system
    # (where step 4's VM stop/delete is asynchronous), cancelling every listed
    # Step always completes synchronously within a single cancel_run call.
    registry = _registry_with_run(steps={"a": StepStatus.SUCCEEDED, "b": StepStatus.RUNNING})
    run = cancel_run(registry, run_id="run-1", step_names=["a", "b"], reason="user requested")
    assert run.status == RunStatus.CANCELLED
    assert registry.get_step("run-1", "b").status == StepStatus.CANCELLED


def test_reconcile_cancelled_run_waits_for_a_step_that_is_still_active():
    # Simulates a controller crash/restart mid-cancellation: "a" already
    # reached cancelled, "b" has not yet been observed as terminal.
    registry = _registry_with_run(steps={"a": StepStatus.CANCELLED, "b": StepStatus.RUNNING})
    registry.update_run_status("run-1", RunStatus.CANCELLING)

    run = reconcile_cancelled_run(registry, run_id="run-1", step_names=["a", "b"])
    assert run.status == RunStatus.CANCELLING

    registry.update_step_status("run-1", "b", StepStatus.FAILED)
    run = reconcile_cancelled_run(registry, run_id="run-1", step_names=["a", "b"])
    assert run.status == RunStatus.CANCELLED


def test_reconcile_cancelled_run_is_a_noop_for_a_running_run():
    registry = _registry_with_run(steps={"a": StepStatus.READY})
    run = reconcile_cancelled_run(registry, run_id="run-1", step_names=["a"])
    assert run.status == RunStatus.RUNNING


def test_cancel_run_is_idempotent_when_called_again_after_full_cancellation():
    registry = _registry_with_run(steps={"a": StepStatus.BLOCKED})
    cancel_run(registry, run_id="run-1", step_names=["a"], reason="first")
    run = cancel_run(registry, run_id="run-1", step_names=["a"], reason="second")
    assert run.status == RunStatus.CANCELLED


def test_cancel_run_rejects_already_succeeded_run():
    registry = _registry_with_run(steps={"a": StepStatus.RUNNING})
    registry.update_step_status("run-1", "a", StepStatus.COMMITTING)
    registry.update_step_status("run-1", "a", StepStatus.SUCCEEDED)
    registry.update_run_status("run-1", RunStatus.SUCCEEDED, remaining_required_steps=0)

    with pytest.raises(RunCancelError, match="already terminal"):
        cancel_run(registry, run_id="run-1", step_names=["a"], reason="too late")


def test_cancel_run_rejects_empty_reason():
    registry = _registry_with_run(steps={"a": StepStatus.BLOCKED})
    with pytest.raises(RunCancelError, match="reason"):
        cancel_run(registry, run_id="run-1", step_names=["a"], reason="")


def test_cancel_run_rejects_unknown_run_id():
    registry = FakeRegistryV2()
    with pytest.raises(RunCancelError, match="no Run registered"):
        cancel_run(registry, run_id="missing", step_names=[], reason="user requested")

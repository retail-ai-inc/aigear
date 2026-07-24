from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aigear.management.v2.attempt_fail import FailAttemptError, fail_attempt
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.run import (
    AttemptStatus,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
)
from aigear.management.v2.step_lease import acquire_step_lease

_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
_FP = TypedId.from_bare("aa" * 32)


def _leased_attempt(registry, *, run_id="run-1", step_name="train"):
    registry.create_run(RunRecord(run_id=run_id, status=RunStatus.PENDING))
    registry.update_run_status(run_id, RunStatus.RUNNING)
    registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=StepStatus.READY))
    return acquire_step_lease(
        registry,
        run_id=run_id,
        step_name=step_name,
        output_names=["model"],
        resolved_input_bindings=(),
        environment_fingerprint=_FP,
        schema_version="2.0",
        owner_principal="worker-vm-1@aigear",
        now=_NOW,
    )


def test_fail_attempt_retryable_moves_step_to_retry_wait():
    registry = FakeRegistryV2()
    attempt = _leased_attempt(registry)

    step = fail_attempt(
        registry,
        run_id="run-1",
        step_name="train",
        attempt_no=attempt.attempt_no,
        fencing_token=attempt.fencing_token,
        retryable=True,
        reason="transient worker crash",
    )

    assert step.status == StepStatus.RETRY_WAIT
    updated_attempt = registry.get_attempt("run-1", "train", attempt.attempt_no)
    assert updated_attempt.status == AttemptStatus.FAILED
    assert updated_attempt.owner_principal is None


def test_fail_attempt_non_retryable_moves_step_to_failed():
    registry = FakeRegistryV2()
    attempt = _leased_attempt(registry)

    step = fail_attempt(
        registry,
        run_id="run-1",
        step_name="train",
        attempt_no=attempt.attempt_no,
        fencing_token=attempt.fencing_token,
        retryable=False,
        reason="user code raised a non-retryable exception",
    )

    assert step.status == StepStatus.FAILED
    assert registry.get_attempt("run-1", "train", attempt.attempt_no).status == AttemptStatus.FAILED


def test_fail_attempt_rejects_stale_fencing_token():
    registry = FakeRegistryV2()
    attempt = _leased_attempt(registry)

    with pytest.raises(FailAttemptError, match="fencing_token mismatch"):
        fail_attempt(
            registry,
            run_id="run-1",
            step_name="train",
            attempt_no=attempt.attempt_no,
            fencing_token=attempt.fencing_token - 1,
            retryable=True,
            reason="stale worker report",
        )


def test_fail_attempt_rejects_already_terminal_attempt():
    registry = FakeRegistryV2()
    attempt = _leased_attempt(registry)
    fail_attempt(
        registry,
        run_id="run-1",
        step_name="train",
        attempt_no=attempt.attempt_no,
        fencing_token=attempt.fencing_token,
        retryable=False,
        reason="first failure",
    )

    with pytest.raises(FailAttemptError, match="not eligible to fail"):
        fail_attempt(
            registry,
            run_id="run-1",
            step_name="train",
            attempt_no=attempt.attempt_no,
            fencing_token=attempt.fencing_token,
            retryable=False,
            reason="duplicate report",
        )


def test_fail_attempt_rejects_empty_reason():
    registry = FakeRegistryV2()
    attempt = _leased_attempt(registry)

    with pytest.raises(FailAttemptError, match="reason must be a non-empty str"):
        fail_attempt(
            registry,
            run_id="run-1",
            step_name="train",
            attempt_no=attempt.attempt_no,
            fencing_token=attempt.fencing_token,
            retryable=True,
            reason="",
        )


def test_fail_attempt_rejects_unknown_attempt():
    registry = FakeRegistryV2()
    _leased_attempt(registry)

    with pytest.raises(FailAttemptError, match="no Attempt registered"):
        fail_attempt(
            registry,
            run_id="run-1",
            step_name="train",
            attempt_no=99,
            fencing_token=1,
            retryable=True,
            reason="reason",
        )

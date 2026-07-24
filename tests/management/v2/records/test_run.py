from __future__ import annotations

import pytest

from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    InvalidAttemptStatusTransitionError,
    InvalidRunRecordError,
    InvalidRunStatusTransitionError,
    InvalidStepStatusTransitionError,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    validate_attempt_status_transition,
    validate_run_status_transition,
    validate_step_status_transition,
)


# ── RunRecord ────────────────────────────────────────────────────────────────────


def test_run_record_accepts_well_formed_fields():
    record = RunRecord(run_id="run-1", status=RunStatus.PENDING)
    assert record.status == RunStatus.PENDING


def test_run_record_rejects_invalid_run_id_segment():
    with pytest.raises(ValueError):
        RunRecord(run_id="../escape", status=RunStatus.PENDING)


def test_run_record_rejects_non_enum_status():
    with pytest.raises(InvalidRunRecordError):
        RunRecord(run_id="run-1", status="pending")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "current,target",
    [
        (RunStatus.PENDING, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.SUCCEEDED),
        (RunStatus.RUNNING, RunStatus.FAILED),
        (RunStatus.RUNNING, RunStatus.CANCELLING),
        (RunStatus.CANCELLING, RunStatus.CANCELLED),
    ],
)
def test_valid_run_transitions_are_accepted(current, target):
    validate_run_status_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (RunStatus.PENDING, RunStatus.SUCCEEDED),
        (RunStatus.PENDING, RunStatus.CANCELLING),
        (RunStatus.SUCCEEDED, RunStatus.RUNNING),
        (RunStatus.FAILED, RunStatus.RUNNING),
        (RunStatus.CANCELLED, RunStatus.RUNNING),
        (RunStatus.RUNNING, RunStatus.PENDING),
    ],
)
def test_invalid_run_transitions_are_rejected(current, target):
    with pytest.raises(InvalidRunStatusTransitionError):
        validate_run_status_transition(current, target)


def test_run_terminal_states_have_no_outgoing_transitions():
    for terminal in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
        for target in RunStatus:
            with pytest.raises(InvalidRunStatusTransitionError):
                validate_run_status_transition(terminal, target)


# ── StepRecord ───────────────────────────────────────────────────────────────────


def test_step_record_accepts_well_formed_fields():
    record = StepRecord(run_id="run-1", step_name="training", status=StepStatus.READY)
    assert record.current_attempt_no is None


def test_step_record_accepts_current_attempt_no():
    record = StepRecord(
        run_id="run-1", step_name="training", status=StepStatus.LEASED, current_attempt_no=2
    )
    assert record.current_attempt_no == 2


def test_step_record_rejects_non_positive_current_attempt_no():
    with pytest.raises(InvalidRunRecordError):
        StepRecord(
            run_id="run-1", step_name="training", status=StepStatus.LEASED, current_attempt_no=0
        )


@pytest.mark.parametrize(
    "current,target",
    [
        (StepStatus.BLOCKED, StepStatus.READY),
        (StepStatus.READY, StepStatus.LEASED),
        (StepStatus.LEASED, StepStatus.RUNNING),
        (StepStatus.RUNNING, StepStatus.COMMITTING),
        (StepStatus.RUNNING, StepStatus.RETRY_WAIT),
        (StepStatus.RUNNING, StepStatus.FAILED),
        (StepStatus.RUNNING, StepStatus.CANCELLED),
        (StepStatus.COMMITTING, StepStatus.SUCCEEDED),
        (StepStatus.COMMITTING, StepStatus.RETRY_WAIT),
        (StepStatus.RETRY_WAIT, StepStatus.READY),
    ],
)
def test_valid_step_transitions_are_accepted(current, target):
    validate_step_status_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (StepStatus.BLOCKED, StepStatus.LEASED),
        (StepStatus.LEASED, StepStatus.FAILED),
        (StepStatus.LEASED, StepStatus.CANCELLED),
        (StepStatus.COMMITTING, StepStatus.FAILED),
        (StepStatus.COMMITTING, StepStatus.CANCELLED),
        (StepStatus.SUCCEEDED, StepStatus.READY),
        (StepStatus.FAILED, StepStatus.READY),
        (StepStatus.CANCELLED, StepStatus.READY),
    ],
)
def test_invalid_step_transitions_are_rejected(current, target):
    with pytest.raises(InvalidStepStatusTransitionError):
        validate_step_status_transition(current, target)


def test_step_terminal_states_have_no_outgoing_transitions():
    for terminal in (StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED):
        for target in StepStatus:
            with pytest.raises(InvalidStepStatusTransitionError):
                validate_step_status_transition(terminal, target)


# ── AttemptRecord ────────────────────────────────────────────────────────────────


def test_attempt_record_accepts_well_formed_fields():
    record = AttemptRecord(
        run_id="run-1",
        step_name="training",
        attempt_no=1,
        status=AttemptStatus.LEASED,
        fencing_token=0,
    )
    assert record.fencing_token == 0


def test_attempt_record_rejects_non_positive_attempt_no():
    with pytest.raises(InvalidRunRecordError):
        AttemptRecord(
            run_id="run-1",
            step_name="training",
            attempt_no=0,
            status=AttemptStatus.LEASED,
            fencing_token=0,
        )


def test_attempt_record_rejects_negative_fencing_token():
    with pytest.raises(InvalidRunRecordError):
        AttemptRecord(
            run_id="run-1",
            step_name="training",
            attempt_no=1,
            status=AttemptStatus.LEASED,
            fencing_token=-1,
        )


@pytest.mark.parametrize(
    "current,target",
    [
        (AttemptStatus.LEASED, AttemptStatus.RUNNING),
        (AttemptStatus.LEASED, AttemptStatus.EXPIRED),
        (AttemptStatus.LEASED, AttemptStatus.CANCELLED),
        (AttemptStatus.RUNNING, AttemptStatus.COMMITTING),
        (AttemptStatus.RUNNING, AttemptStatus.FAILED),
        (AttemptStatus.COMMITTING, AttemptStatus.SUCCEEDED),
        (AttemptStatus.COMMITTING, AttemptStatus.FAILED),
    ],
)
def test_valid_attempt_transitions_are_accepted(current, target):
    validate_attempt_status_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (AttemptStatus.RUNNING, AttemptStatus.EXPIRED),
        (AttemptStatus.RUNNING, AttemptStatus.CANCELLED),
        (AttemptStatus.COMMITTING, AttemptStatus.EXPIRED),
        (AttemptStatus.COMMITTING, AttemptStatus.CANCELLED),
        (AttemptStatus.SUCCEEDED, AttemptStatus.RUNNING),
        (AttemptStatus.FAILED, AttemptStatus.RUNNING),
        (AttemptStatus.EXPIRED, AttemptStatus.RUNNING),
        (AttemptStatus.CANCELLED, AttemptStatus.RUNNING),
    ],
)
def test_invalid_attempt_transitions_are_rejected(current, target):
    with pytest.raises(InvalidAttemptStatusTransitionError):
        validate_attempt_status_transition(current, target)


def test_attempt_terminal_states_have_no_outgoing_transitions():
    for terminal in (
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.EXPIRED,
        AttemptStatus.CANCELLED,
    ):
        for target in AttemptStatus:
            with pytest.raises(InvalidAttemptStatusTransitionError):
                validate_attempt_status_transition(terminal, target)

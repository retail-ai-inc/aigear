from __future__ import annotations

import pytest

from aigear.management.v2.identifiers import TypedId
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

_DIGEST = TypedId.from_bare("aa" * 32)


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


def test_run_record_new_fields_default_to_none():
    record = RunRecord(run_id="run-1", status=RunStatus.PENDING)
    assert record.run_spec_digest is None
    assert record.remaining_required_steps is None
    assert record.parent_run_id is None
    assert record.backfill_of is None


def test_run_record_accepts_run_spec_digest_and_lineage_fields():
    record = RunRecord(
        run_id="run-2",
        status=RunStatus.RUNNING,
        run_spec_digest=_DIGEST,
        remaining_required_steps=3,
        parent_run_id="run-1",
        backfill_of="run-0",
    )
    assert record.run_spec_digest == _DIGEST
    assert record.remaining_required_steps == 3
    assert record.parent_run_id == "run-1"
    assert record.backfill_of == "run-0"


def test_run_record_rejects_non_typed_id_run_spec_digest():
    with pytest.raises(InvalidRunRecordError):
        RunRecord(run_id="run-1", status=RunStatus.PENDING, run_spec_digest="not-a-typed-id")


def test_run_record_rejects_negative_remaining_required_steps():
    with pytest.raises(InvalidRunRecordError):
        RunRecord(run_id="run-1", status=RunStatus.PENDING, remaining_required_steps=-1)


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


def test_step_record_accepts_resolved_input_fields():
    record = StepRecord(
        run_id="run-1",
        step_name="training",
        status=StepStatus.READY,
        resolved_inputs_digest=_DIGEST,
        resolved_at="2026-07-24T00:00:00Z",
        source_step_revision=1,
    )
    assert record.resolved_inputs_digest == _DIGEST
    assert record.resolved_at == "2026-07-24T00:00:00Z"
    assert record.source_step_revision == 1


def test_step_record_rejects_non_typed_id_resolved_inputs_digest():
    with pytest.raises(InvalidRunRecordError):
        StepRecord(
            run_id="run-1",
            step_name="training",
            status=StepStatus.READY,
            resolved_inputs_digest="not-a-typed-id",
        )


def test_step_record_rejects_non_positive_source_step_revision():
    with pytest.raises(InvalidRunRecordError):
        StepRecord(
            run_id="run-1", step_name="training", status=StepStatus.READY, source_step_revision=0
        )


@pytest.mark.parametrize(
    "current,target",
    [
        (StepStatus.BLOCKED, StepStatus.READY),
        (StepStatus.BLOCKED, StepStatus.CANCELLED),
        (StepStatus.READY, StepStatus.LEASED),
        (StepStatus.READY, StepStatus.CANCELLED),
        (StepStatus.LEASED, StepStatus.RUNNING),
        (StepStatus.LEASED, StepStatus.CANCELLED),
        (StepStatus.RUNNING, StepStatus.COMMITTING),
        (StepStatus.RUNNING, StepStatus.RETRY_WAIT),
        (StepStatus.RUNNING, StepStatus.FAILED),
        (StepStatus.RUNNING, StepStatus.CANCELLED),
        (StepStatus.RUNNING, StepStatus.LEASED),
        (StepStatus.COMMITTING, StepStatus.SUCCEEDED),
        (StepStatus.COMMITTING, StepStatus.RETRY_WAIT),
        (StepStatus.RETRY_WAIT, StepStatus.READY),
        (StepStatus.RETRY_WAIT, StepStatus.CANCELLED),
    ],
)
def test_valid_step_transitions_are_accepted(current, target):
    validate_step_status_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (StepStatus.BLOCKED, StepStatus.LEASED),
        (StepStatus.LEASED, StepStatus.FAILED),
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


def test_attempt_record_accepts_lease_bookkeeping_fields():
    record = AttemptRecord(
        run_id="run-1",
        step_name="training",
        attempt_no=1,
        status=AttemptStatus.LEASED,
        fencing_token=1,
        owner_principal="worker-vm-1@aigear",
        lease_expires_at="2026-07-24T00:02:00Z",
        heartbeat_at="2026-07-24T00:00:00Z",
    )
    assert record.owner_principal == "worker-vm-1@aigear"
    assert record.lease_expires_at == "2026-07-24T00:02:00Z"
    assert record.heartbeat_at == "2026-07-24T00:00:00Z"


def test_attempt_record_rejects_empty_owner_principal():
    with pytest.raises(InvalidRunRecordError):
        AttemptRecord(
            run_id="run-1",
            step_name="training",
            attempt_no=1,
            status=AttemptStatus.LEASED,
            fencing_token=0,
            owner_principal="",
        )


@pytest.mark.parametrize(
    "current,target",
    [
        (AttemptStatus.LEASED, AttemptStatus.RUNNING),
        (AttemptStatus.LEASED, AttemptStatus.EXPIRED),
        (AttemptStatus.LEASED, AttemptStatus.CANCELLED),
        (AttemptStatus.RUNNING, AttemptStatus.COMMITTING),
        (AttemptStatus.RUNNING, AttemptStatus.FAILED),
        (AttemptStatus.RUNNING, AttemptStatus.EXPIRED),
        (AttemptStatus.RUNNING, AttemptStatus.CANCELLED),
        (AttemptStatus.COMMITTING, AttemptStatus.SUCCEEDED),
        (AttemptStatus.COMMITTING, AttemptStatus.FAILED),
    ],
)
def test_valid_attempt_transitions_are_accepted(current, target):
    validate_attempt_status_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
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

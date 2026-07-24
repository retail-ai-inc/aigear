from __future__ import annotations

import pytest

from aigear.management.v2.records.operation import (
    InvalidOperationPhaseTransitionError,
    InvalidOperationRecordError,
    OperationPhase,
    OperationRecord,
    validate_operation_phase_transition,
)

_HASH = "ab" * 32


def _operation_record(**overrides) -> OperationRecord:
    defaults = dict(
        idempotency_key_hash=_HASH,
        request_fingerprint="fingerprint-1",
        operation_type="pipeline_finalize",
        owner_principal="finalizer@aigear",
        write_epoch=1,
        fencing_token=0,
        phase=OperationPhase.RESERVED,
        revision=1,
    )
    defaults.update(overrides)
    return OperationRecord(**defaults)


# ── OperationRecord ──────────────────────────────────────────────────────────────


def test_operation_record_accepts_minimal_well_formed_fields():
    record = _operation_record()
    assert record.phase == OperationPhase.RESERVED
    assert record.run_id is None


def test_operation_record_accepts_run_scoped_fields():
    record = _operation_record(
        run_id="run-1", step_name="training", attempt_no=1, output_name="model"
    )
    assert record.run_id == "run-1"
    assert record.output_name == "model"


def test_operation_record_rejects_invalid_idempotency_key_hash_segment():
    with pytest.raises(ValueError):
        _operation_record(idempotency_key_hash="not/a/segment")


def test_operation_record_rejects_empty_operation_type():
    with pytest.raises(InvalidOperationRecordError):
        _operation_record(operation_type="")


def test_operation_record_rejects_non_positive_write_epoch():
    with pytest.raises(InvalidOperationRecordError):
        _operation_record(write_epoch=0)


def test_operation_record_rejects_negative_fencing_token():
    with pytest.raises(InvalidOperationRecordError):
        _operation_record(fencing_token=-1)


def test_operation_record_rejects_non_enum_phase():
    with pytest.raises(InvalidOperationRecordError):
        _operation_record(phase="reserved")  # type: ignore[arg-type]


def test_operation_record_rejects_non_positive_attempt_no():
    with pytest.raises(InvalidOperationRecordError):
        _operation_record(run_id="run-1", attempt_no=0)


def test_operation_record_rejects_last_error_without_error_class():
    with pytest.raises(InvalidOperationRecordError):
        _operation_record(last_error="boom")


def test_operation_record_rejects_error_class_without_last_error():
    with pytest.raises(InvalidOperationRecordError):
        _operation_record(error_class="ValueError")


def test_operation_record_accepts_matching_error_fields():
    record = _operation_record(last_error="boom", error_class="ValueError")
    assert record.last_error == "boom"


# ── validate_operation_phase_transition (spec 10.2) ───────────────────────────────


@pytest.mark.parametrize(
    "current,target",
    [
        (OperationPhase.RESERVED, OperationPhase.STAGING),
        (OperationPhase.STAGING, OperationPhase.UPLOADED),
        (OperationPhase.UPLOADED, OperationPhase.FINALIZING),
        (OperationPhase.FINALIZING, OperationPhase.SUCCEEDED),
        (OperationPhase.RESERVED, OperationPhase.FAILED),
        (OperationPhase.STAGING, OperationPhase.FAILED),
        (OperationPhase.UPLOADED, OperationPhase.FAILED),
        (OperationPhase.FINALIZING, OperationPhase.FAILED),
        (OperationPhase.FAILED, OperationPhase.COMPENSATING),
        (OperationPhase.COMPENSATING, OperationPhase.COMPENSATED),
        (OperationPhase.COMPENSATING, OperationPhase.MANUAL_INTERVENTION),
    ],
)
def test_valid_phase_transitions_are_accepted(current, target):
    validate_operation_phase_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (OperationPhase.RESERVED, OperationPhase.UPLOADED),
        (OperationPhase.RESERVED, OperationPhase.SUCCEEDED),
        (OperationPhase.SUCCEEDED, OperationPhase.FAILED),
        (OperationPhase.COMPENSATED, OperationPhase.RESERVED),
        (OperationPhase.MANUAL_INTERVENTION, OperationPhase.COMPENSATING),
        (OperationPhase.FAILED, OperationPhase.RESERVED),
    ],
)
def test_invalid_phase_transitions_are_rejected(current, target):
    with pytest.raises(InvalidOperationPhaseTransitionError):
        validate_operation_phase_transition(current, target)


def test_terminal_phases_have_no_outgoing_transitions():
    for terminal in (
        OperationPhase.SUCCEEDED,
        OperationPhase.COMPENSATED,
        OperationPhase.MANUAL_INTERVENTION,
    ):
        for target in OperationPhase:
            with pytest.raises(InvalidOperationPhaseTransitionError):
                validate_operation_phase_transition(terminal, target)

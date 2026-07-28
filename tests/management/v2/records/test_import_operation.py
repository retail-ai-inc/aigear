from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportCompletionRecord,
    ImportControlSnapshot,
    ImportOperationRecord,
    ImportPhase,
    ImportTicketRecord,
    InvalidImportPhaseTransitionError,
    InvalidImportRecordError,
    validate_import_phase_transition,
)

_A = TypedId.from_bare("aa" * 32)
_B = TypedId.from_bare("bb" * 32)
_C = TypedId.from_bare("cc" * 32)


def _source(**overrides) -> ExactImportSource:
    values = {
        "environment_id": "production",
        "project_id": "source-project",
        "bucket": "source.bucket",
        "object_name": "models/model.onnx",
        "generation": "123",
        "region": "asia-east1",
        "size_bytes": 42,
    }
    values.update(overrides)
    return ExactImportSource(**values)


def _control(**overrides) -> ImportControlSnapshot:
    values = {
        "environment_id": "production",
        "environment_fingerprint": _A,
        "firestore_database_id": "(default)",
        "registry_binding_id": "binding-1",
        "registry_binding_epoch": 2,
        "write_epoch": 3,
    }
    values.update(overrides)
    return ImportControlSnapshot(**values)


def _ticket(**overrides) -> ImportTicketRecord:
    values = {
        "schema_version": "2.0",
        "ticket_digest": _B,
        "operation_id": "import-1",
        "request_fingerprint": _C,
        "source": _source(),
        "target_environment_id": "production",
        "target_quarantine_prefix": "project/pipeline/registry/v2/_quarantine/import-1/",
        "audience": "aigear-import",
        "executor_principal": "import-executor@example.iam.gserviceaccount.com",
        "fencing_token": 1,
        "issued_at": "2026-07-28T00:00:00+00:00",
        "expires_at": "2026-07-28T00:05:00+00:00",
    }
    values.update(overrides)
    return ImportTicketRecord(**values)


def _completion(**overrides) -> ImportCompletionRecord:
    values = {
        "schema_version": "2.0",
        "operation_id": "import-1",
        "ticket_digest": _B,
        "environment_fingerprint": _A,
        "executor_principal": "import-executor@example.iam.gserviceaccount.com",
        "fencing_token": 1,
        "payload_set_digest": _C,
        "completed_at": "2026-07-28T00:01:00+00:00",
    }
    values.update(overrides)
    return ImportCompletionRecord(**values)


def _operation(**overrides) -> ImportOperationRecord:
    values = {
        "schema_version": "2.0",
        "operation_id": "import-1",
        "idempotency_key_hash": "ab" * 32,
        "request_fingerprint": _C,
        "source": _source(),
        "target_environment_id": "production",
        "target_quarantine_prefix": "project/pipeline/registry/v2/_quarantine/import-1/",
        "control_snapshot": _control(),
        "owner_principal": "controller@example.iam.gserviceaccount.com",
        "write_budget": 100,
        "fencing_token": 1,
        "phase": ImportPhase.RESERVED,
        "revision": 1,
    }
    values.update(overrides)
    return ImportOperationRecord(**values)


@pytest.mark.parametrize(
    "current,target",
    [
        (ImportPhase.RESERVED, ImportPhase.TICKETED),
        (ImportPhase.TICKETED, ImportPhase.QUARANTINING),
        (ImportPhase.QUARANTINING, ImportPhase.INSPECTING),
        (ImportPhase.INSPECTING, ImportPhase.PREPARING),
        (ImportPhase.PREPARING, ImportPhase.COMMITTING),
        (ImportPhase.COMMITTING, ImportPhase.SUCCEEDED),
        (ImportPhase.PREPARING, ImportPhase.COMPENSATING),
        (ImportPhase.COMPENSATING, ImportPhase.FAILED),
        (ImportPhase.COMPENSATING, ImportPhase.CANCELLED),
        (ImportPhase.QUARANTINING, ImportPhase.RECONCILING),
        (ImportPhase.RECONCILING, ImportPhase.INSPECTING),
    ],
)
def test_valid_import_phase_transitions(current, target):
    validate_import_phase_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (ImportPhase.RESERVED, ImportPhase.SUCCEEDED),
        (ImportPhase.TICKETED, ImportPhase.PREPARING),
        (ImportPhase.COMPENSATING, ImportPhase.SUCCEEDED),
        (ImportPhase.SUCCEEDED, ImportPhase.RECONCILING),
        (ImportPhase.FAILED, ImportPhase.RESERVED),
        (ImportPhase.CANCELLED, ImportPhase.RESERVED),
    ],
)
def test_invalid_import_phase_transitions(current, target):
    with pytest.raises(InvalidImportPhaseTransitionError):
        validate_import_phase_transition(current, target)


def test_exact_source_requires_positive_generation():
    with pytest.raises(InvalidImportRecordError, match="generation"):
        _source(generation="latest")


def test_ticket_rejects_naive_timestamp():
    with pytest.raises(InvalidImportRecordError, match="timezone-aware"):
        _ticket(expires_at="2026-07-28T00:05:00")


def test_ticket_rejects_cross_environment_import():
    with pytest.raises(InvalidImportRecordError, match="environment_id"):
        _ticket(target_environment_id="staging")


def test_operation_rejects_negative_fence():
    with pytest.raises(InvalidImportRecordError, match="fencing_token"):
        _operation(fencing_token=-1)


def test_operation_rejects_cross_environment_control_snapshot():
    with pytest.raises(InvalidImportRecordError, match="control snapshot"):
        _operation(control_snapshot=_control(environment_id="staging"))


def test_ticketed_operation_requires_matching_ticket():
    with pytest.raises(InvalidImportRecordError, match="require a ticket"):
        _operation(phase=ImportPhase.TICKETED)
    operation = _operation(phase=ImportPhase.TICKETED, ticket=_ticket())
    assert operation.ticket == _ticket()


def test_operation_rejects_ticket_from_another_fence():
    with pytest.raises(InvalidImportRecordError, match="not bound"):
        _operation(phase=ImportPhase.TICKETED, ticket=_ticket(fencing_token=2))


def test_inspecting_operation_requires_matching_completion():
    with pytest.raises(InvalidImportRecordError, match="require completion"):
        _operation(phase=ImportPhase.INSPECTING, ticket=_ticket())
    operation = _operation(
        phase=ImportPhase.INSPECTING,
        ticket=_ticket(),
        completion=_completion(),
    )
    assert operation.completion == _completion()


def test_operation_rejects_completion_from_another_executor():
    with pytest.raises(InvalidImportRecordError, match="not bound"):
        _operation(
            phase=ImportPhase.INSPECTING,
            ticket=_ticket(),
            completion=_completion(executor_principal="attacker@example.com"),
        )


def test_succeeded_operation_requires_result_and_finished_at():
    with pytest.raises(InvalidImportRecordError, match="result_asset_version_id"):
        _operation(
            phase=ImportPhase.SUCCEEDED,
            ticket=_ticket(),
            completion=_completion(),
            finished_at="2026-07-28T00:03:00+00:00",
        )
    operation = _operation(
        phase=ImportPhase.SUCCEEDED,
        ticket=_ticket(),
        completion=_completion(),
        result_asset_version_id=_A,
        finished_at="2026-07-28T00:03:00+00:00",
    )
    assert operation.result_asset_version_id == _A


def test_terminal_operation_cannot_reopen():
    terminal = _operation(
        phase=ImportPhase.SUCCEEDED,
        ticket=_ticket(),
        completion=_completion(),
        result_asset_version_id=_A,
        finished_at="2026-07-28T00:03:00+00:00",
    )
    with pytest.raises(InvalidImportPhaseTransitionError):
        validate_import_phase_transition(terminal.phase, ImportPhase.RECONCILING)


def test_replacing_operation_with_mismatched_ticket_fails_closed():
    operation = _operation(phase=ImportPhase.TICKETED, ticket=_ticket())
    with pytest.raises(InvalidImportRecordError, match="not bound"):
        replace(operation, fencing_token=2)

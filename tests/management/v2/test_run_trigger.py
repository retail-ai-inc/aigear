from __future__ import annotations

from typing import Dict, Optional

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.operation import OperationPhase, OperationRecord
from aigear.management.v2.run_trigger import (
    RunTriggerIdempotencyConflict,
    begin_run_trigger,
    compute_run_idempotency_key,
)

_PAYLOAD_DIGEST = TypedId.from_bare("aa" * 32)


class _InMemoryOperationStore:
    """Minimal stand-in satisfying the OperationStore Protocol for these tests."""

    def __init__(self) -> None:
        self._operations: Dict[str, OperationRecord] = {}

    def get_operation(self, idempotency_key_hash: str) -> Optional[OperationRecord]:
        return self._operations.get(idempotency_key_hash)

    def put_operation(self, record: OperationRecord) -> OperationRecord:
        self._operations[record.idempotency_key_hash] = record
        return record


# ── compute_run_idempotency_key ───────────────────────────────────────────────


def test_compute_run_idempotency_key_is_deterministic():
    a = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    b = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    assert a == b


def test_compute_run_idempotency_key_changes_with_scheduled_for():
    a = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    b = compute_run_idempotency_key("pipeline-x", "2026-07-25T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    assert a != b


def test_compute_run_idempotency_key_changes_with_trigger_payload_digest():
    other_digest = TypedId.from_bare("bb" * 32)
    a = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    b = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", other_digest)
    assert a != b


# ── begin_run_trigger ─────────────────────────────────────────────────────────


def test_begin_run_trigger_creates_run_on_first_call():
    store = _InMemoryOperationStore()
    key = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    created_run_ids = []

    def create_run():
        created_run_ids.append("run-1")
        return "run-1"

    operation = begin_run_trigger(
        store,
        idempotency_key=key,
        request_fingerprint="fp-1",
        owner_principal="controller@aigear",
        create_run=create_run,
    )

    assert created_run_ids == ["run-1"]
    assert operation.run_id == "run-1"
    assert operation.phase == OperationPhase.SUCCEEDED
    assert operation.idempotency_key_hash == key.bare
    assert store.get_operation(key.bare) is operation


def test_begin_run_trigger_replay_with_same_fingerprint_does_not_create_run_again():
    store = _InMemoryOperationStore()
    key = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    call_count = [0]

    def create_run():
        call_count[0] += 1
        return "run-1"

    first = begin_run_trigger(
        store,
        idempotency_key=key,
        request_fingerprint="fp-1",
        owner_principal="controller@aigear",
        create_run=create_run,
    )
    second = begin_run_trigger(
        store,
        idempotency_key=key,
        request_fingerprint="fp-1",
        owner_principal="controller@aigear",
        create_run=create_run,
    )

    assert call_count[0] == 1
    assert second is first
    assert second.run_id == "run-1"


def test_begin_run_trigger_replay_with_different_fingerprint_conflicts():
    store = _InMemoryOperationStore()
    key = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)

    begin_run_trigger(
        store,
        idempotency_key=key,
        request_fingerprint="fp-1",
        owner_principal="controller@aigear",
        create_run=lambda: "run-1",
    )

    with pytest.raises(RunTriggerIdempotencyConflict):
        begin_run_trigger(
            store,
            idempotency_key=key,
            request_fingerprint="fp-2",
            owner_principal="controller@aigear",
            create_run=lambda: "run-2",
        )


def test_begin_run_trigger_rejects_create_run_returning_non_string():
    store = _InMemoryOperationStore()
    key = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)

    with pytest.raises(TypeError):
        begin_run_trigger(
            store,
            idempotency_key=key,
            request_fingerprint="fp-1",
            owner_principal="controller@aigear",
            create_run=lambda: None,
        )


def test_begin_run_trigger_different_keys_both_create_runs():
    store = _InMemoryOperationStore()
    key_a = compute_run_idempotency_key("pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)
    key_b = compute_run_idempotency_key("pipeline-x", "2026-07-25T00:00:00Z", "cloud_scheduler", _PAYLOAD_DIGEST)

    op_a = begin_run_trigger(
        store, idempotency_key=key_a, request_fingerprint="fp", owner_principal="c", create_run=lambda: "run-a"
    )
    op_b = begin_run_trigger(
        store, idempotency_key=key_b, request_fingerprint="fp", owner_principal="c", create_run=lambda: "run-b"
    )

    assert op_a.run_id == "run-a"
    assert op_b.run_id == "run-b"

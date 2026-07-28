from __future__ import annotations

import pytest

from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.environment import RegistryBinding
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_reservation import (
    ImportReservationConflict,
    ImportReservationError,
    reserve_import_operation,
)
from aigear.management.v2.records.import_operation import ExactImportSource

_FP = TypedId.from_bare("aa" * 32)


def _control(**overrides):
    values = {
        "schema_version": "2.0",
        "environment_id": "production",
        "authority": "v2",
        "phase": "v2_authoritative",
        "write_epoch": 3,
        "min_reader_version": "2.0",
        "min_writer_version": "2.0",
        "required_capabilities": ("typed_gcs_layout_v2",),
        "environment_fingerprint": _FP,
        "registry_binding": RegistryBinding(
            firestore_database_id="(default)",
            registry_binding_id="A" * 43,
            registry_binding_epoch=2,
            bound_environment_fingerprint=_FP,
        ),
        "registry_bound_by": "bootstrap@example.com",
    }
    values.update(overrides)
    return ControlDocument(**values)


def _source():
    return ExactImportSource(
        environment_id="production",
        project_id="source-project",
        bucket="approved.bucket",
        object_name="models/model.onnx",
        generation="123",
        region="asia-east1",
        size_bytes=42,
    )


class Registry(FakeRegistryV2):
    def __init__(self, control):
        super().__init__()
        self.control = control

    def get_control_document(self):
        return self.control


def _reserve(registry, control, **overrides):
    values = {
        "control": control,
        "source": _source(),
        "idempotency_key": "daily-model-import",
        "operation_id": "import-1",
        "owner_principal": "controller-a@example.com",
        "target_quarantine_prefix": "p/v/registry/v2/_quarantine/import-1/",
        "write_budget": 100,
        "now": "2026-07-28T00:00:00+00:00",
        "lease_ttl_seconds": 300,
    }
    values.update(overrides)
    return reserve_import_operation(registry, **values)


def test_reservation_is_idempotent_for_same_request():
    control = _control()
    registry = Registry(control)
    first = _reserve(registry, control)
    second = _reserve(registry, control, operation_id="ignored-retry-id")
    assert second == first


def test_same_idempotency_key_with_different_request_conflicts():
    control = _control()
    registry = Registry(control)
    _reserve(registry, control)
    with pytest.raises(ImportReservationConflict, match="different"):
        _reserve(registry, control, write_budget=101)


def test_expired_reservation_takeover_increments_fence():
    control = _control()
    registry = Registry(control)
    first = _reserve(registry, control, lease_ttl_seconds=1)
    takeover = _reserve(
        registry,
        control,
        owner_principal="controller-b@example.com",
        now="2026-07-28T00:00:02+00:00",
    )
    assert takeover.fencing_token == first.fencing_token + 1
    assert takeover.owner_principal == "controller-b@example.com"


def test_live_reservation_is_not_taken_over():
    control = _control()
    registry = Registry(control)
    first = _reserve(registry, control)
    retry = _reserve(
        registry,
        control,
        owner_principal="controller-b@example.com",
        now="2026-07-28T00:00:02+00:00",
    )
    assert retry == first


def test_control_change_during_transaction_fails_closed():
    expected = _control()
    registry = Registry(_control(write_epoch=4))
    with pytest.raises(ImportReservationConflict, match="control"):
        _reserve(registry, expected)


def test_nonwritable_control_is_rejected():
    control = _control(authority="v1", phase="v1_only")
    with pytest.raises(ImportReservationError, match="not writable"):
        _reserve(Registry(control), control)


def test_naive_now_is_rejected():
    control = _control()
    with pytest.raises(ImportReservationError, match="timezone-aware"):
        _reserve(Registry(control), control, now="2026-07-28T00:00:00")

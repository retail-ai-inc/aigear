from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_ticket import (
    ImportTicketError,
    issue_import_ticket,
    verify_import_ticket,
)
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportControlSnapshot,
    ImportOperationRecord,
    ImportPhase,
)

_FP = TypedId.from_bare("aa" * 32)
_REQUEST = TypedId.from_bare("bb" * 32)
_KEY = "projects/p/locations/l/keyRings/r/cryptoKeys/import/cryptoKeyVersions/1"


def _operation(**overrides):
    values = {
        "schema_version": "2.0",
        "operation_id": "import-1",
        "idempotency_key_hash": "ab" * 32,
        "request_fingerprint": _REQUEST,
        "source": ExactImportSource(
            environment_id="production",
            project_id="source-project",
            bucket="approved.bucket",
            object_name="model.onnx",
            generation="123",
            region="asia-east1",
            size_bytes=42,
        ),
        "target_environment_id": "production",
        "target_quarantine_prefix": "p/v/registry/v2/_quarantine/import-1/",
        "control_snapshot": ImportControlSnapshot(
            environment_id="production",
            environment_fingerprint=_FP,
            firestore_database_id="(default)",
            registry_binding_id="binding-1",
            registry_binding_epoch=2,
            write_epoch=3,
        ),
        "owner_principal": "controller@example.com",
        "write_budget": 100,
        "fencing_token": 1,
        "phase": ImportPhase.RESERVED,
        "revision": 1,
        "lease_expires_at": "2026-07-28T00:05:00+00:00",
    }
    values.update(overrides)
    return ImportOperationRecord(**values)


def _issue(operation=None):
    return issue_import_ticket(
        operation or _operation(),
        audience="aigear-import",
        executor_principal="import-executor@example.com",
        issued_at="2026-07-28T00:00:00+00:00",
        expires_at="2026-07-28T00:04:00+00:00",
        signer=HmacTestSigner(b"secret", key_version=_KEY),
    )


def _verify(ticket, **overrides):
    values = {
        "verifier": HmacTestVerifier(b"secret", key_version=_KEY),
        "at": "2026-07-28T00:01:00+00:00",
        "expected_audience": "aigear-import",
        "expected_executor_principal": "import-executor@example.com",
        "expected_environment_fingerprint": _FP,
    }
    values.update(overrides)
    return verify_import_ticket(ticket, **values)


def test_ticket_is_deterministic_for_same_operation_and_time():
    assert _issue() == _issue()
    _verify(_issue())


def test_ticket_cannot_outlive_operation_lease():
    with pytest.raises(ImportTicketError, match="outlive"):
        issue_import_ticket(
            _operation(),
            audience="aigear-import",
            executor_principal="import-executor@example.com",
            issued_at="2026-07-28T00:00:00+00:00",
            expires_at="2026-07-28T00:06:00+00:00",
            signer=HmacTestSigner(b"secret", key_version=_KEY),
        )


def test_only_reserved_operation_can_receive_ticket():
    with pytest.raises(ImportTicketError, match="reserved"):
        _issue(_operation(phase=ImportPhase.RECONCILING))


def test_tampered_ticket_field_is_rejected():
    signed = _issue()
    with pytest.raises(ImportTicketError, match="ticket_digest"):
        replace(
            signed,
            ticket=replace(signed.ticket, audience="attacker"),
        )


def test_tampered_signature_is_rejected():
    signed = replace(_issue(), signature_b64="dGFtcGVyZWQ=")
    with pytest.raises(ImportTicketError, match="signature"):
        _verify(signed)


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"expected_audience": "wrong"}, "audience"),
        ({"expected_executor_principal": "wrong"}, "principal"),
        (
            {"expected_environment_fingerprint": TypedId.from_bare("cc" * 32)},
            "environment",
        ),
        ({"at": "2026-07-27T23:59:00+00:00"}, "not active"),
        ({"at": "2026-07-28T00:04:00+00:00"}, "expired"),
    ],
)
def test_ticket_context_and_window_are_enforced(overrides, error):
    with pytest.raises(ImportTicketError, match=error):
        _verify(_issue(), **overrides)

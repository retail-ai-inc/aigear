from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_completion import (
    ExpectedImportPayload,
    ImportCompletionEnvelope,
    ImportCompletionError,
    authenticate_import_completion,
)
from aigear.management.v2.import_executor import (
    compute_import_payload_set_digest,
    execute_import_to_quarantine,
)
from aigear.management.v2.import_ticket import issue_import_ticket
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportControlSnapshot,
    ImportOperationRecord,
    ImportPhase,
)

_FP = TypedId.from_bare("aa" * 32)
_KEY = "projects/p/locations/l/keyRings/r/cryptoKeys/import/cryptoKeyVersions/1"
_LAYOUT = GcsLayoutV2("target-bucket", "project", "pipeline")


def _setup():
    source_gcs = FakeGcsClient()
    payload = source_gcs.put_object("bundle/model.onnx", b"model")
    manifest_bytes = (
        b'{"declaration":{"asset_type":"model"},"payloads":'
        b'[{"file_name":"model.onnx","generation":"'
        + payload.generation.encode()
        + b'","media_type":"application/octet-stream",'
        b'"object_name":"bundle/model.onnx","payload_key":"primary",'
        b'"payload_kind":"components","size_bytes":5}],"schema_version":"2.0"}'
    )
    manifest = source_gcs.put_object("bundle/source-manifest.json", manifest_bytes)
    operation = ImportOperationRecord(
        schema_version="2.0",
        operation_id="import-1",
        idempotency_key_hash="ab" * 32,
        request_fingerprint=TypedId.from_bare("bb" * 32),
        source=ExactImportSource(
            environment_id="production",
            project_id="source-project",
            bucket="approved-bucket",
            object_name=manifest.object_name,
            generation=manifest.generation,
            region="asia-east1",
            size_bytes=manifest.size_bytes,
        ),
        target_environment_id="production",
        target_quarantine_prefix=(
            "project/pipeline/registry/v2/_quarantine/import-1/"
        ),
        control_snapshot=ImportControlSnapshot(
            environment_id="production",
            environment_fingerprint=_FP,
            firestore_database_id="(default)",
            registry_binding_id="binding-1",
            registry_binding_epoch=1,
            write_epoch=1,
        ),
        owner_principal="controller@example.com",
        write_budget=100,
        fencing_token=3,
        phase=ImportPhase.RESERVED,
        revision=1,
        lease_expires_at="2026-07-28T00:05:00+00:00",
    )
    signed_ticket = issue_import_ticket(
        operation,
        audience="aigear-import",
        executor_principal="import-executor@example.com",
        issued_at="2026-07-28T00:00:00+00:00",
        expires_at="2026-07-28T00:04:00+00:00",
        signer=HmacTestSigner(b"secret", key_version=_KEY),
    )
    completion = execute_import_to_quarantine(
        signed_ticket,
        source_gcs=source_gcs,
        quarantine_gcs=FakeGcsClient(),
        layout=_LAYOUT,
        verifier=HmacTestVerifier(b"secret", key_version=_KEY),
        at="2026-07-28T00:01:00+00:00",
        expected_audience="aigear-import",
        expected_executor_principal="import-executor@example.com",
        expected_environment_fingerprint=_FP,
    )
    operation = replace(
        operation,
        phase=ImportPhase.QUARANTINING,
        ticket=signed_ticket.ticket,
        revision=2,
    )
    expected = (
        ExpectedImportPayload(
            payload_kind="components",
            payload_key="primary",
            file_name="model.onnx",
            media_type="application/octet-stream",
            source_object_name=payload.object_name,
            source_generation=payload.generation,
            source_size_bytes=payload.size_bytes,
        ),
    )
    envelope = ImportCompletionEnvelope(
        completion=completion,
        message_id="message-1",
        audience="aigear-completion",
        publisher_principal="import-executor@example.com",
        issued_at="2026-07-28T00:01:01+00:00",
        expires_at="2026-07-28T00:02:00+00:00",
    )
    return operation, signed_ticket, expected, envelope


def _authenticate(operation, ticket, expected, envelope, **overrides):
    values = {
        "signed_ticket": ticket,
        "operation": operation,
        "expected_payloads": expected,
        "layout": _LAYOUT,
        "oidc_token": "token",
        "expected_audience": "aigear-completion",
        "allowed_publishers": ("import-executor@example.com",),
        "ticket_verifier": HmacTestVerifier(b"secret", key_version=_KEY),
        "at": "2026-07-28T00:01:10+00:00",
        "token_verifier": lambda token, **kwargs: "import-executor@example.com",
    }
    values.update(overrides)
    return authenticate_import_completion(envelope, **values)


def test_authenticated_completion_returns_bound_registry_record():
    operation, ticket, expected, envelope = _setup()

    record = _authenticate(operation, ticket, expected, envelope)

    assert record.operation_id == operation.operation_id
    assert record.fencing_token == operation.fencing_token
    assert record.payload_set_digest == envelope.completion.payload_set_digest


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda op, env: replace(env, audience="wrong"), "audience"),
        (
            lambda op, env: replace(env, publisher_principal="attacker@example.com"),
            "publisher",
        ),
        (
            lambda op, env: replace(
                env,
                completion=replace(env.completion, fencing_token=2),
            ),
            "fence",
        ),
        (
            lambda op, env: replace(
                env,
                completion=replace(
                    env.completion,
                    environment_fingerprint=TypedId.from_bare("cc" * 32),
                ),
            ),
            "fence",
        ),
    ],
)
def test_context_replay_and_stale_fence_are_rejected(change, error):
    operation, ticket, expected, envelope = _setup()
    changed = change(operation, envelope)

    with pytest.raises(ImportCompletionError, match=error):
        _authenticate(operation, ticket, expected, changed)


def test_verified_oidc_principal_must_match_envelope_and_ticket():
    operation, ticket, expected, envelope = _setup()

    with pytest.raises(ImportCompletionError, match="publisher"):
        _authenticate(
            operation,
            ticket,
            expected,
            envelope,
            token_verifier=lambda token, **kwargs: "attacker@example.com",
        )


@pytest.mark.parametrize(
    "at,error",
    [
        ("2026-07-28T00:00:59+00:00", "not currently valid"),
        ("2026-07-28T00:02:00+00:00", "not currently valid"),
    ],
)
def test_completion_message_window_is_closed(at, error):
    operation, ticket, expected, envelope = _setup()
    with pytest.raises(ImportCompletionError, match=error):
        _authenticate(operation, ticket, expected, envelope, at=at)


def test_message_cannot_claim_completion_after_ticket_expiry():
    operation, ticket, expected, envelope = _setup()
    changed = replace(envelope, expires_at="2026-07-28T00:06:00+00:00")

    with pytest.raises(ImportCompletionError, match="ticket window"):
        _authenticate(operation, ticket, expected, changed)


def test_missing_unknown_and_duplicate_payloads_are_rejected():
    operation, ticket, expected, envelope = _setup()
    with pytest.raises(ImportCompletionError, match="missing"):
        _authenticate(
            operation,
            ticket,
            expected
            + (
                replace(
                    expected[0],
                    payload_kind="attachments",
                    payload_key="schema",
                    file_name="schema.json",
                ),
            ),
            envelope,
        )
    unknown_payload = replace(
        envelope.completion.payloads[0],
        payload_kind="attachments",
        payload_key="schema",
        file_name="schema.json",
        object_name=(
            "project/pipeline/registry/v2/_quarantine/import-1/"
            "attachments/schema/schema.json"
        ),
    )
    unknown_payloads = envelope.completion.payloads + (unknown_payload,)
    unknown_completion = replace(
        envelope.completion,
        payloads=unknown_payloads,
        payload_set_digest=compute_import_payload_set_digest(
            envelope.completion.source_manifest, unknown_payloads
        ),
    )
    with pytest.raises(ImportCompletionError, match="unknown"):
        _authenticate(
            operation,
            ticket,
            expected,
            replace(envelope, completion=unknown_completion),
        )

    duplicate_payloads = envelope.completion.payloads * 2
    duplicate_completion = replace(
        envelope.completion,
        payloads=duplicate_payloads,
        payload_set_digest=compute_import_payload_set_digest(
            envelope.completion.source_manifest, duplicate_payloads
        ),
    )
    with pytest.raises(ImportCompletionError, match="duplicate"):
        _authenticate(
            operation,
            ticket,
            expected,
            replace(envelope, completion=duplicate_completion),
        )


def test_descriptor_target_path_cannot_be_replayed():
    operation, ticket, expected, envelope = _setup()
    changed_payload = replace(
        envelope.completion.payloads[0],
        object_name="project/other/registry/v2/_quarantine/import-1/components/primary/model.onnx",
    )
    changed_completion = replace(
        envelope.completion,
        payloads=(changed_payload,),
        payload_set_digest=compute_import_payload_set_digest(
            envelope.completion.source_manifest, (changed_payload,)
        ),
    )

    with pytest.raises(ImportCompletionError, match="inventory"):
        _authenticate(
            operation,
            ticket,
            expected,
            replace(envelope, completion=changed_completion),
        )

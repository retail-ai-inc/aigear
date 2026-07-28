from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aigear.management.v2.attestation import (
    HmacTestSigner,
    HmacTestVerifier,
    verify_attestation,
)
from aigear.management.v2.content_policy import (
    ContentGovernance,
    FormatRule,
    ImportContentPolicy,
    ProducerEvidence,
    ScanVerdict,
    create_scanner_result,
    inspect_import_content,
)
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_executor import execute_import_to_quarantine
from aigear.management.v2.import_prepare import (
    ImportPrepareError,
    prepare_external_import,
)
from aigear.management.v2.import_ticket import issue_import_ticket
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportControlSnapshot,
    ImportOperationRecord,
    ImportPhase,
)

_FP = TypedId.from_bare("aa" * 32)
_ZERO = TypedId.from_bare("00" * 32)
_TICKET_KEY = "test/import-ticket/1"
_MANIFEST_KEY = "test/manifest/1"
_PROVENANCE_KEY = "test/provenance/1"
_AT = "2026-07-28T00:01:00+00:00"
_LAYOUT = GcsLayoutV2("target-bucket", "project", "pipeline")


def _governance():
    return {
        "owner": "ml-team",
        "data_classification": "internal",
        "purpose": "fraud-detection",
        "license_or_consent_ref": "policy/license/1",
        "residency": "asia-east1",
        "retention_class": "standard",
        "legal_hold": False,
        "policy_version": "policy-7",
    }


def _declaration():
    return {
        "asset_type": "model",
        "name": "fraud-model",
        "display_version": "v7",
        "components": [
            {"payload_key": "primary", "role": "model", "logical_name": "primary"}
        ],
        "attachments": [
            {
                "payload_key": "schema",
                "attachment_kind": "schema",
                "logical_name": "input-schema",
            }
        ],
        "producer_spec": {
            "source_commit": "abc123",
            "image_digest": _ZERO.typed,
            "code_digest": TypedId.from_bare("11" * 32).typed,
            "config_digest": TypedId.from_bare("22" * 32).typed,
        },
        "schema_contract_digest": TypedId.from_bare("33" * 32).typed,
        "runtime_contract_digest": TypedId.from_bare("44" * 32).typed,
        "policy_version": "policy-7",
        "governance": _governance(),
    }


class Scanner:
    scanner_id = "scanner"
    scanner_version = "1"

    def scan(self, data, *, media_type, expected_sha256):
        return create_scanner_result(
            scanner_id=self.scanner_id,
            scanner_version=self.scanner_version,
            payload_sha256=expected_sha256,
            verdict=ScanVerdict.CLEAN,
            finding_codes=(),
            completed_at=_AT,
        )


def _setup(*, declaration=None):
    source_gcs = FakeGcsClient()
    model = source_gcs.put_object("bundle/model.onnx", b"model")
    schema = source_gcs.put_object("bundle/schema.json", b'{"type":"object"}')
    source_manifest = source_gcs.put_object(
        "bundle/source-manifest.json",
        json.dumps(
            {
                "schema_version": "2.0",
                "declaration": declaration or _declaration(),
                "payloads": [
                    {
                        "payload_kind": "components",
                        "payload_key": "primary",
                        "file_name": "model.onnx",
                        "media_type": "model/onnx",
                        "object_name": model.object_name,
                        "generation": model.generation,
                        "size_bytes": model.size_bytes,
                    },
                    {
                        "payload_kind": "attachments",
                        "payload_key": "schema",
                        "file_name": "schema.json",
                        "media_type": "application/schema+json",
                        "object_name": schema.object_name,
                        "generation": schema.generation,
                        "size_bytes": schema.size_bytes,
                    },
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    )
    operation = ImportOperationRecord(
        schema_version="2.0",
        operation_id="import-1",
        idempotency_key_hash="ab" * 32,
        request_fingerprint=TypedId.from_bare("bb" * 32),
        source=ExactImportSource(
            environment_id="production",
            project_id="source-project",
            bucket="approved-bucket",
            object_name=source_manifest.object_name,
            generation=source_manifest.generation,
            region="asia-east1",
            size_bytes=source_manifest.size_bytes,
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
        fencing_token=4,
        phase=ImportPhase.RESERVED,
        revision=1,
        lease_expires_at="2026-07-28T00:05:00+00:00",
    )
    ticket = issue_import_ticket(
        operation,
        audience="aigear-import",
        executor_principal="import-executor@example.com",
        issued_at="2026-07-28T00:00:00+00:00",
        expires_at="2026-07-28T00:04:00+00:00",
        signer=HmacTestSigner(b"ticket", key_version=_TICKET_KEY),
    )
    quarantine = FakeGcsClient()
    completion = execute_import_to_quarantine(
        ticket,
        source_gcs=source_gcs,
        quarantine_gcs=quarantine,
        layout=_LAYOUT,
        verifier=HmacTestVerifier(b"ticket", key_version=_TICKET_KEY),
        at=_AT,
        expected_audience="aigear-import",
        expected_executor_principal="import-executor@example.com",
        expected_environment_fingerprint=_FP,
    )
    governance = ContentGovernance(**_governance())
    inspection = inspect_import_content(
        completion,
        quarantine_gcs=quarantine,
        policy=ImportContentPolicy(
            policy_version="policy-7",
            format_rules=(
                FormatRule("onnx", ("model/onnx",), (".onnx",), 100),
                FormatRule(
                    "json-schema",
                    ("application/schema+json",),
                    (".json",),
                    100,
                ),
            ),
            allowed_scanners=(("scanner", "1"),),
            allowed_producers=("trusted-build",),
            required_logical_paths=(
                ("components", "primary"),
                ("attachments", "schema"),
            ),
        ),
        governance=governance,
        producer=ProducerEvidence("trusted-build", TypedId.from_bare("55" * 32), True),
        scanner=Scanner(),
        inspected_at=_AT,
    )
    operation = replace(
        operation,
        phase=ImportPhase.INSPECTING,
        ticket=ticket.ticket,
        completion=completion.to_record(),
        revision=3,
    )
    return operation, completion, inspection, quarantine


def _prepare(operation, completion, inspection, quarantine, **overrides):
    values = {
        "quarantine_gcs": quarantine,
        "quarantine_bucket": "target-bucket",
        "manifest_signer": HmacTestSigner(b"manifest", key_version=_MANIFEST_KEY),
        "provenance_signer": HmacTestSigner(
            b"provenance", key_version=_PROVENANCE_KEY
        ),
        "prepared_at": "2026-07-28T00:01:30+00:00",
    }
    values.update(overrides)
    return prepare_external_import(
        operation, completion, inspection, **values
    )


def test_prepare_builds_canonical_manifest_and_domain_separated_attestations():
    operation, completion, inspection, quarantine = _setup()

    prepared = _prepare(operation, completion, inspection, quarantine)

    assert prepared.canonical_manifest["asset_type"] == "model"
    assert prepared.canonical_manifest["components"][0]["role"] == "model"
    assert prepared.declaration.display_version == "v7"
    assert len(prepared.payloads) == 2
    assert (
        prepared.manifest_integrity_attestation.attestation_id
        != prepared.source_provenance_attestation.attestation_id
    )
    verify_attestation(
        prepared.manifest_integrity_attestation,
        HmacTestVerifier(b"manifest", key_version=_MANIFEST_KEY),
    )
    verify_attestation(
        prepared.source_provenance_attestation,
        HmacTestVerifier(b"provenance", key_version=_PROVENANCE_KEY),
    )


def test_quarantine_payload_generation_drift_fails_the_whole_bundle():
    operation, completion, inspection, quarantine = _setup()
    descriptor = completion.payloads[0]
    changed = quarantine.put_object(descriptor.object_name, b"changed")
    changed_descriptor = replace(
        descriptor,
        generation=changed.generation,
        sha256=changed.sha256,
        crc32c=changed.crc32c,
        size_bytes=changed.size_bytes,
    )
    from aigear.management.v2.import_executor import compute_import_payload_set_digest

    changed_completion = replace(
        completion,
        payloads=(changed_descriptor, completion.payloads[1]),
        payload_set_digest=compute_import_payload_set_digest(
            completion.source_manifest,
            (changed_descriptor, completion.payloads[1]),
        ),
    )
    with pytest.raises(ImportPrepareError, match="operation and completion"):
        _prepare(operation, changed_completion, inspection, quarantine)

    # A later live generation does not change the exact prepared result.
    prepared = _prepare(operation, completion, inspection, quarantine)
    assert prepared.payloads[0].quarantine_generation == descriptor.generation


def test_exact_generation_byte_or_inspection_tamper_is_rejected():
    operation, completion, inspection, quarantine = _setup()
    descriptor = completion.payloads[0]
    corrupted = replace(descriptor, sha256="00" * 32)
    from aigear.management.v2.import_executor import compute_import_payload_set_digest

    changed_completion = replace(
        completion,
        payloads=(corrupted, completion.payloads[1]),
        payload_set_digest=compute_import_payload_set_digest(
            completion.source_manifest, (corrupted, completion.payloads[1])
        ),
    )
    changed_operation = replace(
        operation,
        completion=changed_completion.to_record(),
    )
    with pytest.raises(ImportPrepareError, match="inspection"):
        _prepare(
            changed_operation,
            changed_completion,
            inspection,
            quarantine,
        )


def test_declaration_must_cover_payloads_and_match_inspected_governance():
    changed_declaration = _declaration()
    changed_declaration["governance"] = {
        **changed_declaration["governance"],
        "owner": "attacker",
    }
    operation, completion, inspection, quarantine = _setup(
        declaration=changed_declaration
    )
    with pytest.raises(ImportPrepareError, match="governance"):
        _prepare(operation, completion, inspection, quarantine)

    incomplete_declaration = _declaration()
    incomplete_declaration["attachments"] = []
    operation, completion, inspection, quarantine = _setup(
        declaration=incomplete_declaration
    )
    with pytest.raises(ImportPrepareError, match="complete payload set"):
        _prepare(operation, completion, inspection, quarantine)


def test_signers_are_called_before_prepared_value_can_be_committed():
    operation, completion, inspection, quarantine = _setup()

    class FailingSigner:
        key_version = "test/failing/1"

        def sign_sha256_digest(self, digest):
            raise RuntimeError("KMS unavailable")

    with pytest.raises(RuntimeError, match="KMS unavailable"):
        _prepare(
            operation,
            completion,
            inspection,
            quarantine,
            manifest_signer=FailingSigner(),
        )

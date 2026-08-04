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
from aigear.management.v2.import_adoption import (
    ImportAdoptionError,
    adopt_import_blobs,
)
from aigear.management.v2.import_identity_reservation import (
    ImportIdentityReservationError,
    import_identity_journal_prefix,
    reserve_import_identity,
)
from aigear.management.v2.import_commit import (
    ImportCommitError,
    commit_external_import,
    verify_import_commit_evidence,
)
from aigear.management.v2.import_recovery import (
    ImportRecoveryAction,
    StaleImportFenceError,
    build_import_cleanup_intent,
    fail_import_with_cleanup,
    reconcile_import_once,
)
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
from aigear.management.v2.records.blob_claim import BlobClaim, ClaimState
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.security_journal import (
    SecurityJournal,
    SecurityJournalConflictError,
)
from aigear.management.v2.records.label import compute_label_id

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


def _setup(*, declaration=None, model_data=b"model"):
    source_gcs = FakeGcsClient()
    model = source_gcs.put_object("bundle/model.onnx", model_data)
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


def _adoption_setup():
    operation, completion, inspection, quarantine = _setup()
    prepared = _prepare(operation, completion, inspection, quarantine)
    operation = replace(
        operation,
        phase=ImportPhase.PREPARING,
        revision=4,
    )
    registry = FakeRegistryV2()
    registry.put_import_operation(operation)
    return registry, operation, prepared, quarantine


def test_import_adoption_claims_copies_and_rechecks_exact_canonical_generation():
    registry, operation, prepared, gcs = _adoption_setup()

    adopted = adopt_import_blobs(
        registry,
        gcs,
        _LAYOUT,
        operation,
        prepared,
        location_signer=HmacTestSigner(b"location", key_version="test/location/1"),
        existing_location_verifier=HmacTestVerifier(
            b"location", key_version="test/location/1"
        ),
        now="2026-07-28T00:02:00+00:00",
    )

    assert len(adopted.materials) == 2
    for material in adopted.materials:
        claim = registry.get_blob_claim(material.blob.blob_id)
        assert claim.state is ClaimState.ADOPTING
        assert claim.expected_generation == material.blob.generation
        exact = gcs.get_object(
            material.blob.object_name, generation=material.blob.generation
        )
        assert exact.sha256 == material.blob.blob_id.bare


def test_orphan_delete_claim_wins_before_import_copy():
    registry, operation, prepared, gcs = _adoption_setup()
    payload = prepared.payloads[0]
    registry.put_blob_claim(
        BlobClaim(
            blob_id=payload.blob_id,
            claim_epoch=1,
            fencing_token=99,
            operation_id="orphan-delete",
            expected_object_name=_LAYOUT.canonical_blob(payload.blob_id),
            request_digest=TypedId.from_bare("77" * 32),
            state=ClaimState.DELETE_INTENT,
        )
    )

    with pytest.raises(ImportAdoptionError, match="claimed by another"):
        adopt_import_blobs(
            registry,
            gcs,
            _LAYOUT,
            operation,
            prepared,
            location_signer=HmacTestSigner(),
            existing_location_verifier=HmacTestVerifier(),
            now="2026-07-28T00:02:00+00:00",
        )
    assert gcs.get_live_object(_LAYOUT.canonical_blob(payload.blob_id)) is None


def test_existing_blob_requires_verified_current_location_chain():
    registry, operation, prepared, gcs = _adoption_setup()
    signer = HmacTestSigner(b"location", key_version="test/location/1")
    verifier = HmacTestVerifier(b"location", key_version="test/location/1")
    adopted = adopt_import_blobs(
        registry,
        gcs,
        _LAYOUT,
        operation,
        prepared,
        location_signer=signer,
        existing_location_verifier=verifier,
        now="2026-07-28T00:02:00+00:00",
    )
    for material in adopted.materials:
        registry.put_attestation(material.location_attestation)
        registry.put_blob_location_revision(material.location_revision)
        registry.put_blob(material.blob)
        claim = registry.get_blob_claim(material.blob.blob_id)
        registry.put_blob_claim(replace(claim, state=ClaimState.CONSUMED))

    replay = adopt_import_blobs(
        registry,
        gcs,
        _LAYOUT,
        operation,
        prepared,
        location_signer=signer,
        existing_location_verifier=verifier,
        now="2026-07-28T00:03:00+00:00",
    )
    assert all(value.reused_existing for value in replay.materials)

    blob = replay.materials[0].blob
    registry._blobs[blob.blob_id] = replace(blob, generation="999")
    with pytest.raises(ImportAdoptionError, match="location chain"):
        adopt_import_blobs(
            registry,
            gcs,
            _LAYOUT,
            operation,
            prepared,
            location_signer=signer,
            existing_location_verifier=verifier,
            now="2026-07-28T00:03:00+00:00",
        )


def _identity_journal(gcs, prepared):
    label_id = compute_label_id(
        prepared.declaration.asset_type,
        prepared.declaration.name,
        prepared.declaration.display_version,
    )
    return SecurityJournal(
        gcs=gcs,
        object_prefix=import_identity_journal_prefix("security", label_id),
        environment_fingerprint=_FP,
        signer=HmacTestSigner(b"journal", key_version="test/journal/1"),
        verifier=HmacTestVerifier(b"journal", key_version="test/journal/1"),
    )


def test_import_identity_reservation_is_journal_first_and_ack_loss_idempotent():
    operation, completion, inspection, quarantine = _setup()
    prepared = _prepare(operation, completion, inspection, quarantine)
    journal = _identity_journal(FakeGcsClient(), prepared)

    first = reserve_import_identity(prepared, journal=journal, issued_at=_AT)
    retry = reserve_import_identity(prepared, journal=journal, issued_at=_AT)

    assert retry == first
    assert first.asset_version_id == prepared.asset_version_id
    assert journal.read_head().entry_id == first.journal_entry_id


def test_same_label_cannot_be_reinterpreted_after_registry_rollback():
    operation, completion, inspection, quarantine = _setup()
    prepared = _prepare(operation, completion, inspection, quarantine)
    journal = _identity_journal(FakeGcsClient(), prepared)
    reserve_import_identity(prepared, journal=journal, issued_at=_AT)
    other_operation, other_completion, other_inspection, other_quarantine = _setup(
        model_data=b"different-model"
    )
    changed = _prepare(
        other_operation, other_completion, other_inspection, other_quarantine
    )

    with pytest.raises(SecurityJournalConflictError, match="different evidence"):
        reserve_import_identity(changed, journal=journal, issued_at=_AT)


def test_identity_reservation_rejects_wrong_per_label_journal():
    operation, completion, inspection, quarantine = _setup()
    prepared = _prepare(operation, completion, inspection, quarantine)
    wrong_label = TypedId.from_bare("99" * 32)
    journal = SecurityJournal(
        gcs=FakeGcsClient(),
        object_prefix=import_identity_journal_prefix("security", wrong_label),
        environment_fingerprint=_FP,
        signer=HmacTestSigner(),
        verifier=HmacTestVerifier(),
    )

    with pytest.raises(ImportIdentityReservationError, match="prefix"):
        reserve_import_identity(prepared, journal=journal, issued_at=_AT)


def _commit_setup():
    registry, operation, prepared, gcs = _adoption_setup()
    location_signer = HmacTestSigner(b"location", key_version="test/location/1")
    location_verifier = HmacTestVerifier(
        b"location", key_version="test/location/1"
    )
    adopted = adopt_import_blobs(
        registry,
        gcs,
        _LAYOUT,
        operation,
        prepared,
        location_signer=location_signer,
        existing_location_verifier=location_verifier,
        now="2026-07-28T00:02:00+00:00",
    )
    journal = _identity_journal(FakeGcsClient(), prepared)
    reservation = reserve_import_identity(prepared, journal=journal, issued_at=_AT)
    entry = journal.read_entry(
        reservation.journal_entry_object_name,
        reservation.journal_entry_generation,
    )
    evidence = verify_import_commit_evidence(
        prepared,
        adopted,
        reservation,
        entry,
        manifest_verifier=HmacTestVerifier(
            b"manifest", key_version=_MANIFEST_KEY
        ),
        provenance_verifier=HmacTestVerifier(
            b"provenance", key_version=_PROVENANCE_KEY
        ),
        location_verifier=location_verifier,
    )
    committing = replace(
        operation,
        phase=ImportPhase.COMMITTING,
        revision=5,
    )
    registry.put_import_operation(committing)
    return registry, committing, evidence


def test_atomic_import_commit_persists_verified_asset_label_edges_and_result_refs():
    registry, operation, evidence = _commit_setup()

    result = commit_external_import(
        registry,
        _LAYOUT,
        operation,
        evidence,
        committed_at="2026-07-28T00:03:00+00:00",
    )

    assert result.phase is ImportPhase.SUCCEEDED
    assert result.result_asset_version_id == evidence.prepared.asset_version_id
    assert result.result_label_id == evidence.identity_reservation.label_id
    asset = registry.get_asset_version(result.result_asset_version_id)
    assert asset.trust_state.value == "verified"
    assert registry.get_label(result.result_label_id).asset_version_id == asset.asset_version_id
    assert registry.get_import_provenance(
        asset.asset_version_id,
        result.result_source_provenance_attestation_ref,
    ).operation_id == operation.operation_id
    assert all(
        registry.get_blob(material.blob.blob_id) == material.blob
        for material in evidence.adopted.materials
    )
    assert all(
        registry.get_blob_claim(material.blob.blob_id).state is ClaimState.CONSUMED
        for material in evidence.adopted.materials
    )


def test_write_budget_rejection_leaves_registry_business_facts_uncommitted():
    registry, operation, evidence = _commit_setup()
    low_budget = replace(operation, write_budget=1)
    registry.put_import_operation(low_budget)

    with pytest.raises(ImportCommitError, match="budget"):
        commit_external_import(
            registry,
            _LAYOUT,
            low_budget,
            evidence,
            committed_at="2026-07-28T00:03:00+00:00",
        )

    assert registry.get_asset_version(evidence.prepared.asset_version_id) is None
    assert all(
        registry.get_blob(material.blob.blob_id) is None
        for material in evidence.adopted.materials
    )


def test_recovery_rebuilds_ack_lost_success_only_from_operation_result_refs():
    registry, operation, evidence = _commit_setup()
    succeeded = commit_external_import(
        registry,
        _LAYOUT,
        operation,
        evidence,
        committed_at="2026-07-28T00:03:00+00:00",
    )

    recovered = reconcile_import_once(
        registry,
        idempotency_key_hash=succeeded.idempotency_key_hash,
        expected_request_fingerprint=succeeded.request_fingerprint,
        expected_fencing_token=succeeded.fencing_token,
        now="2026-07-28T00:03:01+00:00",
    )

    assert recovered.action is ImportRecoveryAction.REBUILT_SUCCEEDED
    assert recovered.result.asset_version.asset_version_id == succeeded.result_asset_version_id
    assert recovered.result.label.label_id == succeeded.result_label_id


def test_canonical_side_effect_without_registry_commit_is_not_guessed_as_success():
    registry, operation, evidence = _commit_setup()

    recovered = reconcile_import_once(
        registry,
        idempotency_key_hash=operation.idempotency_key_hash,
        expected_request_fingerprint=operation.request_fingerprint,
        expected_fencing_token=operation.fencing_token,
        now="2026-07-28T00:03:01+00:00",
    )

    assert recovered.action is ImportRecoveryAction.RECONCILING
    assert recovered.operation.phase is ImportPhase.RECONCILING
    assert registry.get_asset_version(evidence.prepared.asset_version_id) is None


def test_recovery_rejects_stale_fence():
    registry, operation, _ = _commit_setup()
    with pytest.raises(StaleImportFenceError, match="stale"):
        reconcile_import_once(
            registry,
            idempotency_key_hash=operation.idempotency_key_hash,
            expected_request_fingerprint=operation.request_fingerprint,
            expected_fencing_token=operation.fencing_token - 1,
            now="2026-07-28T00:03:01+00:00",
        )


def test_failure_atomically_persists_generation_bound_cleanup_intent():
    operation, completion, inspection, quarantine = _setup()
    intent = build_import_cleanup_intent(
        operation,
        quarantine_bucket="target-bucket",
        completion=completion,
        created_at="2026-07-28T00:02:00+00:00",
        eligible_after="2026-07-29T00:02:00+00:00",
    )
    registry = FakeRegistryV2()
    registry.put_import_operation(operation)

    failed = fail_import_with_cleanup(
        registry,
        operation,
        intent,
        error_class="ContentRejected",
        error_summary="scanner policy rejected payload",
        failed_at="2026-07-28T00:02:00+00:00",
    )

    assert failed.phase is ImportPhase.FAILED
    assert registry.get_import_cleanup_intent(intent.intent_id) == intent
    assert all(value.generation for value in intent.objects)
    assert fail_import_with_cleanup(
        registry,
        failed,
        intent,
        error_class="ignored",
        error_summary="ignored",
        failed_at="2026-07-28T00:02:01+00:00",
    ) == failed

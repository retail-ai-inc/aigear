from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aigear.management.pipeline_asset import (
    LocalImportPayload,
    PipelineAssetManagement,
    PipelineAssetManagementError,
)
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.environment import (
    EnvironmentIdentity,
    RegistryBinding,
    compute_environment_fingerprint,
)
from aigear.management.v2.external_source import (
    AllowedExternalBucket,
    ExternalSourcePolicy,
)
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_api import (
    ExternalObjectMetadata,
    GoogleExternalSourceInspector,
    LocalImportConflict,
)
from aigear.management.v2.import_recovery import (
    ImportRecoveryAction,
    ImportRecoveryOutcome,
    RecoveredImportResult,
)
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportCompletionRecord,
    ImportPhase,
    ImportTicketRecord,
)

_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _identity() -> EnvironmentIdentity:
    return EnvironmentIdentity(
        environment_id="production",
        gcp_project_number="123456789012",
        project_name="fraud_pipeline",
        pipeline_version="v7",
        asset_bucket_name="target-assets",
        asset_bucket_location="asia-east1",
        kms_trust_domain="projects/p/locations/l/keyRings/r",
    )


def _control(*, capabilities=("asset_import_v2",)) -> ControlDocument:
    fingerprint = compute_environment_fingerprint(_identity())
    return ControlDocument(
        schema_version="2.0",
        environment_id="production",
        authority="v2",
        phase="v2_authoritative",
        write_epoch=3,
        min_reader_version="2.0",
        min_writer_version="2.0",
        required_capabilities=capabilities,
        environment_fingerprint=fingerprint,
        registry_binding=RegistryBinding(
            firestore_database_id="registry",
            registry_binding_id="A" * 43,
            registry_binding_epoch=2,
            bound_environment_fingerprint=fingerprint,
        ),
        registry_bound_by="bootstrap@example.com",
    )


class _Registry(FakeRegistryV2):
    def __init__(self, control: ControlDocument):
        super().__init__()
        self.control = control

    def get_control_document(self):
        return self.control


class _Inspector:
    def __init__(self, metadata=None):
        self.metadata = metadata or ExternalObjectMetadata("asia-east1", 42)
        self.seen = []

    def inspect(self, source):
        self.seen.append(source)
        return self.metadata


class _GoogleBlob:
    generation = 17
    size = 42

    def __init__(self):
        self.reload_generation = None

    def reload(self, *, if_generation_match):
        self.reload_generation = if_generation_match


class _GoogleBucket:
    location = "ASIA-EAST1"

    def __init__(self):
        self.value = _GoogleBlob()
        self.requested = None
        self.reloaded = False

    def blob(self, object_name, *, generation):
        self.requested = (object_name, generation)
        return self.value

    def reload(self):
        self.reloaded = True


class _GoogleClient:
    def __init__(self):
        self.value = _GoogleBucket()
        self.requested = None

    def bucket(self, name):
        self.requested = name
        return self.value


def _policy() -> ExternalSourcePolicy:
    return ExternalSourcePolicy(
        target_environment_id="production",
        allowed_buckets=(
            AllowedExternalBucket(
                project_id="source-project",
                bucket="approved-source",
                region="asia-east1",
            ),
        ),
        max_size_bytes=1024,
    )


def test_google_external_inspector_reads_exact_generation_metadata():
    client = _GoogleClient()
    source = ExactImportSource(
        environment_id="production",
        project_id="source-project",
        bucket="approved-source",
        object_name="bundles/source-manifest.json",
        generation="17",
        region="asia-east1",
        size_bytes=0,
    )
    metadata = GoogleExternalSourceInspector(client=client).inspect(source)
    assert metadata == ExternalObjectMetadata("asia-east1", 42)
    assert client.requested == "approved-source"
    assert client.value.requested == ("bundles/source-manifest.json", 17)
    assert client.value.value.reload_generation == 17


def _manager(
    *,
    control=None,
    gcs=None,
    inspector=None,
    policy=None,
) -> PipelineAssetManagement:
    control = control or _control()
    return PipelineAssetManagement(
        _identity(),
        registry=_Registry(control),
        gcs=gcs or FakeGcsClient(),
        control_document=control,
        external_source_policy=policy,
        external_source_inspector=inspector,
    )


def _declaration():
    return {
        "asset_type": "model",
        "name": "fraud-model",
        "display_version": "v7",
        "components": [
            {"payload_key": "primary", "role": "model", "logical_name": "model"}
        ],
        "attachments": [],
        "producer_spec": {
            "source_commit": "abc123",
            "image_digest": TypedId.from_bare("11" * 32).typed,
            "code_digest": TypedId.from_bare("22" * 32).typed,
            "config_digest": TypedId.from_bare("33" * 32).typed,
        },
        "schema_contract_digest": TypedId.from_bare("44" * 32).typed,
        "runtime_contract_digest": TypedId.from_bare("55" * 32).typed,
        "policy_version": "policy-7",
        "governance": {
            "owner": "ml@example.com",
            "data_classification": "internal",
            "purpose": "fraud-detection",
            "license_or_consent_ref": "contract-7",
            "residency": "asia-east1",
            "retention_class": "standard",
            "legal_hold": False,
            "policy_version": "policy-7",
        },
    }


def test_external_import_reserves_exact_source_idempotently():
    inspector = _Inspector()
    manager = _manager(inspector=inspector, policy=_policy())
    values = dict(
        source_uri="gs://approved-source/bundles/source-manifest.json#17",
        idempotency_key="daily-import",
        owner_principal="controller@example.com",
        now=_NOW,
    )

    first = manager.import_external(**values)
    second = manager.import_external(**values)

    assert first == second
    assert first.result is None
    assert first.operation.phase is ImportPhase.RESERVED
    assert first.operation.source.generation == "17"
    assert first.operation.source.size_bytes == 42
    assert first.operation.target_quarantine_prefix.endswith(
        f"_quarantine/{first.operation.operation_id}/"
    )
    assert len(inspector.seen) == 1
    assert all(item.size_bytes == 0 for item in inspector.seen)


def test_external_import_fails_before_inspection_without_capability():
    inspector = _Inspector()
    manager = _manager(
        control=_control(capabilities=("pipeline_v2",)),
        inspector=inspector,
        policy=_policy(),
    )
    with pytest.raises(PipelineAssetManagementError, match="capability"):
        manager.import_external(
            "gs://approved-source/bundles/source-manifest.json#17",
            idempotency_key="daily-import",
            owner_principal="controller@example.com",
            now=_NOW,
        )
    assert inspector.seen == []


def test_external_import_requires_policy_and_trusted_inspector():
    manager = _manager()
    with pytest.raises(PipelineAssetManagementError, match="trusted"):
        manager.import_external(
            "gs://approved-source/bundles/source-manifest.json#17",
            idempotency_key="daily-import",
            owner_principal="controller@example.com",
            now=_NOW,
        )


def test_import_requires_control_document_before_external_lookup():
    inspector = _Inspector()
    manager = PipelineAssetManagement(
        _identity(),
        external_source_policy=_policy(),
        external_source_inspector=inspector,
    )
    with pytest.raises(PipelineAssetManagementError, match="control_document"):
        manager.import_external(
            "gs://approved-source/bundles/source-manifest.json#17",
            idempotency_key="daily-import",
            owner_principal="controller@example.com",
            now=_NOW,
        )
    assert inspector.seen == []


def test_local_upload_is_create_only_and_operation_scoped(tmp_path):
    source = tmp_path / "model.onnx"
    source.write_bytes(b"model-v7")
    gcs = FakeGcsClient()
    manager = _manager(gcs=gcs)
    values = dict(
        path=source,
        declaration=_declaration(),
        payload_key="primary",
        media_type="model/onnx",
        idempotency_key="manual-model-v7",
        owner_principal="operator@example.com",
        now=_NOW,
    )

    first = manager.upload_asset(**values)
    second = manager.upload_asset(**values)

    assert first == second
    operation = first.operation
    prefix = operation.target_quarantine_prefix
    assert operation.phase is ImportPhase.RESERVED
    assert operation.source.object_name == f"{prefix}source-manifest.json"
    assert gcs.get_live_object(f"{prefix}components/primary/model.onnx").generation == "1"
    assert gcs.get_live_object(f"{prefix}source-manifest.json").generation == "1"
    assert "/_quarantine/" in operation.source.object_name


def test_local_upload_missing_capability_has_no_gcs_side_effect(tmp_path):
    source = tmp_path / "model.onnx"
    source.write_bytes(b"model-v7")
    gcs = FakeGcsClient()
    manager = _manager(
        control=_control(capabilities=("pipeline_v2",)),
        gcs=gcs,
    )
    with pytest.raises(PipelineAssetManagementError, match="capability"):
        manager.upload_asset(
            source,
            declaration=_declaration(),
            payload_key="primary",
            media_type="model/onnx",
            idempotency_key="manual-model-v7",
            owner_principal="operator@example.com",
            now=_NOW,
        )
    assert gcs._live == {}


def test_local_upload_nonwritable_authority_has_no_gcs_side_effect(tmp_path):
    source = tmp_path / "model.onnx"
    source.write_bytes(b"model-v7")
    control = replace(_control(), authority="v1", phase="v1_only")
    gcs = FakeGcsClient()
    manager = _manager(control=control, gcs=gcs)
    with pytest.raises(PipelineAssetManagementError, match="not writable"):
        manager.upload_asset(
            source,
            declaration=_declaration(),
            payload_key="primary",
            media_type="model/onnx",
            idempotency_key="manual-model-v7",
            owner_principal="operator@example.com",
            now=_NOW,
        )
    assert gcs._live == {}


def test_upload_bundle_requires_exact_declaration_payload_set(tmp_path):
    source = tmp_path / "model.onnx"
    source.write_bytes(b"model-v7")
    manager = _manager()
    with pytest.raises(ValueError, match="exactly match"):
        manager.upload_bundle(
            (
                LocalImportPayload(
                    source,
                    "attachments",
                    "primary",
                    "model.onnx",
                    "model/onnx",
                ),
            ),
            declaration=_declaration(),
            idempotency_key="manual-model-v7",
            owner_principal="operator@example.com",
            now=_NOW,
        )


def test_local_retry_refuses_different_bytes_for_same_idempotency_key(tmp_path):
    source = tmp_path / "model.onnx"
    source.write_bytes(b"model-v7")
    manager = _manager()
    values = dict(
        path=source,
        declaration=_declaration(),
        payload_key="primary",
        media_type="model/onnx",
        idempotency_key="manual-model-v7",
        owner_principal="operator@example.com",
        now=_NOW,
    )
    manager.upload_asset(**values)
    source.write_bytes(b"different")

    with pytest.raises(LocalImportConflict, match="different bytes"):
        manager.upload_asset(**values)


def test_successful_idempotent_replay_returns_rebuilt_result(monkeypatch):
    inspector = _Inspector()
    manager = _manager(inspector=inspector, policy=_policy())
    values = dict(
        source_uri="gs://approved-source/bundles/source-manifest.json#17",
        idempotency_key="daily-import",
        owner_principal="controller@example.com",
        now=_NOW,
    )
    reserved = manager.import_external(**values).operation
    ref = TypedId.from_bare("ab" * 32)
    ticket_digest = TypedId.from_bare("ef" * 32)
    ticket = ImportTicketRecord(
        schema_version="2.0",
        ticket_digest=ticket_digest,
        operation_id=reserved.operation_id,
        request_fingerprint=reserved.request_fingerprint,
        source=reserved.source,
        target_environment_id=reserved.target_environment_id,
        target_quarantine_prefix=reserved.target_quarantine_prefix,
        audience="https://executor.example.com",
        executor_principal="executor@example.com",
        fencing_token=reserved.fencing_token,
        issued_at=_NOW.isoformat(),
        expires_at="2026-07-28T00:05:00+00:00",
    )
    completion = ImportCompletionRecord(
        schema_version="2.0",
        operation_id=reserved.operation_id,
        ticket_digest=ticket_digest,
        environment_fingerprint=reserved.control_snapshot.environment_fingerprint,
        executor_principal=ticket.executor_principal,
        fencing_token=reserved.fencing_token,
        payload_set_digest=TypedId.from_bare("fa" * 32),
        completed_at=_NOW.isoformat(),
    )
    succeeded = replace(
        reserved,
        phase=ImportPhase.SUCCEEDED,
        revision=reserved.revision + 1,
        lease_expires_at=None,
        ticket=ticket,
        completion=completion,
        result_asset_version_id=ref,
        result_label_id=TypedId.from_bare("bc" * 32),
        result_source_provenance_attestation_ref=TypedId.from_bare("cd" * 32),
        identity_reservation_entry_id=TypedId.from_bare("de" * 32),
        identity_reservation_sequence=1,
        finished_at=_NOW.isoformat(),
        updated_at=_NOW.isoformat(),
    )
    manager.registry.put_import_operation(succeeded)
    result = RecoveredImportResult(
        succeeded, object(), object(), object(), object()
    )

    def _reconcile(*args, **kwargs):
        return ImportRecoveryOutcome(
            ImportRecoveryAction.REBUILT_SUCCEEDED, succeeded, result
        )

    monkeypatch.setattr(
        "aigear.management.pipeline_asset.reconcile_import_once", _reconcile
    )
    replay = manager.import_external(**values)
    assert replay.operation == succeeded
    assert replay.result is result

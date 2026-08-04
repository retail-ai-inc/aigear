from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from aigear.management.v2.attestation import HmacTestSigner, create_attestation
from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.content_policy import (
    ContentInspectionEvidence,
    InspectionPayloadEvidence,
    ScanVerdict,
    create_scanner_result,
)
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_evidence import (
    EvidenceNodeKind,
    PolicyEvidenceRequest,
    compute_policy_evidence_closure,
    compute_producer_identity,
)
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    AssetVersionRecord,
    InputBinding,
    LifecycleState,
    ProducerSpec,
    TrustState,
    compute_asset_version_id,
)
from aigear.management.v2.records.blob import (
    AvailabilityState,
    BlobLocationRevision,
    BlobRecord,
    LocationOperationKind,
    compute_genesis_location_chain_head,
    compute_location_chain_head,
)
from aigear.management.v2.records.import_operation import (
    ImportProvenanceIndexRecord,
)
from aigear.management.v2.records.lineage import (
    ComponentEdge,
    compute_component_edge_id,
)
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    compute_committed_output_key,
    compute_metrics_digest,
    compute_occurrence_id,
    compute_resolved_inputs_digest,
)

_FP = TypedId.from_bare("aa" * 32)
_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
_AT = _NOW.isoformat()
_SIGNER = HmacTestSigner(b"evidence", key_version="test/evidence/1")


def _producer(seed="11") -> ProducerSpec:
    return ProducerSpec(
        source_commit=f"commit-{seed}",
        image_digest=TypedId.from_bare(seed * 32),
        code_digest=TypedId.from_bare("22" * 32),
        config_digest=TypedId.from_bare("33" * 32),
    )


def _put_component_material(registry, data=b"model"):
    blob_id = TypedId.from_bare(hashlib.sha256(data).hexdigest())
    previous = compute_genesis_location_chain_head(_FP, blob_id)
    location_attestation = create_attestation(
        schema_version="2.0",
        attestation_kind="blob_location",
        environment_fingerprint=_FP,
        subject={
            "blob_id": blob_id.typed,
            "location_revision": 1,
            "previous_location_attestation_ref": None,
            "previous_chain_head": previous.typed,
            "bucket": "assets",
            "object_name": f"blobs/{blob_id.bare}",
            "generation": "7",
            "sha256": blob_id.bare,
            "crc32c": "crc",
            "size_bytes": len(data),
            "location_operation_kind": "pipeline_finalize",
            "location_operation_id": "finalize-1",
            "fencing_token": 1,
        },
        signer=_SIGNER,
    )
    head = compute_location_chain_head(previous, location_attestation.attestation_id)
    revision = BlobLocationRevision(
        schema_version="2.0",
        environment_fingerprint=_FP,
        blob_id=blob_id,
        location_revision=1,
        bucket="assets",
        object_name=f"blobs/{blob_id.bare}",
        generation="7",
        sha256=blob_id.bare,
        crc32c="crc",
        size_bytes=len(data),
        location_operation_id="finalize-1",
        location_operation_kind=LocationOperationKind.PIPELINE_FINALIZE,
        location_attestation_ref=location_attestation.attestation_id,
        location_chain_head=head,
        reason="test",
    )
    blob = BlobRecord(
        schema_version="2.0",
        environment_fingerprint=_FP,
        blob_id=blob_id,
        sha256=blob_id.bare,
        size_bytes=len(data),
        crc32c="crc",
        bucket=revision.bucket,
        object_name=revision.object_name,
        generation=revision.generation,
        current_location_revision=1,
        current_location_attestation_ref=location_attestation.attestation_id,
        location_chain_head=head,
        availability_state=AvailabilityState.READY,
    )
    registry.put_attestation(location_attestation)
    registry.put_blob_location_revision(revision)
    registry.put_blob(blob)
    return blob


def _put_asset(
    registry,
    *,
    producer=None,
    input_bindings=(),
    add_occurrence=True,
):
    producer = producer or _producer()
    blob = _put_component_material(registry)
    component = AssetComponent(
        role="model",
        blob_id=blob.blob_id,
        logical_name="model",
        media_type="model/onnx",
    )
    manifest = {
        "environment_id": "production",
        "environment_fingerprint": _FP.typed,
        "asset_type": "model",
        "name": "fraud",
        "components": [component.to_manifest_dict()],
        "input_bindings": [value.to_manifest_dict() for value in input_bindings],
        "producer_spec": producer.to_manifest_dict(),
        "schema_contract_digest": TypedId.from_bare("44" * 32).typed,
        "runtime_contract_digest": TypedId.from_bare("55" * 32).typed,
        "policy_version": "policy-7",
    }
    asset_id = compute_asset_version_id(manifest)
    manifest_attestation = create_attestation(
        schema_version="2.0",
        attestation_kind="asset_manifest_integrity",
        environment_fingerprint=_FP,
        subject={
            "asset_version_id": asset_id.typed,
            "manifest_digest": asset_id.typed,
            "manifest_schema_version": "2.0",
        },
        signer=_SIGNER,
    )
    asset = AssetVersionRecord(
        schema_version="2.0",
        environment_id="production",
        environment_fingerprint=_FP,
        asset_version_id=asset_id,
        asset_type="model",
        name="fraud",
        manifest_digest=asset_id,
        record_revision=1,
        components=(component,),
        input_bindings=tuple(input_bindings),
        producer_spec=producer,
        schema_contract_digest=TypedId.from_bare("44" * 32),
        runtime_contract_digest=TypedId.from_bare("55" * 32),
        lifecycle_state=LifecycleState.ACTIVE,
        trust_state=TrustState.VERIFIED,
        policy_version="policy-7",
        manifest_integrity_attestation_ref=manifest_attestation.attestation_id,
        created_at=_AT,
    )
    registry.put_attestation(manifest_attestation)
    registry.put_asset_version(asset)
    registry.put_component_edge(
        ComponentEdge(
            schema_version="2.0",
            environment_fingerprint=_FP,
            component_edge_id=compute_component_edge_id(
                asset_id, component.role, component.logical_name, component.blob_id
            ),
            asset_version_id=asset_id,
            blob_id=component.blob_id,
            role=component.role,
            logical_name=component.logical_name,
            created_at=_AT,
        )
    )
    if add_occurrence:
        _put_occurrence(registry, asset)
    return asset


def _put_occurrence(registry, asset):
    occurrence_id = compute_occurrence_id("run-1", "train", 1, "model")
    resolved = ()
    resolved_digest = compute_resolved_inputs_digest(resolved)
    finalization = create_attestation(
        schema_version="2.0",
        attestation_kind="occurrence_finalization",
        environment_fingerprint=_FP,
        subject={
            "producer_kind": "normal_pipeline",
            "occurrence_id": occurrence_id.typed,
            "asset_version_id": asset.asset_version_id.typed,
            "resolved_inputs_digest": resolved_digest.typed,
        },
        signer=_SIGNER,
    )
    occurrence = OccurrenceRecord(
        schema_version="2.0",
        environment_fingerprint=_FP,
        occurrence_id=occurrence_id,
        run_id="run-1",
        step_name="train",
        attempt_no=1,
        fencing_token=1,
        output_name="model",
        committed_output_key=compute_committed_output_key(
            "run-1", "train", "model"
        ),
        resolved_input_bindings=resolved,
        resolved_inputs_digest=resolved_digest,
        metrics={},
        metrics_digest=compute_metrics_digest({}),
        status=OccurrenceStatus.COMMITTED,
        operation_id="finalize-1",
        asset_version_id=asset.asset_version_id,
        asset_type=asset.asset_type,
        asset_name=asset.name,
        finalization_attestation_ref=finalization.attestation_id,
        committed_at=_AT,
    )
    registry.put_attestation(finalization)
    registry.put_occurrence(occurrence)
    return occurrence


def _request(asset, *, allowed_producer=None, allowed_scanners=(("scanner", "1"),), **overrides):
    producer = allowed_producer or compute_producer_identity(asset.producer_spec)
    values = dict(
        subject_asset_version_id=asset.asset_version_id,
        environment_fingerprint=_FP,
        read_time=_NOW,
        allowed_producer_identities=(producer,),
        allowed_scanners=allowed_scanners,
        max_depth=8,
        max_nodes=100,
        page_size=2,
    )
    values.update(overrides)
    return PolicyEvidenceRequest(**values)


def test_normal_pipeline_closure_is_complete_deterministic_and_approvable():
    registry = FakeRegistryV2()
    asset = _put_asset(registry)
    request = _request(asset)

    first = compute_policy_evidence_closure(registry, request)
    second = compute_policy_evidence_closure(registry, request)

    assert first == second
    assert first.approvable
    assert not first.rejection_reasons
    kinds = {value.kind for value in first.nodes}
    assert {
        EvidenceNodeKind.ASSET_VERSION,
        EvidenceNodeKind.PRODUCER,
        EvidenceNodeKind.COMPONENT_EDGE,
        EvidenceNodeKind.BLOB,
        EvidenceNodeKind.BLOB_LOCATION_REVISION,
        EvidenceNodeKind.BLOB_LOCATION_ATTESTATION,
        EvidenceNodeKind.OCCURRENCE,
        EvidenceNodeKind.OCCURRENCE_ATTESTATION,
    } <= kinds
    assert first.filter_digest == request.filter_digest


def test_unknown_producer_is_not_approvable():
    registry = FakeRegistryV2()
    asset = _put_asset(registry)
    closure = compute_policy_evidence_closure(
        registry,
        _request(
            asset,
            allowed_producer=TypedId.from_bare("ff" * 32),
        ),
    )
    assert not closure.approvable
    assert "unknown_producer" in closure.rejection_reasons


def test_missing_component_edge_and_node_bound_fail_closed():
    registry = FakeRegistryV2()
    asset = _put_asset(registry)
    registry._component_edges.clear()
    missing = compute_policy_evidence_closure(registry, _request(asset))
    assert "missing_or_invalid_component_edge" in missing.rejection_reasons

    bounded = compute_policy_evidence_closure(
        registry,
        _request(asset, max_nodes=2),
    )
    assert "max_nodes_exceeded" in bounded.rejection_reasons


def test_asset_input_cycle_is_not_approvable_even_from_corrupt_backend_data():
    registry = FakeRegistryV2()
    asset = _put_asset(registry)
    corrupt = object.__new__(AssetVersionRecord)
    for field_name in asset.__dataclass_fields__:
        object.__setattr__(corrupt, field_name, getattr(asset, field_name))
    object.__setattr__(
        corrupt,
        "input_bindings",
        (InputBinding("self", asset.asset_version_id),),
    )
    registry._asset_versions[asset.asset_version_id] = corrupt

    closure = compute_policy_evidence_closure(registry, _request(asset))
    assert "asset_input_cycle" in closure.rejection_reasons


def _inspection_evidence(blob_id):
    scanner = create_scanner_result(
        scanner_id="scanner",
        scanner_version="1",
        payload_sha256=blob_id.bare,
        verdict=ScanVerdict.CLEAN,
        finding_codes=(),
        completed_at=_AT,
    )
    payload = InspectionPayloadEvidence(
        payload_kind="components",
        payload_key="primary",
        quarantine_object_name="p/v/registry/v2/_quarantine/import-1/components/primary/model.onnx",
        quarantine_generation="3",
        sha256=blob_id.bare,
        size_bytes=5,
        format_name="onnx",
        scanner_id=scanner.scanner_id,
        scanner_version=scanner.scanner_version,
        scanner_verdict=scanner.verdict,
        scanner_finding_codes=scanner.finding_codes,
        scanner_completed_at=scanner.completed_at,
        scanner_report_digest=scanner.report_digest,
    )
    values = {
        "operation_id": "import-1",
        "ticket_digest": TypedId.from_bare("66" * 32),
        "environment_fingerprint": _FP,
        "fencing_token": 1,
        "payload_set_digest": TypedId.from_bare("77" * 32),
        "policy_digest": TypedId.from_bare("88" * 32),
        "producer_evidence_digest": TypedId.from_bare("99" * 32),
        "governance_digest": TypedId.from_bare("ab" * 32),
        "payloads": (payload,),
        "inspected_at": _AT,
    }
    core = {
        "domain": "aigear.content-inspection.v2",
        "operation_id": values["operation_id"],
        "ticket_digest": values["ticket_digest"].typed,
        "environment_fingerprint": _FP.typed,
        "fencing_token": 1,
        "payload_set_digest": values["payload_set_digest"].typed,
        "policy_digest": values["policy_digest"].typed,
        "producer_evidence_digest": values["producer_evidence_digest"].typed,
        "governance_digest": values["governance_digest"].typed,
        "payloads": [payload.canonical_dict()],
        "inspected_at": _AT,
    }
    evidence_digest = TypedId.from_bare(
        hashlib.sha256(canonicalize_json(core)).hexdigest()
    )
    return ContentInspectionEvidence(**values, evidence_digest=evidence_digest)


def _put_import_provenance(registry, asset, *, include_scanner=True):
    inspection = _inspection_evidence(asset.components[0].blob_id)
    subject = {
        "provenance_kind": "external_import",
        "asset_version_id": asset.asset_version_id.typed,
        "operation_id": "import-1",
        "fencing_token": 1,
        "ticket_digest": inspection.ticket_digest.typed,
        "inspection_evidence_digest": inspection.evidence_digest.typed,
        "payloads": [
            {
                "payload_kind": "components",
                "payload_key": "primary",
                "semantic_kind": "model",
                "logical_name": "model",
                "media_type": "model/onnx",
                "blob_id": asset.components[0].blob_id.typed,
                "size_bytes": 5,
                "crc32c": "crc",
                "source_bucket": "source",
                "source_object_name": "bundle/model.onnx",
                "source_generation": "2",
                "quarantine_bucket": "assets",
                "quarantine_object_name": (
                    "p/v/registry/v2/_quarantine/import-1/"
                    "components/primary/model.onnx"
                ),
                "quarantine_generation": "3",
            }
        ],
        "producer_evidence_digest": inspection.producer_evidence_digest.typed,
    }
    if include_scanner:
        subject["inspection_evidence"] = inspection.canonical_dict()
    provenance = create_attestation(
        schema_version="2.0",
        attestation_kind="source_provenance",
        environment_fingerprint=_FP,
        subject=subject,
        signer=_SIGNER,
    )
    registry.put_attestation(provenance)
    registry.put_import_provenance(
        ImportProvenanceIndexRecord(
            schema_version="2.0",
            environment_fingerprint=_FP,
            asset_version_id=asset.asset_version_id,
            source_provenance_attestation_ref=provenance.attestation_id,
            operation_id="import-1",
            ticket_digest=inspection.ticket_digest,
            identity_reservation_entry_id=TypedId.from_bare("bc" * 32),
        )
    )


def test_external_import_requires_complete_allowlisted_scanner_evidence():
    registry = FakeRegistryV2()
    asset = _put_asset(registry, add_occurrence=False)
    _put_import_provenance(registry, asset)

    approved = compute_policy_evidence_closure(registry, _request(asset))
    assert approved.approvable

    wrong_scanner = compute_policy_evidence_closure(
        registry,
        _request(asset, allowed_scanners=(("other", "1"),)),
    )
    assert "untrusted_or_invalid_scanner_evidence" in wrong_scanner.rejection_reasons


def test_external_import_without_embedded_scanner_evidence_is_not_approvable():
    registry = FakeRegistryV2()
    asset = _put_asset(registry, add_occurrence=False)
    _put_import_provenance(registry, asset, include_scanner=False)
    closure = compute_policy_evidence_closure(registry, _request(asset))
    assert "missing_scanner_evidence" in closure.rejection_reasons


class _DriftingRegistry(FakeRegistryV2):
    def at_read_time(self, read_time):
        view = super().at_read_time(read_time)
        view.read_time = read_time + timedelta(seconds=1)
        return view


def test_read_time_drift_is_not_approvable():
    registry = _DriftingRegistry()
    asset = _put_asset(registry)
    closure = compute_policy_evidence_closure(registry, _request(asset))
    assert not closure.approvable
    assert closure.rejection_reasons == ("read_time_drift",)

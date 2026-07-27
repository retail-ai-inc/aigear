from __future__ import annotations

import pytest

from aigear.management.v2.fake_registry import (
    FakeRegistryConflictError,
    FakeRegistryV2,
    IdempotencyConflict,
    IdentityConflict,
    IntegrityConflict,
    LabelRebindConflict,
    OperationConflict,
    OutputAlreadyCommitted,
)
from aigear.management.v2.identifiers import TypedId
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
)
from aigear.management.v2.records.blob_claim import BlobClaim, ClaimState, InvalidClaimTransitionError
from aigear.management.v2.records.label import (
    LabelRecord,
    ReadableManifestProjection,
    compute_label_id,
)
from aigear.management.v2.records.lineage import (
    AttachmentEdge,
    ComponentEdge,
    LineageEdge,
    OwnerKind,
    compute_attachment_edge_id,
    compute_component_edge_id,
    compute_lineage_edge_id,
)
from aigear.management.v2.records.occurrence import (
    InvalidOccurrenceStatusTransitionError,
    OccurrenceRecord,
    OccurrenceStatus,
    compute_committed_output_key,
    compute_metrics_digest,
    compute_occurrence_id,
    compute_resolved_inputs_digest,
)
from aigear.management.v2.records.operation import (
    InvalidOperationPhaseTransitionError,
    OperationPhase,
    OperationRecord,
)
from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    InvalidAttemptStatusTransitionError,
    InvalidRunStatusTransitionError,
    InvalidStepStatusTransitionError,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
)

_FINGERPRINT_HEX = "aa" * 32
_BLOB_HEX = "bb" * 32
_ATTESTATION_HEX = "cc" * 32
_SCHEMA_DIGEST_HEX = "dd" * 32
_RUNTIME_DIGEST_HEX = "ee" * 32
_IMAGE_DIGEST_HEX = "11" * 32
_CODE_DIGEST_HEX = "22" * 32
_CONFIG_DIGEST_HEX = "33" * 32


def _fingerprint() -> TypedId:
    return TypedId.from_bare(_FINGERPRINT_HEX)


def test_run_atomic_rolls_back_every_registry_write_on_failure():
    registry = FakeRegistryV2()

    def work(tx):
        tx.create_run(RunRecord(run_id="run-atomic", status=RunStatus.PENDING))
        raise RuntimeError("inject transaction failure")

    with pytest.raises(RuntimeError, match="inject"):
        registry.run_atomic(work)
    assert registry.get_run("run-atomic") is None


def _blob_record(**overrides) -> BlobRecord:
    blob_id = TypedId.from_bare(_BLOB_HEX)
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        blob_id=blob_id,
        sha256=_BLOB_HEX,
        size_bytes=100,
        crc32c="AAAAAA==",
        bucket="bucket",
        object_name="proj/pipeline/registry/v2/_objects/sha256/bb/" + _BLOB_HEX,
        generation="1",
        current_location_revision=1,
        current_location_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
        location_chain_head=TypedId.from_bare("44" * 32),
        availability_state=AvailabilityState.READY,
    )
    defaults.update(overrides)
    return BlobRecord(**defaults)


def _asset_version_record(**overrides) -> AssetVersionRecord:
    components = (
        AssetComponent(
            role="model",
            blob_id=TypedId.from_bare(_BLOB_HEX),
            logical_name="model.onnx",
            media_type="application/onnx",
        ),
    )
    producer_spec = ProducerSpec(
        source_commit="abc123",
        image_digest=TypedId.from_bare(_IMAGE_DIGEST_HEX),
        code_digest=TypedId.from_bare(_CODE_DIGEST_HEX),
        config_digest=TypedId.from_bare(_CONFIG_DIGEST_HEX),
    )
    manifest = {
        "environment_id": "production",
        "environment_fingerprint": _fingerprint().typed,
        "asset_type": "model",
        "name": "logistic_regression",
        "components": [c.to_manifest_dict() for c in components],
        "input_bindings": [],
        "producer_spec": producer_spec.to_manifest_dict(),
        "schema_contract_digest": TypedId.from_bare(_SCHEMA_DIGEST_HEX).typed,
        "runtime_contract_digest": TypedId.from_bare(_RUNTIME_DIGEST_HEX).typed,
        "policy_version": "policy-v1",
    }
    asset_version_id = compute_asset_version_id(manifest)
    defaults = dict(
        schema_version="2.0",
        environment_id="production",
        environment_fingerprint=_fingerprint(),
        asset_version_id=asset_version_id,
        asset_type="model",
        name="logistic_regression",
        manifest_digest=asset_version_id,
        record_revision=1,
        components=components,
        input_bindings=(),
        producer_spec=producer_spec,
        schema_contract_digest=TypedId.from_bare(_SCHEMA_DIGEST_HEX),
        runtime_contract_digest=TypedId.from_bare(_RUNTIME_DIGEST_HEX),
        lifecycle_state=LifecycleState.ACTIVE,
        trust_state=TrustState.VERIFIED,
        policy_version="policy-v1",
        manifest_integrity_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
    )
    defaults.update(overrides)
    return AssetVersionRecord(**defaults)


def _label_record(asset_version_id: TypedId, **overrides) -> LabelRecord:
    label_id = compute_label_id("model", "logistic_regression", "v1")
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        label_id=label_id,
        asset_type="model",
        asset_name="logistic_regression",
        display_version="v1",
        asset_version_id=asset_version_id,
        projection_source_revision=1,
        readable_manifest=ReadableManifestProjection.initial(uri="gs://bucket/manifest.json"),
        created_by="finalizer@aigear",
    )
    defaults.update(overrides)
    return LabelRecord(**defaults)


def _occurrence_record(
    *, run_id="run-1", step_name="training", attempt_no=1, output_name="model", **overrides
) -> OccurrenceRecord:
    occurrence_id = compute_occurrence_id(run_id, step_name, attempt_no, output_name)
    committed_output_key = compute_committed_output_key(run_id, step_name, output_name)
    metrics = {}
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        occurrence_id=occurrence_id,
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt_no,
        fencing_token=1,
        output_name=output_name,
        committed_output_key=committed_output_key,
        resolved_input_bindings=(),
        resolved_inputs_digest=compute_resolved_inputs_digest(()),
        metrics=metrics,
        metrics_digest=compute_metrics_digest(metrics),
        status=OccurrenceStatus.PROVISIONAL,
        operation_id="op-1",
    )
    defaults.update(overrides)
    return OccurrenceRecord(**defaults)


def _committed_occurrence_record(**overrides) -> OccurrenceRecord:
    asset_version_id = TypedId.from_bare("55" * 32)
    base = dict(
        status=OccurrenceStatus.COMMITTED,
        asset_version_id=asset_version_id,
        asset_type="model",
        asset_name="logistic_regression",
        label_id=TypedId.from_bare("66" * 32),
        display_version="v1",
        finalization_attestation_ref=TypedId.from_bare("77" * 32),
    )
    base.update(overrides)
    return _occurrence_record(**base)


# ── Blob ─────────────────────────────────────────────────────────────────────────


def test_put_blob_stores_and_returns_record():
    registry = FakeRegistryV2()
    record = _blob_record()
    assert registry.put_blob(record) is record
    assert registry.get_blob(record.blob_id) is record


def test_put_blob_allows_mutable_availability_state_update():
    registry = FakeRegistryV2()
    record = _blob_record()
    registry.put_blob(record)
    updated = _blob_record(availability_state=AvailabilityState.MISSING)
    registry.put_blob(updated)
    assert registry.get_blob(record.blob_id).availability_state == AvailabilityState.MISSING


def test_put_blob_rejects_physical_identity_mismatch():
    registry = FakeRegistryV2()
    registry.put_blob(_blob_record())
    with pytest.raises(IntegrityConflict):
        registry.put_blob(_blob_record(size_bytes=999))


def test_get_blob_returns_none_for_unknown_id():
    registry = FakeRegistryV2()
    assert registry.get_blob(TypedId.from_bare("00" * 32)) is None


def _blob_location_revision(**overrides) -> BlobLocationRevision:
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        blob_id=TypedId.from_bare(_BLOB_HEX),
        location_revision=1,
        bucket="bucket",
        object_name="proj/pipeline/registry/v2/_objects/sha256/bb/" + _BLOB_HEX,
        generation="1",
        sha256=_BLOB_HEX,
        crc32c="AAAAAA==",
        size_bytes=100,
        location_operation_id="op-1",
        location_operation_kind=LocationOperationKind.PIPELINE_FINALIZE,
        location_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
        location_chain_head=TypedId.from_bare("44" * 32),
        reason="pipeline finalize: first canonical location",
    )
    defaults.update(overrides)
    return BlobLocationRevision(**defaults)


def test_put_blob_location_revision_stores_and_returns_record():
    registry = FakeRegistryV2()
    record = _blob_location_revision()
    assert registry.put_blob_location_revision(record) is record
    assert registry.get_blob_location_revision(record.blob_id, 1) is record


def test_put_blob_location_revision_is_idempotent_for_identical_content():
    registry = FakeRegistryV2()
    record = _blob_location_revision()
    registry.put_blob_location_revision(record)
    registry.put_blob_location_revision(record)
    assert registry.get_blob_location_revision(record.blob_id, 1) == record


def test_put_blob_location_revision_rejects_conflicting_content_at_same_revision():
    registry = FakeRegistryV2()
    registry.put_blob_location_revision(_blob_location_revision())
    with pytest.raises(IdentityConflict):
        registry.put_blob_location_revision(_blob_location_revision(generation="2"))


def test_get_blob_location_revision_returns_none_for_unknown_revision():
    registry = FakeRegistryV2()
    assert registry.get_blob_location_revision(TypedId.from_bare(_BLOB_HEX), 1) is None


# ── AssetVersion ─────────────────────────────────────────────────────────────────


def test_put_asset_version_stores_and_returns_record():
    registry = FakeRegistryV2()
    record = _asset_version_record()
    registry.put_asset_version(record)
    assert registry.get_asset_version(record.asset_version_id) is record


def test_put_asset_version_allows_mutable_lifecycle_state_update():
    registry = FakeRegistryV2()
    record = _asset_version_record()
    registry.put_asset_version(record)
    archived = _asset_version_record(lifecycle_state=LifecycleState.ARCHIVED)
    registry.put_asset_version(archived)
    assert registry.get_asset_version(record.asset_version_id).lifecycle_state == LifecycleState.ARCHIVED


def test_put_asset_version_rejects_manifest_mismatch_for_same_id():
    registry = FakeRegistryV2()
    record = _asset_version_record()
    registry.put_asset_version(record)
    # Force a different asset_version_id/manifest_digest onto a record whose
    # canonical_manifest() content differs, then override back to the same ID
    # to simulate a corrupted/forged registration under an existing key.
    other = _asset_version_record()
    object.__setattr__(other, "name", "different_name")
    object.__setattr__(other, "asset_version_id", record.asset_version_id)
    object.__setattr__(other, "manifest_digest", record.asset_version_id)
    with pytest.raises(IdentityConflict):
        registry.put_asset_version(other)


# ── Label ────────────────────────────────────────────────────────────────────────


def test_put_label_stores_and_returns_record():
    registry = FakeRegistryV2()
    asset_version_id = TypedId.from_bare("55" * 32)
    label = _label_record(asset_version_id)
    registry.put_label(label)
    assert registry.get_label(label.label_id) is label


def test_put_label_allows_projection_state_update_for_same_binding():
    registry = FakeRegistryV2()
    asset_version_id = TypedId.from_bare("55" * 32)
    label = _label_record(asset_version_id)
    registry.put_label(label)
    updated = _label_record(
        asset_version_id,
        readable_manifest=ReadableManifestProjection(
            uri="gs://bucket/manifest.json",
            status="ready",
            projection_mutation_fence=1,
            projection_schema_version="1.0",
        ),
    )
    registry.put_label(updated)
    assert registry.get_label(label.label_id).readable_manifest.status == "ready"


def test_put_label_rejects_rebind_to_different_asset_version():
    registry = FakeRegistryV2()
    registry.put_label(_label_record(TypedId.from_bare("55" * 32)))
    with pytest.raises(LabelRebindConflict):
        registry.put_label(_label_record(TypedId.from_bare("99" * 32)))


def test_get_label_returns_none_for_unknown_id():
    registry = FakeRegistryV2()
    assert registry.get_label(TypedId.from_bare("00" * 32)) is None


# ── Occurrence ───────────────────────────────────────────────────────────────────


def test_put_occurrence_stores_provisional_record():
    registry = FakeRegistryV2()
    record = _occurrence_record()
    registry.put_occurrence(record)
    assert registry.get_occurrence(record.occurrence_id) is record


def test_put_occurrence_allows_provisional_to_committed_transition():
    registry = FakeRegistryV2()
    provisional = _occurrence_record()
    registry.put_occurrence(provisional)
    committed = _committed_occurrence_record()
    registry.put_occurrence(committed)
    assert registry.get_occurrence(committed.occurrence_id).status == OccurrenceStatus.COMMITTED


def test_put_occurrence_rejects_illegal_status_transition():
    registry = FakeRegistryV2()
    registry.put_occurrence(_occurrence_record(status=OccurrenceStatus.ABORTED))
    with pytest.raises(InvalidOccurrenceStatusTransitionError):
        # ABORTED -> COMMITTED is not a legal Occurrence transition (spec 12.1).
        registry.put_occurrence(_committed_occurrence_record())


def test_put_occurrence_rejects_overwriting_committed_with_different_content():
    registry = FakeRegistryV2()
    committed = _committed_occurrence_record()
    registry.put_occurrence(committed)
    different = _committed_occurrence_record(fencing_token=999)
    with pytest.raises(IdempotencyConflict):
        registry.put_occurrence(different)


def test_put_occurrence_allows_idempotent_retry_with_identical_content():
    registry = FakeRegistryV2()
    committed = _committed_occurrence_record()
    registry.put_occurrence(committed)
    registry.put_occurrence(_committed_occurrence_record())  # identical content


def test_put_occurrence_enforces_committed_output_uniqueness_across_attempts():
    registry = FakeRegistryV2()
    winner = _committed_occurrence_record(attempt_no=1)
    registry.put_occurrence(_occurrence_record(attempt_no=1))
    registry.put_occurrence(winner)

    loser_provisional = _occurrence_record(attempt_no=2)
    registry.put_occurrence(loser_provisional)
    loser_committed = _committed_occurrence_record(attempt_no=2)
    with pytest.raises(OutputAlreadyCommitted):
        registry.put_occurrence(loser_committed)


def test_get_committed_occurrence_by_output_key():
    registry = FakeRegistryV2()
    committed = _committed_occurrence_record()
    registry.put_occurrence(committed)
    found = registry.get_committed_occurrence_by_output_key(committed.committed_output_key)
    assert found is committed


def test_get_committed_occurrence_by_output_key_returns_none_when_absent():
    registry = FakeRegistryV2()
    key = compute_committed_output_key("run-x", "step-x", "output-x")
    assert registry.get_committed_occurrence_by_output_key(key) is None


# ── Run ──────────────────────────────────────────────────────────────────────────


def test_create_run_and_get_run():
    registry = FakeRegistryV2()
    record = RunRecord(run_id="run-1", status=RunStatus.PENDING)
    registry.create_run(record)
    assert registry.get_run("run-1") is record


def test_create_run_rejects_duplicate_run_id():
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))
    with pytest.raises(FakeRegistryConflictError):
        registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))


def test_update_run_status_follows_state_machine():
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))
    updated = registry.update_run_status("run-1", RunStatus.RUNNING)
    assert updated.status == RunStatus.RUNNING
    assert registry.get_run("run-1").status == RunStatus.RUNNING


def test_update_run_status_rejects_illegal_transition():
    registry = FakeRegistryV2()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.PENDING))
    with pytest.raises(InvalidRunStatusTransitionError):
        registry.update_run_status("run-1", RunStatus.SUCCEEDED)


def test_update_run_status_rejects_unknown_run_id():
    registry = FakeRegistryV2()
    with pytest.raises(KeyError):
        registry.update_run_status("missing-run", RunStatus.RUNNING)


def test_update_run_status_preserves_other_fields():
    registry = FakeRegistryV2()
    run_spec_digest = TypedId.from_bare("aa" * 32)
    registry.create_run(
        RunRecord(
            run_id="run-1",
            status=RunStatus.PENDING,
            run_spec_digest=run_spec_digest,
            remaining_required_steps=2,
            parent_run_id="run-0",
        )
    )
    updated = registry.update_run_status("run-1", RunStatus.RUNNING)
    assert updated.run_spec_digest == run_spec_digest
    assert updated.remaining_required_steps == 2
    assert updated.parent_run_id == "run-0"


def test_update_run_status_accepts_extra_field_updates():
    registry = FakeRegistryV2()
    registry.create_run(
        RunRecord(run_id="run-1", status=RunStatus.RUNNING, remaining_required_steps=2)
    )
    updated = registry.update_run_status("run-1", RunStatus.RUNNING, remaining_required_steps=1)
    assert updated.remaining_required_steps == 1


# ── Step ─────────────────────────────────────────────────────────────────────


def test_create_step_and_get_step():
    registry = FakeRegistryV2()
    record = StepRecord(run_id="run-1", step_name="train", status=StepStatus.BLOCKED)
    registry.create_step(record)
    assert registry.get_step("run-1", "train") is record


def test_create_step_rejects_duplicate_key():
    registry = FakeRegistryV2()
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.BLOCKED))
    with pytest.raises(FakeRegistryConflictError):
        registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.BLOCKED))


def test_update_step_status_follows_state_machine():
    registry = FakeRegistryV2()
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.BLOCKED))
    updated = registry.update_step_status("run-1", "train", StepStatus.READY)
    assert updated.status == StepStatus.READY


def test_update_step_status_rejects_illegal_transition():
    registry = FakeRegistryV2()
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.BLOCKED))
    with pytest.raises(InvalidStepStatusTransitionError):
        registry.update_step_status("run-1", "train", StepStatus.LEASED)


def test_update_step_status_accepts_extra_field_updates():
    registry = FakeRegistryV2()
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.READY))
    updated = registry.update_step_status("run-1", "train", StepStatus.LEASED, current_attempt_no=1)
    assert updated.current_attempt_no == 1


def test_update_step_status_rejects_unknown_key():
    registry = FakeRegistryV2()
    with pytest.raises(KeyError):
        registry.update_step_status("run-1", "missing", StepStatus.READY)


# ── Attempt ──────────────────────────────────────────────────────────────────


def test_create_attempt_and_get_attempt():
    registry = FakeRegistryV2()
    record = AttemptRecord(
        run_id="run-1", step_name="train", attempt_no=1, status=AttemptStatus.LEASED, fencing_token=1
    )
    registry.create_attempt(record)
    assert registry.get_attempt("run-1", "train", 1) is record


def test_create_attempt_rejects_duplicate_key():
    registry = FakeRegistryV2()
    registry.create_attempt(
        AttemptRecord(
            run_id="run-1", step_name="train", attempt_no=1, status=AttemptStatus.LEASED, fencing_token=1
        )
    )
    with pytest.raises(FakeRegistryConflictError):
        registry.create_attempt(
            AttemptRecord(
                run_id="run-1", step_name="train", attempt_no=1, status=AttemptStatus.LEASED, fencing_token=1
            )
        )


def test_update_attempt_status_follows_state_machine():
    registry = FakeRegistryV2()
    registry.create_attempt(
        AttemptRecord(
            run_id="run-1", step_name="train", attempt_no=1, status=AttemptStatus.LEASED, fencing_token=1
        )
    )
    updated = registry.update_attempt_status("run-1", "train", 1, AttemptStatus.RUNNING)
    assert updated.status == AttemptStatus.RUNNING


def test_update_attempt_status_rejects_illegal_transition():
    registry = FakeRegistryV2()
    registry.create_attempt(
        AttemptRecord(
            run_id="run-1", step_name="train", attempt_no=1, status=AttemptStatus.LEASED, fencing_token=1
        )
    )
    with pytest.raises(InvalidAttemptStatusTransitionError):
        registry.update_attempt_status("run-1", "train", 1, AttemptStatus.SUCCEEDED)


# ── Operation ────────────────────────────────────────────────────────────────


def _operation(**overrides) -> OperationRecord:
    defaults = dict(
        idempotency_key_hash="aa" * 32,
        request_fingerprint="fp-1",
        operation_type="run_trigger",
        owner_principal="controller@aigear",
        write_epoch=1,
        fencing_token=0,
        phase=OperationPhase.RESERVED,
        revision=1,
    )
    defaults.update(overrides)
    return OperationRecord(**defaults)


def test_put_operation_and_get_operation():
    registry = FakeRegistryV2()
    record = _operation()
    registry.put_operation(record)
    assert registry.get_operation("aa" * 32) is record


def test_put_operation_replay_with_same_fingerprint_succeeds():
    registry = FakeRegistryV2()
    registry.put_operation(_operation())
    updated = registry.put_operation(_operation(phase=OperationPhase.STAGING))
    assert updated.phase == OperationPhase.STAGING


def test_put_operation_rejects_different_fingerprint():
    registry = FakeRegistryV2()
    registry.put_operation(_operation())
    with pytest.raises(OperationConflict):
        registry.put_operation(_operation(request_fingerprint="fp-2"))


def test_put_operation_rejects_illegal_phase_transition():
    registry = FakeRegistryV2()
    registry.put_operation(_operation())
    with pytest.raises(InvalidOperationPhaseTransitionError):
        registry.put_operation(_operation(phase=OperationPhase.SUCCEEDED))


def test_fake_registry_satisfies_operation_store_protocol_for_run_trigger():
    from aigear.management.v2.run_trigger import begin_run_trigger, compute_run_idempotency_key

    registry = FakeRegistryV2()
    key = compute_run_idempotency_key(
        "pipeline-x", "2026-07-24T00:00:00Z", "cloud_scheduler", TypedId.from_bare("aa" * 32)
    )
    operation = begin_run_trigger(
        registry,
        idempotency_key=key,
        request_fingerprint="fp-1",
        owner_principal="controller@aigear",
        create_run=lambda: "run-1",
    )
    assert operation.run_id == "run-1"
    assert registry.get_operation(key.bare) is operation


# ── BlobClaim ────────────────────────────────────────────────────────────────


def _blob_claim(**overrides) -> BlobClaim:
    defaults = dict(
        blob_id=TypedId.from_bare(_BLOB_HEX),
        claim_epoch=0,
        fencing_token=1,
        operation_id="op-1",
        expected_object_name="registry/v2/_objects/sha256/bb/bbbb...",
        request_digest=TypedId.from_bare("dd" * 32),
        state=ClaimState.ADOPTING,
    )
    defaults.update(overrides)
    return BlobClaim(**defaults)


def test_put_blob_claim_and_get_blob_claim():
    registry = FakeRegistryV2()
    record = _blob_claim()
    registry.put_blob_claim(record)
    assert registry.get_blob_claim(TypedId.from_bare(_BLOB_HEX)) is record


def test_put_blob_claim_follows_state_machine():
    registry = FakeRegistryV2()
    registry.put_blob_claim(_blob_claim(state=ClaimState.ADOPTING))
    updated = registry.put_blob_claim(_blob_claim(state=ClaimState.CONSUMED))
    assert updated.state == ClaimState.CONSUMED


def test_put_blob_claim_rejects_illegal_transition():
    registry = FakeRegistryV2()
    registry.put_blob_claim(_blob_claim(state=ClaimState.ADOPTING))
    registry.put_blob_claim(_blob_claim(state=ClaimState.CONSUMED))
    with pytest.raises(InvalidClaimTransitionError):
        registry.put_blob_claim(_blob_claim(state=ClaimState.DELETE_INTENT))


# ── Lineage / Component / Attachment edges ────────────────────────────────────


def test_put_lineage_edge_and_get_lineage_edge():
    registry = FakeRegistryV2()
    output_occ = TypedId.from_bare("11" * 32)
    input_occ = TypedId.from_bare("22" * 32)
    edge_id = compute_lineage_edge_id(output_occ, "training_features", input_occ)
    record = LineageEdge(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        edge_id=edge_id,
        output_occurrence_id=output_occ,
        input_occurrence_id=input_occ,
        binding_name="training_features",
        run_id="run-1",
    )
    registry.put_lineage_edge(record)
    assert registry.get_lineage_edge(edge_id) == record


def test_put_lineage_edge_is_idempotent_across_created_at():
    registry = FakeRegistryV2()
    output_occ = TypedId.from_bare("11" * 32)
    input_occ = TypedId.from_bare("22" * 32)
    edge_id = compute_lineage_edge_id(output_occ, "training_features", input_occ)

    def _edge(created_at):
        return LineageEdge(
            schema_version="2.0",
            environment_fingerprint=_fingerprint(),
            edge_id=edge_id,
            output_occurrence_id=output_occ,
            input_occurrence_id=input_occ,
            binding_name="training_features",
            run_id="run-1",
            created_at=created_at,
        )

    first = registry.put_lineage_edge(_edge("2026-07-24T00:00:00Z"))
    second = registry.put_lineage_edge(_edge("2026-07-24T00:00:01Z"))
    assert first is second
    assert registry.get_lineage_edge(edge_id).created_at == "2026-07-24T00:00:00Z"


def test_put_component_edge_and_get_component_edge():
    registry = FakeRegistryV2()
    asset_version_id = TypedId.from_bare("33" * 32)
    blob_id = TypedId.from_bare("44" * 32)
    edge_id = compute_component_edge_id(asset_version_id, "model", "model.onnx", blob_id)
    record = ComponentEdge(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        component_edge_id=edge_id,
        asset_version_id=asset_version_id,
        blob_id=blob_id,
        role="model",
        logical_name="model.onnx",
    )
    registry.put_component_edge(record)
    assert registry.get_component_edge(edge_id) == record


def test_put_attachment_edge_and_get_attachment_edge():
    registry = FakeRegistryV2()
    owner_id = TypedId.from_bare("55" * 32).typed
    blob_id = TypedId.from_bare("66" * 32)
    edge_id = compute_attachment_edge_id(OwnerKind.OCCURRENCE, owner_id, "metrics", "evaluation.json", blob_id)
    record = AttachmentEdge(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        attachment_edge_id=edge_id,
        owner_kind=OwnerKind.OCCURRENCE,
        owner_id=owner_id,
        attachment_kind="metrics",
        logical_name="evaluation.json",
        blob_id=blob_id,
        media_type="application/json",
    )
    registry.put_attachment_edge(record)
    assert registry.get_attachment_edge(edge_id) == record

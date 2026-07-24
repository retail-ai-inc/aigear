from __future__ import annotations

import pytest

from aigear.management.pipeline_asset import PipelineAssetManagement
from aigear.management.v2.environment import EnvironmentIdentity, compute_environment_fingerprint
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    AssetVersionRecord,
    LifecycleState,
    ProducerSpec,
    TrustState,
    compute_asset_version_id,
)
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    compute_committed_output_key,
    compute_metrics_digest,
    compute_occurrence_id,
    compute_resolved_inputs_digest,
)

_FINGERPRINT_HEX = "aa" * 32
_BLOB_HEX = "bb" * 32
_ATTESTATION_HEX = "cc" * 32


def _environment_identity(**overrides) -> EnvironmentIdentity:
    defaults = dict(
        environment_id="production",
        gcp_project_number="123456789012",
        project_name="aigear_sklearn_pipeline",
        pipeline_version="logistic_regression",
        asset_bucket_name="aigear-prod-assets",
        asset_bucket_location="asia-northeast1",
        kms_trust_domain="projects/my-project/locations/asia-northeast1/keyRings/aigear",
    )
    defaults.update(overrides)
    return EnvironmentIdentity(**defaults)


def _asset_version_record() -> AssetVersionRecord:
    fingerprint = TypedId.from_bare(_FINGERPRINT_HEX)
    component = AssetComponent(
        role="model",
        blob_id=TypedId.from_bare(_BLOB_HEX),
        logical_name="model.onnx",
        media_type="application/onnx",
    )
    producer_spec = ProducerSpec(
        source_commit="abc123",
        image_digest=TypedId.from_bare("11" * 32),
        code_digest=TypedId.from_bare("22" * 32),
        config_digest=TypedId.from_bare("33" * 32),
    )
    manifest = {
        "environment_id": "production",
        "environment_fingerprint": fingerprint.typed,
        "asset_type": "model",
        "name": "logistic_regression",
        "components": [component.to_manifest_dict()],
        "input_bindings": [],
        "producer_spec": producer_spec.to_manifest_dict(),
        "schema_contract_digest": TypedId.from_bare("dd" * 32).typed,
        "runtime_contract_digest": TypedId.from_bare("ee" * 32).typed,
        "policy_version": "policy-v1",
    }
    asset_version_id = compute_asset_version_id(manifest)
    return AssetVersionRecord(
        schema_version="2.0",
        environment_id="production",
        environment_fingerprint=fingerprint,
        asset_version_id=asset_version_id,
        asset_type="model",
        name="logistic_regression",
        manifest_digest=asset_version_id,
        record_revision=1,
        components=(component,),
        input_bindings=(),
        producer_spec=producer_spec,
        schema_contract_digest=TypedId.from_bare("dd" * 32),
        runtime_contract_digest=TypedId.from_bare("ee" * 32),
        lifecycle_state=LifecycleState.ACTIVE,
        trust_state=TrustState.VERIFIED,
        policy_version="policy-v1",
        manifest_integrity_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
    )


def _committed_occurrence_record() -> OccurrenceRecord:
    run_id, step_name, attempt_no, output_name = "run-1", "training", 1, "model"
    metrics = {}
    return OccurrenceRecord(
        schema_version="2.0",
        environment_fingerprint=TypedId.from_bare(_FINGERPRINT_HEX),
        occurrence_id=compute_occurrence_id(run_id, step_name, attempt_no, output_name),
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt_no,
        fencing_token=1,
        output_name=output_name,
        committed_output_key=compute_committed_output_key(run_id, step_name, output_name),
        resolved_input_bindings=(),
        resolved_inputs_digest=compute_resolved_inputs_digest(()),
        metrics=metrics,
        metrics_digest=compute_metrics_digest(metrics),
        status=OccurrenceStatus.COMMITTED,
        operation_id="op-1",
        asset_version_id=TypedId.from_bare("55" * 32),
        asset_type="model",
        asset_name="logistic_regression",
        label_id=TypedId.from_bare("66" * 32),
        display_version="v1",
        finalization_attestation_ref=TypedId.from_bare("77" * 32),
    )


# ── construction ─────────────────────────────────────────────────────────────────


def test_construction_derives_environment_fingerprint_from_identity():
    identity = _environment_identity()
    manager = PipelineAssetManagement(identity)
    assert manager.environment_fingerprint == compute_environment_fingerprint(identity)


def test_construction_defaults_to_a_fresh_fake_registry():
    manager_a = PipelineAssetManagement(_environment_identity())
    manager_b = PipelineAssetManagement(_environment_identity())
    assert isinstance(manager_a.registry, FakeRegistryV2)
    assert manager_a.registry is not manager_b.registry


def test_construction_accepts_injected_registry():
    registry = FakeRegistryV2()
    manager = PipelineAssetManagement(_environment_identity(), registry=registry)
    assert manager.registry is registry


# ── get_asset / get_occurrence (implemented) ──────────────────────────────────────


def test_get_asset_returns_none_when_absent():
    manager = PipelineAssetManagement(_environment_identity())
    assert manager.get_asset("aa" * 32) is None


def test_get_asset_returns_registered_record():
    registry = FakeRegistryV2()
    record = _asset_version_record()
    registry.put_asset_version(record)
    manager = PipelineAssetManagement(_environment_identity(), registry=registry)

    assert manager.get_asset(record.asset_version_id) is record
    assert manager.get_asset(record.asset_version_id.bare) is record
    assert manager.get_asset(record.asset_version_id.typed) is record


def test_get_occurrence_returns_none_when_absent():
    manager = PipelineAssetManagement(_environment_identity())
    assert manager.get_occurrence("aa" * 32) is None


def test_get_occurrence_returns_registered_record():
    registry = FakeRegistryV2()
    record = _committed_occurrence_record()
    registry.put_occurrence(record)
    manager = PipelineAssetManagement(_environment_identity(), registry=registry)

    assert manager.get_occurrence(record.occurrence_id) is record
    assert manager.get_occurrence(record.occurrence_id.typed) is record


# ── everything else fails loudly instead of degrading to V1 behavior ─────────────


@pytest.mark.parametrize(
    "method_name,args",
    [
        ("begin_run", (object(), "key-1")),
        ("resolve_inputs", ("run-1", "training")),
        ("begin_attempt", ("run-1", "training")),
        ("finalize_step_outputs", (object(),)),
        ("fail_attempt", ()),
        ("cancel_run", ("run-1", "operator cancelled")),
        ("upload_asset", ()),
        ("upload_bundle", ()),
        ("import_external", ()),
        ("download_exact", ("aa" * 32,)),
    ],
)
def test_unimplemented_methods_raise_not_implemented_error(method_name, args):
    manager = PipelineAssetManagement(_environment_identity())
    method = getattr(manager, method_name)
    with pytest.raises(NotImplementedError):
        method(*args)

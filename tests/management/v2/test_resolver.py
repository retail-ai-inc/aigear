from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.attestation import HmacTestVerifier
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.environment import RegistryBinding, generate_registry_binding_id
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.finalizer import FinalizeContext, finalize_step_outputs
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import TrustState
from aigear.management.v2.records.blob import AvailabilityState
from aigear.management.v2.records.occurrence import ResolvedInputBinding
from aigear.management.v2.records.run import RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec
from aigear.management.v2.resolver import ResolverError, Selector, SelectorKind, UsageContext, resolve
from aigear.management.v2.staging_upload import StagingOutputDescriptor, StepCompletionMessage
from aigear.management.v2.step_lease import acquire_step_lease, compute_attempt_finalize_operation_id

_FP = TypedId.from_bare("aa" * 32)
_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
_CODE_DIGEST = TypedId.from_bare("22" * 32)
_CONFIG_DIGEST = TypedId.from_bare("33" * 32)
_IMAGE_DIGEST = TypedId.from_bare("11" * 32)
_GRAPH_DIGEST = TypedId.from_bare("44" * 32)
_SCHEMA_CONTRACT_DIGEST = TypedId.from_bare("55" * 32)
_RUNTIME_CONTRACT_DIGEST = TypedId.from_bare("66" * 32)


def _layout() -> GcsLayoutV2:
    return GcsLayoutV2(bucket_name="test-bucket", project_name="proj", pipeline_version="v1")


def _control_document(**overrides) -> ControlDocument:
    defaults = dict(
        schema_version="2.0",
        environment_id="test-env",
        authority="v2",
        phase="v2_only",
        write_epoch=1,
        min_reader_version="2.0",
        min_writer_version="2.0",
        required_capabilities=("pipeline_v2",),
        environment_fingerprint=_FP,
        registry_binding=RegistryBinding(
            firestore_database_id="(default)",
            registry_binding_id=generate_registry_binding_id(),
            registry_binding_epoch=1,
            bound_environment_fingerprint=_FP,
        ),
        registry_bound_by="controller@aigear",
    )
    defaults.update(overrides)
    return ControlDocument(**defaults)


def _run_spec(step_name: str = "train", output_name: str = "model") -> RunSpec:
    return RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=_GRAPH_DIGEST,
        code_digest=_CODE_DIGEST,
        config_digest=_CONFIG_DIGEST,
        producer_image_digest=_IMAGE_DIGEST,
        steps=(
            StepSpec(
                step_name=step_name,
                outputs=(OutputSlotSpec(output_name=output_name, role="model", logical_name="weights"),),
            ),
        ),
    )


def _two_step_run_spec() -> RunSpec:
    """A "prep -> train" RunSpec, for tests that need one Step's committed
    output to be a declared input of another Step in the *same* Run."""
    return RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=_GRAPH_DIGEST,
        code_digest=_CODE_DIGEST,
        config_digest=_CONFIG_DIGEST,
        producer_image_digest=_IMAGE_DIGEST,
        steps=(
            StepSpec(
                step_name="prep",
                outputs=(OutputSlotSpec(output_name="features", role="features", logical_name="features"),),
            ),
            StepSpec(
                step_name="train",
                outputs=(OutputSlotSpec(output_name="model", role="model", logical_name="weights"),),
            ),
        ),
    )


def _finalize_context() -> FinalizeContext:
    return FinalizeContext(
        environment_id="test-env",
        environment_fingerprint=_FP,
        schema_version="2.0",
        schema_contract_digest=_SCHEMA_CONTRACT_DIGEST,
        runtime_contract_digest=_RUNTIME_CONTRACT_DIGEST,
        policy_version="policy-v1",
        now=_NOW,
    )


def _produce_committed_output(
    registry, gcs, layout, *, run_id="run-1", step_name="train", output_name="model",
    resolved_input_bindings=(), data=b"model-bytes", run_spec=None,
):
    """Drive a real lease -> stage -> finalize cycle to get an actual committed
    Occurrence/AssetVersion/Blob/Label, instead of hand-building every nested record."""
    if registry.get_run(run_id) is None:
        registry.create_run(RunRecord(run_id=run_id, status=RunStatus.PENDING))
        registry.update_run_status(run_id, RunStatus.RUNNING)
    if registry.get_step(run_id, step_name) is None:
        registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=StepStatus.READY))

    attempt = acquire_step_lease(
        registry,
        run_id=run_id,
        step_name=step_name,
        output_names=[output_name],
        resolved_input_bindings=resolved_input_bindings,
        environment_fingerprint=_FP,
        schema_version="2.0",
        owner_principal="worker-vm-1@aigear",
        now=_NOW,
    )
    operation_id = compute_attempt_finalize_operation_id(run_id, step_name, attempt.attempt_no).bare
    staging_object = layout.staging(
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        output_name=output_name,
        payload_kind="components",
        payload_key="primary",
        file_name="model.bin",
    )
    snapshot = gcs.put_object(staging_object, data, if_generation_match=0)
    descriptor = StagingOutputDescriptor(
        output_name=output_name,
        staging_object=staging_object,
        generation=snapshot.generation,
        digest=TypedId.from_bare(hashlib.sha256(data).hexdigest()),
        size=len(data),
        media_type="application/octet-stream",
    )
    message = StepCompletionMessage(
        run_id=run_id, step_name=step_name, attempt_no=attempt.attempt_no,
        operation_id=operation_id, outputs=(descriptor,),
    )
    run_spec = run_spec or _run_spec(step_name=step_name, output_name=output_name)
    outcome = finalize_step_outputs(registry, gcs, layout, message, run_spec, _finalize_context())
    return outcome.outputs[0]


def _approve(registry, asset_version):
    verified = replace(asset_version, trust_state=TrustState.VERIFIED, record_revision=asset_version.record_revision)
    registry.put_asset_version(verified)
    approved = replace(
        verified, trust_state=TrustState.APPROVED, policy_decision_head_ref=TypedId.from_bare("99" * 32)
    )
    registry.put_asset_version(approved)
    return approved


def test_resolve_by_asset_label_returns_blob_handle():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    _approve(registry, output.asset_version)

    handle = resolve(
        registry, _control_document(), layout,
        Selector.by_asset_label("model", "weights", "run-1"),
        UsageContext.MANUAL_DOWNLOAD, _NOW,
    )

    assert handle.asset_version_id == output.asset_version.asset_version_id
    assert handle.label_id == output.label.label_id
    assert len(handle.blobs) == 1
    assert handle.blobs[0].object_name == layout.canonical_blob(output.blob.blob_id)
    assert handle.blobs[0].generation == output.blob.generation
    assert handle.expires_at == _NOW + timedelta(minutes=5)


def test_resolve_cryptographically_verifies_manifest_and_location_attestations():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    _approve(registry, output.asset_version)
    verifier = HmacTestVerifier()

    handle = resolve(
        registry,
        _control_document(),
        layout,
        Selector.by_asset_label("model", "weights", "run-1"),
        UsageContext.MANUAL_DOWNLOAD,
        _NOW,
        attestation_verifier=verifier,
    )
    assert handle.asset_version_id == output.asset_version.asset_version_id

    attestation_id = output.asset_version.manifest_integrity_attestation_ref
    registry._attestations[attestation_id] = replace(
        registry.get_attestation(attestation_id), signature_b64="dGFtcGVyZWQ="
    )
    with pytest.raises(ResolverError, match="signature is invalid"):
        resolve(
            registry,
            _control_document(),
            layout,
            Selector.by_asset_label("model", "weights", "run-1"),
            UsageContext.MANUAL_DOWNLOAD,
            _NOW,
            attestation_verifier=verifier,
        )


def test_resolve_by_run_output_uses_occurrences_sealed_label():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    _approve(registry, output.asset_version)

    handle = resolve(
        registry, _control_document(), layout,
        Selector.by_run_output("run-1", "train", "model"),
        UsageContext.CROSS_RUN, _NOW,
    )

    assert handle.occurrence_id == output.occurrence.occurrence_id
    assert handle.occurrence_reference_epoch == output.occurrence.reference_epoch
    assert handle.label_id == output.occurrence.label_id


def test_resolve_by_occurrence_id():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    _approve(registry, output.asset_version)

    handle = resolve(
        registry, _control_document(), layout,
        Selector.by_occurrence(output.occurrence.occurrence_id),
        UsageContext.NEW_RUN_SEED, _NOW,
        attestation_verifier=HmacTestVerifier(),
    )
    assert handle.occurrence_id == output.occurrence.occurrence_id


def test_resolve_raw_asset_version_has_no_occurrence_or_label():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)

    handle = resolve(
        registry, _control_document(), layout,
        Selector.by_asset_version(approved.asset_version_id),
        UsageContext.MANUAL_DOWNLOAD, _NOW,
    )
    assert handle.occurrence_id is None
    assert handle.label_id is None
    assert handle.asset_version_id == approved.asset_version_id


def test_resolve_rejects_non_approved_asset_for_cross_run():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    # Freshly finalized AssetVersions start quarantined (see finalizer.py).

    with pytest.raises(ResolverError, match="requires trust_state=approved"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_asset_version(output.asset_version.asset_version_id),
            UsageContext.CROSS_RUN, _NOW,
        )


def test_resolve_rejects_archived_lifecycle():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)
    registry.put_asset_version(replace(approved, lifecycle_state=approved.lifecycle_state.ARCHIVED))

    with pytest.raises(ResolverError, match="not active"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_asset_version(approved.asset_version_id),
            UsageContext.MANUAL_DOWNLOAD, _NOW,
        )


def test_resolve_rejects_revoked_trust_even_for_same_run_direct_upstream():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    run_spec = _two_step_run_spec()
    upstream = _produce_committed_output(
        registry, gcs, layout, step_name="prep", output_name="features", run_spec=run_spec
    )
    registry.put_asset_version(replace(upstream.asset_version, trust_state=TrustState.VERIFIED))

    downstream = _produce_committed_output(
        registry, gcs, layout, step_name="train", output_name="model", run_spec=run_spec,
        resolved_input_bindings=(
            ResolvedInputBinding(binding_name="features", asset_version_id=upstream.asset_version.asset_version_id,
                                  occurrence_id=upstream.occurrence.occurrence_id),
        ),
    )
    registry.put_asset_version(replace(upstream.asset_version, trust_state=TrustState.REVOKED))

    with pytest.raises(ResolverError, match="revoked"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_occurrence(upstream.occurrence.occurrence_id),
            UsageContext.SAME_RUN_DIRECT_UPSTREAM, _NOW,
            consumer_occurrence_id=downstream.occurrence.occurrence_id,
            consumer_binding_name="features",
        )


def test_resolve_rejects_missing_blob():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)
    registry._blobs.pop(output.blob.blob_id)  # simulate a data-consistency bug

    with pytest.raises(ResolverError, match="no Blob found"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_asset_version(approved.asset_version_id),
            UsageContext.MANUAL_DOWNLOAD, _NOW,
        )


def test_resolve_rejects_blob_not_ready():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)
    registry.put_blob(replace(output.blob, availability_state=AvailabilityState.MISSING))

    with pytest.raises(ResolverError, match="not ready"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_asset_version(approved.asset_version_id),
            UsageContext.MANUAL_DOWNLOAD, _NOW,
        )


def test_resolve_rejects_control_document_with_v1_authority():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)

    with pytest.raises(ResolverError, match="authority"):
        resolve(
            registry, _control_document(authority="v1", phase="v1_only"), layout,
            Selector.by_asset_version(approved.asset_version_id),
            UsageContext.MANUAL_DOWNLOAD, _NOW,
        )


# ── same_run_direct_upstream ─────────────────────────────────────────────────────


def test_same_run_direct_upstream_allows_merely_verified_trust_with_no_policy_head():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    run_spec = _two_step_run_spec()
    upstream = _produce_committed_output(
        registry, gcs, layout, step_name="prep", output_name="features", run_spec=run_spec
    )
    registry.put_asset_version(replace(upstream.asset_version, trust_state=TrustState.VERIFIED))

    downstream = _produce_committed_output(
        registry, gcs, layout, step_name="train", output_name="model", run_spec=run_spec,
        resolved_input_bindings=(
            ResolvedInputBinding(binding_name="features", asset_version_id=upstream.asset_version.asset_version_id,
                                  occurrence_id=upstream.occurrence.occurrence_id),
        ),
    )

    handle = resolve(
        registry, _control_document(), layout,
        Selector.by_occurrence(upstream.occurrence.occurrence_id),
        UsageContext.SAME_RUN_DIRECT_UPSTREAM, _NOW,
        consumer_occurrence_id=downstream.occurrence.occurrence_id,
        consumer_binding_name="features",
    )
    assert handle.asset_version_id == upstream.asset_version.asset_version_id
    assert handle.policy_decision_head_ref is None


def test_same_run_direct_upstream_rejects_undeclared_binding():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    run_spec = _two_step_run_spec()
    upstream = _produce_committed_output(
        registry, gcs, layout, step_name="prep", output_name="features", run_spec=run_spec
    )
    registry.put_asset_version(replace(upstream.asset_version, trust_state=TrustState.VERIFIED))
    downstream = _produce_committed_output(
        registry, gcs, layout, step_name="train", output_name="model", run_spec=run_spec
    )

    with pytest.raises(ResolverError, match="no resolved input binding"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_occurrence(upstream.occurrence.occurrence_id),
            UsageContext.SAME_RUN_DIRECT_UPSTREAM, _NOW,
            consumer_occurrence_id=downstream.occurrence.occurrence_id,
            consumer_binding_name="features",
        )


def test_same_run_direct_upstream_requires_consumer_occurrence_id():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    registry.put_asset_version(replace(output.asset_version, trust_state=TrustState.VERIFIED))

    with pytest.raises(ResolverError, match="requires consumer_occurrence_id"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_occurrence(output.occurrence.occurrence_id),
            UsageContext.SAME_RUN_DIRECT_UPSTREAM, _NOW,
        )


def test_same_run_direct_upstream_rejects_selectors_without_an_occurrence():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    registry.put_asset_version(replace(output.asset_version, trust_state=TrustState.VERIFIED))

    with pytest.raises(ResolverError, match="resolves to an Occurrence"):
        resolve(
            registry, _control_document(), layout,
            Selector.by_asset_version(output.asset_version.asset_version_id),
            UsageContext.SAME_RUN_DIRECT_UPSTREAM, _NOW,
            consumer_occurrence_id=output.occurrence.occurrence_id,
            consumer_binding_name="features",
        )


# ── Selector ─────────────────────────────────────────────────────────────────────


def test_selector_by_asset_label_rejects_missing_fields_via_constructor():
    with pytest.raises(ResolverError, match="requires"):
        Selector(kind=SelectorKind.ASSET_LABEL, asset_type="model")


def test_selector_rejects_extra_fields_for_its_kind():
    with pytest.raises(ResolverError, match="must not set"):
        Selector(kind=SelectorKind.ASSET_VERSION, asset_version_id=TypedId.from_bare("aa" * 32), run_id="run-1")

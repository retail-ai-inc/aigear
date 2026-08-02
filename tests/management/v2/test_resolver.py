from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.attestation import (
    AttestationRecord,
    HmacTestSigner,
    HmacTestVerifier,
)
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
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionEpochBinding,
    PolicyDecisionHead,
    PolicyDecisionUnsignedEnvelope,
    compute_subject_epoch_key,
)
from aigear.management.v2.records.run import RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec
from aigear.management.v2.resolver import (
    ResolverError,
    Selector,
    SelectorKind,
    UsageContext,
    require_handle_usage_context,
    resolve,
)
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
    envelope = PolicyDecisionUnsignedEnvelope(
        schema_version="2.0",
        operation_id="approve-1",
        fencing_token=1,
        environment_id="test-env",
        environment_fingerprint=_FP,
        subject_asset_version_id=asset_version.asset_version_id,
        decision=PolicyDecision.APPROVED,
        decision_epoch=1,
        policy_version="policy-v1",
        policy_snapshot_digest=TypedId.from_bare("91" * 32),
        evidence_digests=(
            TypedId.from_bare("92" * 32),
            TypedId.from_bare("93" * 32),
        ),
        evidence_closure_digest=TypedId.from_bare("94" * 32),
        firestore_read_time="2026-07-23T23:59:00+00:00",
        issued_at="2026-07-23T23:59:01+00:00",
        not_before="2026-07-23T23:59:01+00:00",
        valid_until="2026-07-24T01:00:00+00:00",
        key_version="test-only",
    )
    signer = HmacTestSigner()
    attestation = AttestationRecord(
        schema_version="2.0",
        attestation_kind="policy_decision",
        attestation_id=envelope.digest,
        environment_fingerprint=_FP,
        unsigned_envelope=envelope.to_jcs_dict(),
        key_version=signer.key_version,
        signature_b64=base64.b64encode(
            signer.sign_sha256_digest(bytes.fromhex(envelope.digest.bare))
        ).decode("ascii"),
    )
    approved = replace(
        verified,
        trust_state=TrustState.APPROVED,
        policy_decision_head_ref=attestation.attestation_id,
    )
    registry.put_asset_version(approved)
    registry.put_attestation(attestation)
    registry.put_policy_decision_epoch(
        PolicyDecisionEpochBinding(
            schema_version="2.0",
            environment_fingerprint=_FP,
            subject_epoch_key=compute_subject_epoch_key(asset_version.asset_version_id, 1),
            subject_asset_version_id=asset_version.asset_version_id,
            decision_epoch=1,
            attestation_id=attestation.attestation_id,
            decision=PolicyDecision.APPROVED,
            policy_version="policy-v1",
            not_before=envelope.not_before,
            valid_until=envelope.valid_until,
        )
    )
    registry.put_policy_decision_head(
        PolicyDecisionHead(
            schema_version="2.0",
            environment_fingerprint=_FP,
            subject_asset_version_id=asset_version.asset_version_id,
            current_epoch=1,
            revision=1,
            attestation_id=attestation.attestation_id,
            decision=PolicyDecision.APPROVED,
            policy_version="policy-v1",
            not_before=envelope.not_before,
            valid_until=envelope.valid_until,
        )
    )
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
        attestation_verifier=HmacTestVerifier(),
        required_policy_version="policy-v1",
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
        required_policy_version="policy-v1",
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


@pytest.mark.parametrize(
    "usage_context",
    (
        UsageContext.NEW_RUN_SEED,
        UsageContext.CROSS_RUN,
        UsageContext.ALIAS,
        UsageContext.RELEASE,
        UsageContext.SERVICE_RUNTIME,
    ),
)
def test_strict_contexts_verify_current_policy_chain(usage_context):
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)

    handle = resolve(
        registry,
        _control_document(),
        layout,
        Selector.by_asset_version(approved.asset_version_id),
        usage_context,
        _NOW,
        ttl=timedelta(hours=2),
        attestation_verifier=HmacTestVerifier(),
        required_policy_version="policy-v1",
    )

    assert handle.policy_decision_epoch == 1
    assert handle.policy_version == "policy-v1"
    assert handle.policy_valid_until == "2026-07-24T01:00:00+00:00"
    assert handle.expires_at == datetime.fromisoformat(handle.policy_valid_until)


def test_approved_projection_alone_cannot_bypass_missing_policy_head():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    registry.put_asset_version(
        replace(
            output.asset_version,
            trust_state=TrustState.APPROVED,
            policy_decision_head_ref=TypedId.from_bare("99" * 32),
        )
    )

    with pytest.raises(ResolverError, match="policy head is not effective"):
        resolve(
            registry,
            _control_document(),
            layout,
            Selector.by_asset_version(output.asset_version.asset_version_id),
            UsageContext.NEW_RUN_SEED,
            _NOW,
            attestation_verifier=HmacTestVerifier(),
            required_policy_version="policy-v1",
        )


def test_strict_context_requires_verifier_and_current_policy_version():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)
    selector = Selector.by_asset_version(approved.asset_version_id)

    with pytest.raises(ResolverError, match="attestation verifier"):
        resolve(
            registry,
            _control_document(),
            layout,
            selector,
            UsageContext.RELEASE,
            _NOW,
            required_policy_version="policy-v1",
        )
    with pytest.raises(ResolverError, match="current policy version"):
        resolve(
            registry,
            _control_document(),
            layout,
            selector,
            UsageContext.RELEASE,
            _NOW,
            attestation_verifier=HmacTestVerifier(),
        )


def test_strict_context_rejects_policy_signature_or_version_mismatch():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)
    selector = Selector.by_asset_version(approved.asset_version_id)

    with pytest.raises(ResolverError, match="not effective"):
        resolve(
            registry,
            _control_document(),
            layout,
            selector,
            UsageContext.RELEASE,
            _NOW,
            attestation_verifier=HmacTestVerifier(),
            required_policy_version="policy-v2",
        )

    attestation_id = approved.policy_decision_head_ref
    registry._attestations[attestation_id] = replace(
        registry.get_attestation(attestation_id), signature_b64="dGFtcGVyZWQ="
    )
    with pytest.raises(ResolverError, match="policy attestation signature"):
        resolve(
            registry,
            _control_document(),
            layout,
            selector,
            UsageContext.RELEASE,
            _NOW,
            attestation_verifier=HmacTestVerifier(),
            required_policy_version="policy-v1",
        )


def test_expired_or_revoked_head_immediately_blocks_new_authorization():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)
    head = registry.get_policy_decision_head(approved.asset_version_id)
    selector = Selector.by_asset_version(approved.asset_version_id)

    registry._policy_decision_heads[approved.asset_version_id] = replace(
        head,
        not_before="2026-07-23T22:00:00+00:00",
        valid_until="2026-07-23T23:00:00+00:00",
    )
    with pytest.raises(ResolverError, match="not effective"):
        resolve(
            registry,
            _control_document(),
            layout,
            selector,
            UsageContext.SERVICE_RUNTIME,
            _NOW,
            attestation_verifier=HmacTestVerifier(),
            required_policy_version="policy-v1",
        )

    registry._policy_decision_heads[approved.asset_version_id] = replace(
        head, decision=PolicyDecision.REVOKED
    )
    with pytest.raises(ResolverError, match="not effective"):
        resolve(
            registry,
            _control_document(),
            layout,
            selector,
            UsageContext.SERVICE_RUNTIME,
            _NOW,
            attestation_verifier=HmacTestVerifier(),
            required_policy_version="policy-v1",
        )


def test_resolved_handle_cannot_be_reused_for_another_context():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    approved = _approve(registry, output.asset_version)
    handle = resolve(
        registry,
        _control_document(),
        layout,
        Selector.by_asset_version(approved.asset_version_id),
        UsageContext.MANUAL_DOWNLOAD,
        _NOW,
    )

    assert require_handle_usage_context(handle, UsageContext.MANUAL_DOWNLOAD) is handle
    with pytest.raises(ResolverError, match="not 'release'"):
        require_handle_usage_context(handle, UsageContext.RELEASE)


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

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aigear.management.pipeline_asset import PipelineAssetManagement, PipelineAssetManagementError
from aigear.management.v2.attestation import (
    AttestationRecord,
    HmacTestSigner,
    HmacTestVerifier,
)
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.environment import (
    EnvironmentIdentity,
    RegistryBinding,
    compute_environment_fingerprint,
    generate_registry_binding_id,
)
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.fake_gcs import FakeGcsClient
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
from aigear.management.v2.records.run import RunStatus, StepStatus
from aigear.management.v2.records.run_spec import (
    OutputSlotSpec,
    RunSpec,
    SeedInputBinding,
    StepSpec,
)
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionEpochBinding,
    PolicyDecisionHead,
    PolicyDecisionUnsignedEnvelope,
    compute_subject_epoch_key,
)
from aigear.management.v2.staging_upload import StagingOutputDescriptor, StepCompletionMessage
from aigear.management.v2.step_lease import compute_attempt_finalize_operation_id

_FINGERPRINT_HEX = "aa" * 32
_BLOB_HEX = "bb" * 32
_ATTESTATION_HEX = "cc" * 32
_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)


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


class _ProductionSigner:
    def __init__(self, key_version):
        self.key_version = key_version

    def sign_sha256_digest(self, digest):
        return b"signature"


class _ProductionVerifier:
    def verify_sha256_digest(self, *, key_version, digest, signature):
        return None


def _production_control_document():
    fingerprint = compute_environment_fingerprint(_environment_identity())
    return ControlDocument(
        schema_version="2.0",
        environment_id="production",
        authority="v2",
        phase="v2_authoritative",
        write_epoch=2,
        min_reader_version="2.0",
        min_writer_version="2.0",
        required_capabilities=("pipeline_v2",),
        environment_fingerprint=fingerprint,
        registry_binding=RegistryBinding(
            firestore_database_id="aigear-prod",
            registry_binding_id=generate_registry_binding_id(),
            registry_binding_epoch=1,
            bound_environment_fingerprint=fingerprint,
        ),
        registry_bound_by="controller@aigear",
        firestore_database_resource="projects/p/databases/aigear-prod",
    )


def _production_kwargs():
    return {
        "schema_contract_digest": TypedId.from_bare("dd" * 32),
        "runtime_contract_digest": TypedId.from_bare("ee" * 32),
        "policy_version": "policy-v1",
        "control_document": _production_control_document(),
        "manifest_integrity_signer": _ProductionSigner("key/cryptoKeyVersions/1"),
        "blob_location_signer": _ProductionSigner("key/cryptoKeyVersions/2"),
        "occurrence_finalization_signer": _ProductionSigner("key/cryptoKeyVersions/3"),
        "attestation_verifier": _ProductionVerifier(),
        "allowed_completion_publishers": ("push@p.iam.gserviceaccount.com",),
        "completion_oidc_audience": "https://finalizer.example.test/completion",
        "production": True,
    }


def test_production_mode_rejects_fake_backends():
    with pytest.raises(PipelineAssetManagementError, match="refuses Fake"):
        PipelineAssetManagement(_environment_identity(), **_production_kwargs())


def test_production_mode_requires_three_distinct_attestation_keys():
    kwargs = _production_kwargs()
    kwargs["blob_location_signer"] = _ProductionSigner("key/cryptoKeyVersions/1")
    with pytest.raises(PipelineAssetManagementError, match="distinct pinned keys"):
        PipelineAssetManagement(
            _environment_identity(),
            registry=object(),
            gcs=object(),
            **kwargs,
        )


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


# ── execution lifecycle wiring (T28) ──────────────────────────────────────────────


def _two_step_run_spec() -> RunSpec:
    """A "prep -> train" RunSpec: train declares prep as a dependency, so
    begin_run must leave it blocked until resolve_inputs runs."""
    return RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=TypedId.from_bare("44" * 32),
        code_digest=TypedId.from_bare("22" * 32),
        config_digest=TypedId.from_bare("33" * 32),
        producer_image_digest=TypedId.from_bare("11" * 32),
        steps=(
            StepSpec(
                step_name="prep",
                outputs=(OutputSlotSpec(output_name="features", role="features", logical_name="features"),),
            ),
            StepSpec(
                step_name="train",
                outputs=(OutputSlotSpec(output_name="model", role="model", logical_name="weights"),),
                dependencies=("prep",),
            ),
        ),
    )


def _fully_configured_manager() -> PipelineAssetManagement:
    fingerprint = compute_environment_fingerprint(_environment_identity())
    control_document = ControlDocument(
        schema_version="2.0",
        environment_id="production",
        authority="v2",
        phase="v2_only",
        write_epoch=1,
        min_reader_version="2.0",
        min_writer_version="2.0",
        required_capabilities=("pipeline_v2",),
        environment_fingerprint=fingerprint,
        registry_binding=RegistryBinding(
            firestore_database_id="(default)",
            registry_binding_id=generate_registry_binding_id(),
            registry_binding_epoch=1,
            bound_environment_fingerprint=fingerprint,
        ),
        registry_bound_by="controller@aigear",
    )
    return PipelineAssetManagement(
        _environment_identity(),
        schema_contract_digest=TypedId.from_bare("dd" * 32),
        runtime_contract_digest=TypedId.from_bare("ee" * 32),
        policy_version="policy-v1",
        control_document=control_document,
        attestation_signer=HmacTestSigner(),
        attestation_verifier=HmacTestVerifier(),
    )


def _approve_asset(manager: PipelineAssetManagement, asset: AssetVersionRecord) -> None:
    envelope = PolicyDecisionUnsignedEnvelope(
        schema_version="2.0",
        operation_id="approve-seed",
        fencing_token=1,
        environment_id="production",
        environment_fingerprint=manager.environment_fingerprint,
        subject_asset_version_id=asset.asset_version_id,
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
        valid_until="2099-07-24T01:00:00+00:00",
        key_version="test-only",
    )
    signer = HmacTestSigner()
    attestation = AttestationRecord(
        schema_version="2.0",
        attestation_kind="policy_decision",
        attestation_id=envelope.digest,
        environment_fingerprint=manager.environment_fingerprint,
        unsigned_envelope=envelope.to_jcs_dict(),
        key_version=signer.key_version,
        signature_b64=base64.b64encode(
            signer.sign_sha256_digest(bytes.fromhex(envelope.digest.bare))
        ).decode("ascii"),
    )
    manager.registry.put_attestation(attestation)
    manager.registry.put_policy_decision_epoch(
        PolicyDecisionEpochBinding(
            schema_version="2.0",
            environment_fingerprint=manager.environment_fingerprint,
            subject_epoch_key=compute_subject_epoch_key(asset.asset_version_id, 1),
            subject_asset_version_id=asset.asset_version_id,
            decision_epoch=1,
            attestation_id=attestation.attestation_id,
            decision=PolicyDecision.APPROVED,
            policy_version="policy-v1",
            not_before=envelope.not_before,
            valid_until=envelope.valid_until,
        )
    )
    manager.registry.put_policy_decision_head(
        PolicyDecisionHead(
            schema_version="2.0",
            environment_fingerprint=manager.environment_fingerprint,
            subject_asset_version_id=asset.asset_version_id,
            current_epoch=1,
            revision=1,
            attestation_id=attestation.attestation_id,
            decision=PolicyDecision.APPROVED,
            policy_version="policy-v1",
            not_before=envelope.not_before,
            valid_until=envelope.valid_until,
        )
    )
    manager.registry.put_asset_version(
        replace(
            asset,
            trust_state=TrustState.APPROVED,
            policy_decision_head_ref=attestation.attestation_id,
        )
    )


# begin_run's idempotency_key must already be a caller-computed digest (spec
# 10.1's compute_run_idempotency_key, T15) -- a plain human-readable string is
# not itself a valid TypedId, so tests use a fixed stand-in digest.
_IDEMPOTENCY_KEY = TypedId.from_bare("ff" * 32)


def _run_step_to_completion(manager: PipelineAssetManagement, run_id: str, step_name: str, output_name: str, data: bytes):
    """Drive one Step through begin_attempt -> stage -> finalize_step_outputs."""
    attempt = manager.begin_attempt(run_id, step_name, owner_principal="worker-vm-1@aigear", now=_NOW)
    operation_id = compute_attempt_finalize_operation_id(run_id, step_name, attempt.attempt_no).bare
    staging_object = manager.layout.staging(
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        output_name=output_name,
        payload_kind="components",
        payload_key="primary",
        file_name="data.bin",
    )
    snapshot = manager.gcs.put_object(staging_object, data, if_generation_match=0)
    message = StepCompletionMessage(
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        outputs=(
            StagingOutputDescriptor(
                output_name=output_name,
                staging_object=staging_object,
                generation=snapshot.generation,
                digest=TypedId.from_bare(hashlib.sha256(data).hexdigest()),
                size=len(data),
                media_type="application/octet-stream",
            ),
        ),
    )
    return manager.finalize_step_outputs(message, now=_NOW)


def test_begin_run_creates_a_running_run_with_blocked_and_ready_steps():
    manager = _fully_configured_manager()
    run_spec = _two_step_run_spec()

    run = manager.begin_run(run_spec, _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear")

    assert run.status == RunStatus.RUNNING
    assert manager.registry.get_step(run.run_id, "prep").status == StepStatus.READY
    assert manager.registry.get_step(run.run_id, "train").status == StepStatus.BLOCKED


def test_begin_run_replays_the_same_run_on_a_repeated_idempotency_key():
    manager = _fully_configured_manager()
    run_spec = _two_step_run_spec()

    first = manager.begin_run(run_spec, _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear")
    second = manager.begin_run(run_spec, _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear")

    assert second.run_id == first.run_id


def test_legacy_seed_input_is_sealed_onto_root_step_and_revalidated_at_lease():
    manager = _fully_configured_manager()
    source_run = manager.begin_run(
        _two_step_run_spec(), _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear"
    )
    source = _run_step_to_completion(
        manager, source_run.run_id, "prep", "features", b"seed-features"
    ).outputs[0]
    _approve_asset(manager, source.asset_version)
    seeded_spec = RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=TypedId.from_bare("41" * 32),
        code_digest=TypedId.from_bare("42" * 32),
        config_digest=TypedId.from_bare("43" * 32),
        producer_image_digest=TypedId.from_bare("44" * 32),
        steps=(
            StepSpec(
                step_name="train",
                outputs=(OutputSlotSpec("model", "model", "weights"),),
            ),
        ),
        seed_inputs=(
            SeedInputBinding(
                binding_name="features",
                asset_version_id=source.asset_version.asset_version_id,
                occurrence_id=source.occurrence.occurrence_id,
                source_label_id=source.label.label_id,
            ),
        ),
    )

    seeded_run = manager.begin_run(
        seeded_spec,
        TypedId.from_bare("97" * 32),
        owner_principal="scheduler@aigear",
    )
    step = manager.registry.get_step(seeded_run.run_id, "train")

    assert step.resolved_inputs[0].binding_name == "features"
    assert step.resolved_inputs_digest is not None
    attempt = manager.begin_attempt(
        seeded_run.run_id, "train", owner_principal="worker@test", now=_NOW
    )
    assert attempt.attempt_no == 1


def test_begin_run_rechecks_current_policy_head_inside_atomic_commit():
    manager = _fully_configured_manager()
    source_run = manager.begin_run(
        _two_step_run_spec(), _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear"
    )
    source = _run_step_to_completion(
        manager, source_run.run_id, "prep", "features", b"seed-features"
    ).outputs[0]
    _approve_asset(manager, source.asset_version)
    seeded_spec = RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=TypedId.from_bare("41" * 32),
        code_digest=TypedId.from_bare("42" * 32),
        config_digest=TypedId.from_bare("43" * 32),
        producer_image_digest=TypedId.from_bare("44" * 32),
        steps=(
            StepSpec(
                step_name="train",
                outputs=(OutputSlotSpec("model", "model", "weights"),),
            ),
        ),
        seed_inputs=(
            SeedInputBinding(
                binding_name="features",
                asset_version_id=source.asset_version.asset_version_id,
                occurrence_id=source.occurrence.occurrence_id,
                source_label_id=source.label.label_id,
            ),
        ),
    )
    original_run_atomic = manager.registry.run_atomic

    def revoke_before_callback(work):
        asset_id = source.asset_version.asset_version_id
        head = manager.registry.get_policy_decision_head(asset_id)
        manager.registry._policy_decision_heads[asset_id] = replace(
            head, decision=PolicyDecision.REVOKED
        )
        return original_run_atomic(work)

    manager.registry.run_atomic = revoke_before_callback
    run_count = len(manager.registry._runs)

    with pytest.raises(PipelineAssetManagementError, match="policy head"):
        manager.begin_run(
            seeded_spec,
            TypedId.from_bare("96" * 32),
            owner_principal="scheduler@aigear",
        )
    assert len(manager.registry._runs) == run_count


def test_full_run_lifecycle_through_finalize_and_download(tmp_path):
    manager = _fully_configured_manager()
    run_spec = _two_step_run_spec()
    run = manager.begin_run(run_spec, _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear")

    prep_outcome = _run_step_to_completion(manager, run.run_id, "prep", "features", b"features-bytes")
    assert manager.registry.get_step(run.run_id, "prep").status == StepStatus.SUCCEEDED

    train_step = manager.resolve_inputs(run.run_id, "train", now=_NOW)
    assert train_step.status == StepStatus.READY
    assert train_step.resolved_inputs[0].binding_name == "prep.features"

    train_outcome = _run_step_to_completion(manager, run.run_id, "train", "model", b"model-bytes")
    assert manager.registry.get_step(run.run_id, "train").status == StepStatus.SUCCEEDED
    assert manager.registry.get_run(run.run_id).status == RunStatus.SUCCEEDED

    # download_exact's default usage_context (manual_download) requires an
    # approved AssetVersion (spec 6.4); a freshly finalized one starts
    # quarantined, so approve it first, mirroring a real policy-engine step.
    trained_asset = manager.get_asset(train_outcome.outputs[0].asset_version.asset_version_id)
    manager.registry.put_asset_version(replace(trained_asset, trust_state=TrustState.VERIFIED))
    manager.registry.put_asset_version(
        replace(
            trained_asset,
            trust_state=TrustState.APPROVED,
            policy_decision_head_ref=TypedId.from_bare("99" * 32),
        )
    )

    downloaded = manager.download_exact(
        None,
        train_outcome.outputs[0].occurrence.occurrence_id,
        target_path=tmp_path / "model.bin",
        now=_NOW,
    )
    assert downloaded.read_bytes() == b"model-bytes"
    assert prep_outcome.outputs[0].output_name == "features"


def test_finalize_never_calls_gcs_or_signer_inside_registry_transaction():
    class GuardRegistry(FakeRegistryV2):
        inside_transaction = False

        def run_atomic(self, work):
            def guarded(tx):
                self.inside_transaction = True
                try:
                    return work(tx)
                finally:
                    self.inside_transaction = False

            return super().run_atomic(guarded)

    class GuardGcs:
        def __init__(self, registry):
            self.registry = registry
            self.backing = FakeGcsClient()

        def __getattr__(self, name):
            target = getattr(self.backing, name)

            def guarded(*args, **kwargs):
                assert not self.registry.inside_transaction
                return target(*args, **kwargs)

            return guarded

    class GuardSigner:
        def __init__(self, registry, key_version):
            self.registry = registry
            self.backing = HmacTestSigner(key_version=key_version)
            self.key_version = key_version

        def sign_sha256_digest(self, digest):
            assert not self.registry.inside_transaction
            return self.backing.sign_sha256_digest(digest)

    manager = _fully_configured_manager()
    registry = GuardRegistry()
    manager.registry = registry
    manager.gcs = GuardGcs(registry)
    manager.manifest_integrity_signer = GuardSigner(registry, "manifest-test")
    manager.blob_location_signer = GuardSigner(registry, "location-test")
    manager.occurrence_finalization_signer = GuardSigner(registry, "occurrence-test")
    run_spec = _two_step_run_spec()
    run = manager.begin_run(run_spec, _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear")

    outcome = _run_step_to_completion(
        manager, run.run_id, "prep", "features", b"features-bytes"
    )

    assert outcome.outputs[0].occurrence.status == OccurrenceStatus.COMMITTED


def test_finalize_terminal_replay_does_not_require_staging_object(monkeypatch):
    manager = _fully_configured_manager()
    run = manager.begin_run(
        _two_step_run_spec(), _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear"
    )
    attempt = manager.begin_attempt(
        run.run_id, "prep", owner_principal="worker@test", now=_NOW
    )
    operation_id = compute_attempt_finalize_operation_id(
        run.run_id, "prep", attempt.attempt_no
    ).bare
    object_name = manager.layout.staging(
        run_id=run.run_id,
        step_name="prep",
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        output_name="features",
        payload_kind="components",
        payload_key="primary",
        file_name="features.bin",
    )
    data = b"features"
    snapshot = manager.gcs.put_object(object_name, data, if_generation_match=0)
    message = StepCompletionMessage(
        run_id=run.run_id,
        step_name="prep",
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        outputs=(
            StagingOutputDescriptor(
                output_name="features",
                staging_object=object_name,
                generation=snapshot.generation,
                digest=TypedId.from_bare(hashlib.sha256(data).hexdigest()),
                size=len(data),
                media_type="application/octet-stream",
            ),
        ),
    )
    first = manager.finalize_step_outputs(message, now=_NOW)
    monkeypatch.setattr(
        manager.gcs,
        "get_object",
        lambda *args, **kwargs: pytest.fail("terminal replay read staging GCS"),
    )

    second = manager.finalize_step_outputs(message, now=_NOW)

    assert second.outputs[0].occurrence.occurrence_id == first.outputs[0].occurrence.occurrence_id


def test_fail_attempt_and_cancel_run_are_wired_through():
    manager = _fully_configured_manager()
    run_spec = _two_step_run_spec()
    run = manager.begin_run(run_spec, _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear")

    attempt = manager.begin_attempt(run.run_id, "prep", owner_principal="worker-vm-1@aigear", now=_NOW)
    step = manager.fail_attempt(
        run.run_id, "prep", attempt.attempt_no,
        fencing_token=attempt.fencing_token, retryable=True, reason="transient crash",
    )
    assert step.status == StepStatus.RETRY_WAIT

    cancelled_run = manager.cancel_run(run.run_id, "operator requested cancellation")
    assert cancelled_run.status == RunStatus.CANCELLED
    assert manager.registry.get_step(run.run_id, "prep").status == StepStatus.CANCELLED
    assert manager.registry.get_step(run.run_id, "train").status == StepStatus.CANCELLED


def test_resolve_inputs_and_begin_attempt_reject_unknown_run():
    manager = _fully_configured_manager()
    with pytest.raises(PipelineAssetManagementError, match="no RunSpec cached"):
        manager.resolve_inputs("no-such-run", "prep", now=_NOW)
    with pytest.raises(PipelineAssetManagementError, match="no RunSpec cached"):
        manager.begin_attempt("no-such-run", "prep", owner_principal="worker@aigear", now=_NOW)


def test_finalize_step_outputs_requires_contract_digests_and_policy_version():
    manager = PipelineAssetManagement(_environment_identity())
    run_spec = _two_step_run_spec()
    run = manager.begin_run(run_spec, _IDEMPOTENCY_KEY, owner_principal="scheduler@aigear")
    # _require_finalize_context is checked before this message is ever handed
    # to the real finalizer, so it only needs to satisfy StepCompletionMessage's
    # own shape validation, not describe a real staged upload.
    message = StepCompletionMessage(
        run_id=run.run_id,
        step_name="prep",
        attempt_no=1,
        operation_id="op-1",
        outputs=(
            StagingOutputDescriptor(
                output_name="features",
                staging_object="unused/staging/object",
                generation="1",
                digest=TypedId.from_bare("aa" * 32),
                size=4,
                media_type="application/octet-stream",
            ),
        ),
    )

    with pytest.raises(PipelineAssetManagementError, match="schema_contract_digest"):
        manager.finalize_step_outputs(message, now=_NOW)


def test_download_exact_requires_control_document(tmp_path):
    manager = PipelineAssetManagement(
        _environment_identity(),
        schema_contract_digest=TypedId.from_bare("dd" * 32),
        runtime_contract_digest=TypedId.from_bare("ee" * 32),
        policy_version="policy-v1",
    )
    with pytest.raises(PipelineAssetManagementError, match="control_document"):
        manager.download_exact(TypedId.from_bare("55" * 32), target_path=tmp_path / "unused.bin", now=_NOW)


def test_download_exact_rejects_both_or_neither_selector_argument(tmp_path):
    manager = _fully_configured_manager()
    with pytest.raises(PipelineAssetManagementError, match="exactly one of"):
        manager.download_exact(target_path=tmp_path / "unused.bin", now=_NOW)

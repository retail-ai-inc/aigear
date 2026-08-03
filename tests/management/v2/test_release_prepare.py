from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import TrustState
from aigear.management.v2.records.release import ServiceReleaseState
from aigear.management.v2.release_lease import ReleaseExternalState
from aigear.management.v2.release_manifest import (
    ImmutableConfigReference,
    ConfigReferenceKind,
    ReleaseAssetContract,
    ReleaseImageBinding,
    ReleaseManifestCore,
    ReleaseWorkloadSecurity,
    RuntimeContract,
    build_release_asset_binding,
    sign_release_manifest,
)
from aigear.management.v2.release_prepare import (
    ReleasePrepareConflict,
    ReleasePrepareError,
    prepare_release,
)
from aigear.management.v2.resolver import Selector, UsageContext, resolve
from tests.management.v2.test_release_registry import _release
from tests.management.v2.test_resolver import (
    _FP,
    _NOW,
    _approve,
    _control_document,
    _layout,
    _produce_committed_output,
)


_RELEASE_KEY = "release-key-version-1"


class Registry(FakeRegistryV2):
    def __init__(self, control):
        super().__init__(server_read_time=_NOW)
        self.control = control

    def get_control_document(self):
        return self.control


def _inputs():
    control = _control_document()
    registry = Registry(control)
    layout = _layout()
    output = _produce_committed_output(
        registry, FakeGcsClient(), layout, data=b"release-model"
    )
    asset = _approve(registry, output.asset_version)
    handle = resolve(
        registry,
        control,
        layout,
        Selector.by_asset_version(asset.asset_version_id),
        UsageContext.RELEASE,
        _NOW,
        attestation_verifier=HmacTestVerifier(),
        required_policy_version="policy-v1",
    )
    binding = build_release_asset_binding("model", handle, asset, now=_NOW)
    runtime = RuntimeContract(
        protocol="grpc",
        abi_version="v1",
        model_format="safetensors",
        input_schema_digest=TypedId.from_bare("01" * 32),
        output_schema_digest=TypedId.from_bare("02" * 32),
        shape_contract_digest=TypedId.from_bare("03" * 32),
        asset_contracts=(
            ReleaseAssetContract(
                binding_name="model",
                schema_contract_digest=binding.schema_contract_digest,
                runtime_contract_digest=binding.runtime_contract_digest,
            ),
        ),
    )
    image_digest = TypedId.from_bare("04" * 32)
    config_digest = TypedId.from_bare("05" * 32)
    core = ReleaseManifestCore(
        schema_version="2.0",
        environment_fingerprint=_FP,
        service_name="predictor",
        deployment_target_id="prod-cluster",
        image=ReleaseImageBinding(
            image_reference=f"repo/predictor@{image_digest.typed}",
            image_digest=image_digest,
            provenance_attestation_id=TypedId.from_bare("06" * 32),
            sbom_digest=TypedId.from_bare("07" * 32),
        ),
        assets=(binding,),
        config_references=(
            ImmutableConfigReference(
                kind=ConfigReferenceKind.KUBERNETES_CONFIG_MAP,
                name="predictor-config-v1",
                version=config_digest.typed,
                content_digest=config_digest,
            ),
        ),
        runtime_contract=runtime,
        workload_security=ReleaseWorkloadSecurity(
            run_as_non_root=True,
            read_only_root_filesystem=True,
            allow_privilege_escalation=False,
            drop_capabilities=("ALL",),
            seccomp_profile="RuntimeDefault",
            cpu_request="250m",
            cpu_limit="1",
            memory_request="256Mi",
            memory_limit="1Gi",
            startup_probe_path="/startupz",
            readiness_probe_path="/readyz",
            liveness_probe_path="/livez",
            sandbox_runtime_class=None,
            sandbox_policy_id=None,
        ),
        deployment_spec_digest=TypedId.from_bare("09" * 32),
    )
    manifest = sign_release_manifest(
        core,
        signer=HmacTestSigner(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
    )
    return registry, control, layout, asset, runtime, manifest


def _prepare(registry, control, layout, runtime, manifest, **overrides):
    values = dict(
        control=control,
        layout=layout,
        manifest=manifest,
        service_name="predictor",
        deployment_target_id="prod-cluster",
        expected_runtime_contract=runtime,
        asset_attestation_verifier=HmacTestVerifier(),
        release_attestation_verifier=HmacTestVerifier(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        verify_current_image=lambda _image, _at: None,
        idempotency_key="publish-1",
        owner_principal="publisher-a@example.test",
    )
    values.update(overrides)
    return prepare_release(registry, **values)


def test_prepare_requires_current_image_policy_before_release_commit():
    registry, control, layout, _asset, runtime, manifest = _inputs()

    def reject(_image, _at):
        raise ValueError("Critical vulnerability")

    with pytest.raises(ReleasePrepareError, match="verification failed"):
        _prepare(
            registry,
            control,
            layout,
            runtime,
            manifest,
            verify_current_image=reject,
        )

    assert registry.get_release(manifest.release_id) is None


def test_prepare_allocates_version_and_only_changes_desired_state():
    registry, control, layout, _asset, runtime, manifest = _inputs()
    old_release = _release(release_id=TypedId.from_bare("ab" * 32))
    registry.put_release(old_release)
    registry.put_service_release_state(
        ServiceReleaseState(
            schema_version="2.0",
            environment_fingerprint=_FP,
            service_name="predictor",
            revision=1,
            display_version_counter=7,
            observed_release_id=old_release.release_id,
            observed_evidence_revision=3,
            traffic_release_id=old_release.release_id,
            traffic_k8s_resource_version="51",
            champion_release_id=old_release.release_id,
            security_watermark=4,
            updated_at=_NOW.isoformat(),
        )
    )

    result = _prepare(registry, control, layout, runtime, manifest)

    assert result.release.display_version == "service-v8"
    assert result.service_state.desired_release_id == manifest.release_id
    assert result.service_state.observed_release_id == old_release.release_id
    assert result.service_state.traffic_release_id == old_release.release_id
    assert result.service_state.champion_release_id == old_release.release_id
    assert result.service_state.security_watermark == 4
    assert result.operation.phase.value == "preparing"
    assert result.operation.expected_service_revision == result.service_state.revision


def test_prepare_retry_reuses_release_and_does_not_increment_version():
    registry, control, layout, _asset, runtime, manifest = _inputs()
    first = _prepare(registry, control, layout, runtime, manifest)
    second = _prepare(registry, control, layout, runtime, manifest)

    assert second.release == first.release
    assert second.service_state.display_version_counter == 1
    assert second.service_state.desired_revision == 1


def test_same_core_new_operation_reuses_content_addressed_release():
    registry, control, layout, _asset, runtime, manifest = _inputs()
    first = _prepare(registry, control, layout, runtime, manifest)
    registry._server_read_time = _NOW + timedelta(seconds=121)

    second = _prepare(
        registry,
        control,
        layout,
        runtime,
        manifest,
        idempotency_key="publish-2",
        owner_principal="publisher-b@example.test",
        read_external_state=lambda _name: ReleaseExternalState(),
    )

    assert second.release == first.release
    assert second.service_state.display_version_counter == 1
    assert second.service_state.desired_revision == 1


def test_revoked_asset_fails_before_release_record_commit():
    registry, control, layout, asset, runtime, manifest = _inputs()
    registry.put_asset_version(replace(asset, trust_state=TrustState.REVOKED))

    with pytest.raises(ReleasePrepareError, match="verification failed"):
        _prepare(registry, control, layout, runtime, manifest)

    assert registry.get_release(manifest.release_id) is None
    assert registry.get_service_release_state("predictor").desired_release_id is None


def test_prepare_rejects_wrong_service_target_before_acquiring_lease():
    registry, control, layout, _asset, runtime, manifest = _inputs()

    with pytest.raises(ReleasePrepareConflict, match="service target"):
        _prepare(
            registry,
            control,
            layout,
            runtime,
            manifest,
            deployment_target_id="other-cluster",
        )

    assert registry.get_service_release_state("predictor") is None


class _ControlDriftVerifier:
    def __init__(self, registry, control):
        self.registry = registry
        self.control = control
        self.delegate = HmacTestVerifier(key_version=_RELEASE_KEY)

    def verify_sha256_digest(self, *, key_version, digest, signature):
        self.delegate.verify_sha256_digest(
            key_version=key_version, digest=digest, signature=signature
        )
        self.registry.control = replace(
            self.control, write_epoch=self.control.write_epoch + 1
        )


def test_control_epoch_drift_rolls_back_release_commit():
    registry, control, layout, _asset, runtime, manifest = _inputs()

    with pytest.raises(ReleasePrepareConflict, match="control/binding/write epoch"):
        _prepare(
            registry,
            control,
            layout,
            runtime,
            manifest,
            release_attestation_verifier=_ControlDriftVerifier(registry, control),
        )

    assert registry.get_release(manifest.release_id) is None
    assert registry.get_service_release_state("predictor").desired_release_id is None

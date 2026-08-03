from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_manifest import (
    ConfigReferenceKind,
    ImmutableConfigReference,
    ReleaseImageBinding,
    ReleaseAssetContract,
    ReleaseManifestCore,
    ReleaseManifestError,
    ReleaseWorkloadSecurity,
    RuntimeContract,
    SignedReleaseManifest,
    build_release_asset_binding,
    compute_release_id,
    sign_release_manifest,
    verify_release_manifest,
)
from aigear.management.v2.resolver import Selector, UsageContext, resolve
from tests.management.v2.test_resolver import (
    _FP,
    _NOW,
    _approve,
    _control_document,
    _layout,
    _produce_committed_output,
)

_RELEASE_KEY = "release-key-version-1"
_POLICY_KEY = "test-only"


def _workload_security(**overrides):
    values = dict(
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
    )
    values.update(overrides)
    return ReleaseWorkloadSecurity(**values)


@pytest.fixture
def release_inputs():
    registry = FakeRegistryV2()
    layout = _layout()
    outcome = _produce_committed_output(
        registry, FakeGcsClient(), layout, data=b"release-model"
    )
    asset = _approve(registry, outcome.asset_version)
    handle = resolve(
        registry,
        _control_document(),
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
            image_reference=f"us-docker.pkg.dev/proj/runtime/predictor@{image_digest.typed}",
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
            ImmutableConfigReference(
                kind=ConfigReferenceKind.SECRET_MANAGER,
                name="predictor-token",
                version="7",
                content_digest=TypedId.from_bare("08" * 32),
            ),
        ),
        runtime_contract=runtime,
        workload_security=_workload_security(),
        deployment_spec_digest=TypedId.from_bare("09" * 32),
    )
    return core, runtime, handle


def _sign(core):
    return sign_release_manifest(
        core,
        signer=HmacTestSigner(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=(_POLICY_KEY,),
    )


def test_release_manifest_is_content_addressed_signed_and_deployable(release_inputs):
    core, runtime, handle = release_inputs
    manifest = _sign(core)

    assert manifest.release_id == compute_release_id(core)
    assert verify_release_manifest(
        manifest,
        verifier=HmacTestVerifier(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=(_POLICY_KEY,),
        expected_environment_fingerprint=_FP,
        expected_service_name="predictor",
        expected_deployment_target_id="prod-cluster",
        expected_runtime_contract=runtime,
        resolved_assets={"model": handle},
        now=_NOW,
    ) == manifest

    record = manifest.to_release_record(
        creation_operation_id="release-op-1",
        display_version="service-v1",
        created_at="2026-07-24T00:00:00+00:00",
    )
    assert record.manifest_digest == record.release_id
    assert record.asset_version_ids == (handle.asset_version_id,)


def test_release_id_has_stable_cross_language_vector(release_inputs):
    core, _runtime, _handle = release_inputs
    assert compute_release_id(core).typed == (
        "sha256:19c6e4af0293415f98599dc628a192ff71ff428fff7fa9a020bff92d12554656"
    )


def test_release_rejects_mutable_image_and_unpinned_configuration(release_inputs):
    core, _runtime, _handle = release_inputs
    with pytest.raises(ReleaseManifestError, match="immutable digest"):
        replace(core.image, image_reference="us-docker.pkg.dev/proj/runtime/predictor:latest")
    with pytest.raises(ReleaseManifestError, match="positive integer"):
        ImmutableConfigReference(
            kind=ConfigReferenceKind.SECRET_MANAGER,
            name="predictor-token",
            version="latest",
            content_digest=TypedId.from_bare("08" * 32),
        )
    with pytest.raises(ReleaseManifestError, match="content digest"):
        replace(core.config_references[0], version="latest")
    with pytest.raises(ReleaseManifestError, match="TypedId"):
        replace(core.assets[0], asset_version_id="latest")


def test_release_assets_must_satisfy_declared_contract(release_inputs):
    core, runtime, _handle = release_inputs
    incompatible = replace(
        runtime.asset_contracts[0],
        runtime_contract_digest=TypedId.from_bare("99" * 32),
    )
    with pytest.raises(ReleaseManifestError, match="schema/runtime"):
        replace(
            core,
            runtime_contract=replace(runtime, asset_contracts=(incompatible,)),
        )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("run_as_non_root", False, "non-root"),
        ("read_only_root_filesystem", False, "read-only"),
        ("allow_privilege_escalation", True, "privilege escalation"),
        ("drop_capabilities", (), "drop all"),
        ("seccomp_profile", "Unconfined", "RuntimeDefault"),
        ("cpu_request", "", "non-empty"),
        ("liveness_probe_path", "livez", "absolute probe"),
    ),
)
def test_release_rejects_missing_or_weakened_workload_security(
    field_name, value, message
):
    with pytest.raises(ReleaseManifestError, match=message):
        _workload_security(**{field_name: value})


@pytest.mark.parametrize("model_format", ("pickle", "joblib", "cloudpickle"))
def test_external_pickle_compatibility_requires_gvisor_policy(
    release_inputs, model_format
):
    core, runtime, _handle = release_inputs
    with pytest.raises(ReleaseManifestError, match="gVisor sandbox policy"):
        replace(core, runtime_contract=replace(runtime, model_format=model_format))

    sandboxed = replace(
        core,
        runtime_contract=replace(runtime, model_format=model_format),
        workload_security=_workload_security(
            sandbox_runtime_class="gvisor",
            sandbox_policy_id="external-pickle-v1",
        ),
    )
    assert sandboxed.workload_security.sandbox_runtime_class == "gvisor"


def test_release_verification_rejects_contract_asset_and_expiry_drift(release_inputs):
    core, runtime, handle = release_inputs
    manifest = _sign(core)
    verifier = HmacTestVerifier(key_version=_RELEASE_KEY)
    kwargs = dict(
        verifier=verifier,
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=(_POLICY_KEY,),
        expected_environment_fingerprint=_FP,
        expected_service_name="predictor",
        expected_deployment_target_id="prod-cluster",
        expected_runtime_contract=runtime,
        resolved_assets={"model": handle},
        now=_NOW,
    )

    with pytest.raises(ReleaseManifestError, match="incompatible"):
        verify_release_manifest(
            manifest,
            **{**kwargs, "expected_runtime_contract": replace(runtime, abi_version="v2")},
        )
    with pytest.raises(ReleaseManifestError, match="binding set"):
        verify_release_manifest(manifest, **{**kwargs, "resolved_assets": {}})
    with pytest.raises(ReleaseManifestError, match="does not match"):
        verify_release_manifest(
            manifest,
            **{**kwargs, "resolved_assets": {"model": replace(handle, expires_at=_NOW)}},
        )


def test_release_key_must_be_allowlisted_and_independent(release_inputs):
    core, _runtime, _handle = release_inputs
    with pytest.raises(ReleaseManifestError, match="independent"):
        sign_release_manifest(
            core,
            signer=HmacTestSigner(key_version=_RELEASE_KEY),
            release_key_versions=(_RELEASE_KEY,),
            non_release_key_versions=(_RELEASE_KEY,),
        )
    with pytest.raises(ReleaseManifestError, match="not allowlisted"):
        sign_release_manifest(
            core,
            signer=HmacTestSigner(key_version="wrong-key"),
            release_key_versions=(_RELEASE_KEY,),
            non_release_key_versions=(_POLICY_KEY,),
        )


def test_release_core_and_attestation_cannot_be_mixed(release_inputs):
    core, _runtime, _handle = release_inputs
    manifest = _sign(core)
    changed = replace(core, deployment_spec_digest=TypedId.from_bare("10" * 32))
    with pytest.raises(ReleaseManifestError, match="release_id"):
        SignedReleaseManifest(
            core=changed,
            release_id=manifest.release_id,
            attestation=manifest.attestation,
        )

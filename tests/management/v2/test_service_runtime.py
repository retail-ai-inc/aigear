from __future__ import annotations

from dataclasses import replace
import pytest

from aigear.management.v2.attestation import (
    HmacTestSigner,
    HmacTestVerifier,
    verify_attestation,
)
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_deploy import deploy_release_candidate
from aigear.management.v2.release_kubernetes import (
    FakeKubernetesReleasePort,
    KubernetesConfigReference,
)
from aigear.management.v2.runtime_authorization import VerifiedJournalWatermark
from aigear.management.v2.service_runtime import (
    LoadedAssetObservation,
    PodStartupObservation,
    ServiceRuntimeError,
    require_service_runtime_readiness,
    verify_service_runtime_startup,
)
from tests.management.v2.test_release_prepare import (
    _RELEASE_KEY,
    _inputs,
    _prepare,
)
from tests.management.v2.test_resolver import _NOW


_RUNTIME_KEY = "runtime-key-version-1"


def _runtime_inputs():
    registry, control, layout, _asset, runtime, manifest = _inputs()
    prepared = _prepare(registry, control, layout, runtime, manifest)
    port = FakeKubernetesReleasePort()
    port.seed_service("predictor")
    deployed = deploy_release_candidate(
        registry,
        port,
        manifest=manifest,
        operation_id=prepared.operation.operation_id,
        owner_principal=prepared.operation.owner_principal,
        fencing_token=prepared.operation.fencing_token,
        service_account_name="service-runtime-sa",
        allowed_service_accounts=("service-runtime-sa",),
        replicas=1,
    )
    observation = PodStartupObservation(
        pod_uid=f"{deployed.deployment.uid}-pod-1",
        pod_resource_version="pod-rv-1",
        deployment_uid=deployed.deployment.uid,
        deployment_resource_version=deployed.deployment.resource_version,
        release_id=manifest.release_id,
        image_digest=manifest.core.image.image_digest,
        deployment_spec_digest=manifest.core.deployment_spec_digest,
        loaded_assets=tuple(
            LoadedAssetObservation(
                binding_name=value.binding_name,
                asset_version_id=value.asset_version_id,
                schema_contract_digest=value.schema_contract_digest,
                runtime_contract_digest=value.runtime_contract_digest,
                blobs=value.blobs,
            )
            for value in manifest.core.assets
        ),
        config_references=tuple(
            KubernetesConfigReference(
                kind=value.kind.value,
                name=value.name,
                version=value.version,
                content_digest=value.content_digest,
            )
            for value in manifest.core.config_references
        ),
        runtime_contract=runtime,
        fencing_token=deployed.operation.fencing_token,
    )
    journal = VerifiedJournalWatermark(
        security_watermark=0,
        head_entry_id=TypedId.from_bare("fa" * 32),
        verified_at=_NOW,
        fresh_for_seconds=60,
    )
    return registry, control, layout, runtime, manifest, deployed, observation, journal


def _startup(
    registry, control, layout, runtime, manifest, deployed, observation, journal
):
    return verify_service_runtime_startup(
        registry,
        control=control,
        layout=layout,
        manifest=manifest,
        observation=observation,
        operation_id=deployed.operation.operation_id,
        owner_principal=deployed.operation.owner_principal,
        expected_runtime_contract=runtime,
        asset_attestation_verifier=HmacTestVerifier(),
        release_attestation_verifier=HmacTestVerifier(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        journal=journal,
        lease_issuer_principal="controller@example.test",
        evidence_signer=HmacTestSigner(key_version=_RUNTIME_KEY),
        runtime_key_versions=(_RUNTIME_KEY,),
        non_runtime_key_versions=(_RELEASE_KEY, "test-only"),
    )


def test_exact_startup_issues_lease_and_signed_observed_evidence():
    values = _runtime_inputs()
    result = _startup(*values)

    assert result.observed_evidence.release_id == values[4].release_id
    assert result.observed_evidence.pod_uid == values[6].pod_uid
    assert result.observed_attestation.unsigned_envelope["subject"][
        "runtime_lease_id"
    ] == result.lease.lease_id.typed
    verify_attestation(
        result.observed_attestation,
        HmacTestVerifier(key_version=_RUNTIME_KEY),
    )
    assert require_service_runtime_readiness(
        values[0], result, at=_NOW, renewal_margin_seconds=0
    ) == result


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("loaded_assets", (), "asset bundle"),
        ("config_references", (), "config or secret"),
        ("image_digest", TypedId.from_bare("ee" * 32), "digest drifted"),
    ],
)
def test_partial_or_drifted_runtime_inputs_never_become_ready(field, value, error):
    values = _runtime_inputs()
    observation = replace(values[6], **{field: value})

    with pytest.raises(ServiceRuntimeError, match=error):
        _startup(*values[:6], observation, values[7])


def test_policy_expiry_blocks_startup_lease_and_readiness():
    values = _runtime_inputs()
    registry = values[0]
    asset_id = values[4].core.assets[0].asset_version_id
    head = registry.get_policy_decision_head(asset_id)
    registry.put_policy_decision_head(
        replace(head, revision=head.revision + 1, valid_until=_NOW.isoformat())
    )

    with pytest.raises(ServiceRuntimeError, match="lease was not issued"):
        _startup(*values)


def test_missing_authoritative_lease_fails_readiness_closed():
    values = _runtime_inputs()
    result = _startup(*values)
    values[0]._runtime_authorization_leases.clear()

    with pytest.raises(ServiceRuntimeError, match="lease is missing"):
        require_service_runtime_readiness(
            values[0], result, at=_NOW, renewal_margin_seconds=0
        )


def test_wrong_runtime_signing_domain_key_is_rejected():
    values = _runtime_inputs()
    with pytest.raises(ServiceRuntimeError, match="independent"):
        verify_service_runtime_startup(
            values[0],
            control=values[1],
            layout=values[2],
            manifest=values[4],
            observation=values[6],
            operation_id=values[5].operation.operation_id,
            owner_principal=values[5].operation.owner_principal,
            expected_runtime_contract=values[3],
            asset_attestation_verifier=HmacTestVerifier(),
            release_attestation_verifier=HmacTestVerifier(key_version=_RELEASE_KEY),
            release_key_versions=(_RELEASE_KEY,),
            non_release_key_versions=("test-only",),
            journal=values[7],
            lease_issuer_principal="controller@example.test",
            evidence_signer=HmacTestSigner(key_version=_RELEASE_KEY),
            runtime_key_versions=(_RELEASE_KEY,),
            non_runtime_key_versions=(_RELEASE_KEY,),
        )

from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.release_deploy import (
    CandidateDeploymentConflict,
    CandidateDeploymentError,
    CandidateDeploymentUncertain,
    candidate_deployment_name,
    deploy_release_candidate,
)
from aigear.management.v2.release_kubernetes import (
    DeploymentCreateRequest,
    FakeKubernetesReleasePort,
    MutationFault,
)
from tests.management.v2.test_release_prepare import _inputs, _prepare


def _prepared():
    registry, control, layout, _asset, runtime, manifest = _inputs()
    prepared = _prepare(registry, control, layout, runtime, manifest)
    port = FakeKubernetesReleasePort()
    port.seed_service("predictor")
    return registry, manifest, prepared, port


def _deploy(registry, manifest, prepared, port, **overrides):
    values = dict(
        manifest=manifest,
        operation_id=prepared.operation.operation_id,
        owner_principal=prepared.operation.owner_principal,
        fencing_token=prepared.operation.fencing_token,
        service_account_name="service-runtime-sa",
        allowed_service_accounts=("service-runtime-sa",),
        replicas=2,
    )
    values.update(overrides)
    return deploy_release_candidate(registry, port, **values)


def test_candidate_is_isolated_and_binds_all_runtime_inputs():
    registry, manifest, prepared, port = _prepared()
    stable_before = port.get_service("predictor")

    result = _deploy(registry, manifest, prepared, port)

    assert result.operation.phase.value == "deploying"
    assert result.deployment.request.name == candidate_deployment_name(manifest)
    assert manifest.release_id.bare in result.deployment.request.name
    assert result.deployment.request.release_id == manifest.release_id
    assert result.deployment.request.image_reference == manifest.core.image.image_reference
    assert result.deployment.request.config_references[0].version == manifest.core.config_references[0].version
    assert result.deployment.request.runtime_authorization_required is True
    assert result.deployment.request.service_account_name == "service-runtime-sa"
    assert result.deployment.request.workload_security == manifest.core.workload_security
    assert port.get_service("predictor") == stable_before


def test_ack_loss_is_recovered_by_exact_deployment_read():
    registry, manifest, prepared, port = _prepared()
    port.queue_fault("create_deployment", MutationFault.ACK_LOST_AFTER_COMMIT)

    result = _deploy(registry, manifest, prepared, port)

    assert result.operation.expected_deployment_uid == result.deployment.uid
    assert port.get_deployment(candidate_deployment_name(manifest)) == result.deployment


def test_timeout_without_observed_candidate_enters_reconciling():
    registry, manifest, prepared, port = _prepared()
    port.queue_fault("create_deployment", MutationFault.TIMEOUT_BEFORE_COMMIT)

    with pytest.raises(CandidateDeploymentUncertain, match="reconciliation"):
        _deploy(registry, manifest, prepared, port)

    operation = registry.get_release_operation(prepared.operation.operation_id)
    state = registry.get_service_release_state("predictor")
    assert operation.phase.value == "reconciling"
    assert state.active_operation_phase.value == "reconciling"
    assert state.traffic_release_id is None


def test_existing_same_name_with_different_spec_is_never_overwritten():
    registry, manifest, prepared, port = _prepared()
    request = DeploymentCreateRequest(
        name=candidate_deployment_name(manifest),
        service_name="predictor",
        release_id=manifest.release_id,
        image_reference=manifest.core.image.image_reference,
        manifest_digest=manifest.release_id,
        deployment_spec_digest=manifest.core.deployment_spec_digest,
        service_account_name="service-runtime-sa",
        config_references=(),
        workload_security=manifest.core.workload_security,
        runtime_authorization_required=True,
        replicas=3,
        fencing_token=prepared.operation.fencing_token,
    )
    existing = port.create_deployment(request)

    with pytest.raises(CandidateDeploymentConflict, match="different immutable"):
        _deploy(registry, manifest, prepared, port)

    assert port.get_deployment(request.name) == existing
    assert registry.get_release_operation(
        prepared.operation.operation_id
    ).phase.value == "reconciling"


def test_retry_reuses_exact_candidate_and_old_revision_remains_ready():
    registry, manifest, prepared, port = _prepared()
    old_request = replace(
        DeploymentCreateRequest(
            name="release-" + "ab" * 32,
            service_name="predictor",
            release_id=type(manifest.release_id).from_bare("ab" * 32),
            image_reference="repo/predictor@sha256:" + "ab" * 32,
            manifest_digest=type(manifest.release_id).from_bare("ab" * 32),
            deployment_spec_digest=type(manifest.release_id).from_bare("ac" * 32),
            service_account_name="service-runtime-sa",
            config_references=(),
            workload_security=manifest.core.workload_security,
            runtime_authorization_required=True,
            replicas=1,
            fencing_token=1,
        )
    )
    old = port.create_deployment(old_request)
    first = _deploy(registry, manifest, prepared, port)
    second = _deploy(registry, manifest, first, port)

    assert second.deployment == first.deployment
    assert port.get_deployment(old_request.name) == old
    assert old.available_replicas == 1


def test_non_allowlisted_service_account_fails_before_kubernetes_create():
    registry, manifest, prepared, port = _prepared()
    with pytest.raises(CandidateDeploymentError, match="not allowlisted"):
        _deploy(
            registry,
            manifest,
            prepared,
            port,
            service_account_name="default",
        )
    assert port.get_deployment(candidate_deployment_name(manifest)) is None

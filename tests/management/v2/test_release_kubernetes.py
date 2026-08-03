from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_kubernetes import (
    DeploymentCreateRequest,
    FakeKubernetesReleasePort,
    KubernetesMutationUncertain,
    KubernetesConfigReference,
    KubernetesResourceConflict,
    MutationFault,
    ProbeRequest,
    ProbeResult,
    ServiceTrafficPatch,
    compute_probe_evidence_digest,
)


_RELEASE = TypedId.from_bare("aa" * 32)


def _deployment(**overrides):
    values = dict(
        name="predictor-v1",
        service_name="predictor",
        release_id=_RELEASE,
        image_reference=f"repo/predictor@{_RELEASE.typed}",
        manifest_digest=_RELEASE,
        deployment_spec_digest=TypedId.from_bare("bb" * 32),
        service_account_name="service-runtime-sa",
        config_references=(
            KubernetesConfigReference(
                kind="secret_manager",
                name="predictor-token",
                version="7",
                content_digest=TypedId.from_bare("cc" * 32),
            ),
        ),
        startup_probe_path="/startupz",
        readiness_probe_path="/readyz",
        runtime_authorization_required=True,
        replicas=2,
        fencing_token=1,
    )
    values.update(overrides)
    return DeploymentCreateRequest(**values)


def _patch(service, **overrides):
    values = dict(
        service_name=service.service_name,
        expected_uid=service.uid,
        expected_resource_version=service.resource_version,
        target_release_id=_RELEASE,
        fencing_token=1,
    )
    values.update(overrides)
    return ServiceTrafficPatch(**values)


def test_create_deployment_is_exact_and_idempotent():
    port = FakeKubernetesReleasePort()
    first = port.create_deployment(_deployment())

    assert port.create_deployment(_deployment()) == first
    assert port.get_deployment("predictor-v1") == first
    with pytest.raises(KubernetesResourceConflict, match="immutable"):
        port.create_deployment(_deployment(replicas=3))


def test_service_patch_requires_uid_resource_version_and_current_fence():
    port = FakeKubernetesReleasePort()
    port.create_deployment(_deployment())
    service = port.seed_service("predictor")
    updated = port.patch_service_traffic(_patch(service, fencing_token=2))

    with pytest.raises(KubernetesResourceConflict, match="resourceVersion"):
        port.patch_service_traffic(_patch(service, fencing_token=2))
    with pytest.raises(KubernetesResourceConflict, match="fencing"):
        port.patch_service_traffic(_patch(updated, fencing_token=1))


@pytest.mark.parametrize(
    "fault,committed",
    [
        (MutationFault.TIMEOUT_BEFORE_COMMIT, False),
        (MutationFault.TIMEOUT_AFTER_COMMIT, True),
        (MutationFault.ACK_LOST_AFTER_COMMIT, True),
    ],
)
def test_uncertain_create_requires_exact_read_to_determine_result(fault, committed):
    port = FakeKubernetesReleasePort()
    port.queue_fault("create_deployment", fault)

    with pytest.raises(KubernetesMutationUncertain):
        port.create_deployment(_deployment())

    assert (port.get_deployment("predictor-v1") is not None) is committed


def test_uncertain_service_patch_can_be_determined_by_resource_read():
    port = FakeKubernetesReleasePort()
    port.create_deployment(_deployment())
    service = port.seed_service("predictor")
    port.queue_fault("patch_service_traffic", MutationFault.ACK_LOST_AFTER_COMMIT)

    with pytest.raises(KubernetesMutationUncertain):
        port.patch_service_traffic(_patch(service))

    observed = port.get_service("predictor")
    assert observed.traffic_release_id == _RELEASE
    assert observed.resource_version != service.resource_version


def test_endpoint_slice_converges_after_configured_read_delay():
    port = FakeKubernetesReleasePort()
    port.create_deployment(_deployment())
    service = port.seed_service("predictor")
    port.patch_service_traffic(_patch(service))
    port.configure_endpoint_delay(_RELEASE, reads=2)

    assert port.get_endpoint_slice("predictor").endpoints == ()
    assert port.get_endpoint_slice("predictor").endpoints == ()
    endpoints = port.get_endpoint_slice("predictor").endpoints
    assert len(endpoints) == 2
    assert all(endpoint.release_id == _RELEASE and endpoint.ready for endpoint in endpoints)


def test_probe_is_explicitly_configured_per_pod():
    port = FakeKubernetesReleasePort()
    image_digest = TypedId.from_bare("bb" * 32)
    asset_version_ids = (TypedId.from_bare("cc" * 32),)
    runtime_contract_digest = TypedId.from_bare("dd" * 32)
    result = ProbeResult(
        passed=True,
        release_id=_RELEASE,
        image_digest=image_digest,
        asset_version_ids=asset_version_ids,
        runtime_contract_digest=runtime_contract_digest,
        evidence_digest=compute_probe_evidence_digest(
            passed=True,
            release_id=_RELEASE,
            image_digest=image_digest,
            asset_version_ids=asset_version_ids,
            runtime_contract_digest=runtime_contract_digest,
        ),
        summary="ok",
    )
    port.set_probe_result("pod-1", result)

    assert port.probe(
        ProbeRequest(
            service_name="predictor",
            release_id=_RELEASE,
            pod_uid="pod-1",
            fencing_token=1,
        )
    ) == result


def test_probe_result_rejects_an_unbound_evidence_digest():
    with pytest.raises(ValueError, match="exact probe response"):
        ProbeResult(
            passed=True,
            release_id=_RELEASE,
            image_digest=TypedId.from_bare("bb" * 32),
            asset_version_ids=(TypedId.from_bare("cc" * 32),),
            runtime_contract_digest=TypedId.from_bare("dd" * 32),
            evidence_digest=TypedId.from_bare("ee" * 32),
        )


def test_drain_reports_deterministic_long_connection_progress():
    port = FakeKubernetesReleasePort()
    deployment = port.create_deployment(_deployment())
    port.set_active_connections(
        deployment.uid,
        count=5,
        close_per_drain=2,
    )

    results = [port.drain(deployment.uid) for _ in range(3)]
    assert [result.active_connections for result in results] == [3, 1, 0]
    assert [result.complete for result in results] == [False, False, True]


def test_deployment_request_rejects_mutable_image_reference():
    with pytest.raises(ValueError, match="digest pinned"):
        replace(_deployment(), image_reference="repo/predictor:latest")


def test_deployment_request_requires_runtime_authorization_and_sorted_refs():
    with pytest.raises(ValueError, match="authorization"):
        replace(_deployment(), runtime_authorization_required=False)
    with pytest.raises(ValueError, match="sorted"):
        replace(
            _deployment(),
            config_references=(
                KubernetesConfigReference(
                    kind="secret_manager",
                    name="z-secret",
                    version="1",
                    content_digest=TypedId.from_bare("dd" * 32),
                ),
                _deployment().config_references[0],
            ),
        )

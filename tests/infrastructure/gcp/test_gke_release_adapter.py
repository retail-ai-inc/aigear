from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from aigear.infrastructure.gcp.kubernetes import GkeKubernetesReleaseAdapter
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_kubernetes import (
    DeploymentCreateRequest,
    KubernetesConfigReference,
    KubernetesMutationUncertain,
    KubernetesResourceConflict,
    ServiceTrafficPatch,
)
from tests.management.v2.test_release_manifest import _workload_security


_RELEASE = TypedId.from_bare("aa" * 32)
_SPEC = TypedId.from_bare("bb" * 32)


class Models:
    def __getattr__(self, _name):
        return lambda **values: SimpleNamespace(**values)


class ApiError(Exception):
    def __init__(self, status):
        super().__init__(str(status))
        self.status = status


def _deployment_resource(request, *, uid="uid-1", resource_version="11"):
    labels = {
        "app.kubernetes.io/name": request.service_name,
        "aigear.openai.com/release-id": request.release_id.bare,
    }
    annotations = {
        "aigear.openai.com/fencing-token": str(request.fencing_token),
        "aigear.openai.com/manifest-digest": request.manifest_digest.typed,
        "aigear.openai.com/deployment-spec-digest": request.deployment_spec_digest.typed,
        "aigear.openai.com/config-references": json.dumps(
            [value.to_dict() for value in request.config_references],
            sort_keys=True,
            separators=(",", ":"),
        ),
        "aigear.openai.com/runtime-authorization-required": "true",
    }
    if request.workload_security.sandbox_policy_id is not None:
        annotations["aigear.openai.com/sandbox-policy-id"] = (
            request.workload_security.sandbox_policy_id
        )
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=request.name,
            uid=uid,
            resource_version=resource_version,
            labels=labels,
            annotations=annotations,
        ),
        spec=SimpleNamespace(
            replicas=request.replicas,
            template=SimpleNamespace(
                spec=SimpleNamespace(
                    service_account_name=request.service_account_name,
                    runtime_class_name=(
                        request.workload_security.sandbox_runtime_class
                    ),
                    security_context=SimpleNamespace(
                        run_as_non_root=True,
                        seccomp_profile=SimpleNamespace(type="RuntimeDefault"),
                    ),
                    containers=[
                        SimpleNamespace(
                            image=request.image_reference,
                            security_context=SimpleNamespace(
                                run_as_non_root=True,
                                read_only_root_filesystem=True,
                                allow_privilege_escalation=False,
                                capabilities=SimpleNamespace(drop=["ALL"]),
                            ),
                            resources=SimpleNamespace(
                                requests={"cpu": "250m", "memory": "256Mi"},
                                limits={"cpu": "1", "memory": "1Gi"},
                            ),
                            startup_probe=SimpleNamespace(
                                http_get=SimpleNamespace(
                                    path=request.workload_security.startup_probe_path
                                )
                            ),
                            readiness_probe=SimpleNamespace(
                                http_get=SimpleNamespace(
                                    path=request.workload_security.readiness_probe_path
                                )
                            ),
                            liveness_probe=SimpleNamespace(
                                http_get=SimpleNamespace(path="/livez")
                            ),
                        )
                    ],
                )
            ),
        ),
        status=SimpleNamespace(available_replicas=request.replicas),
    )


def _service_resource(*, release_id=None, fence=0, resource_version="21"):
    selector = {}
    if release_id is not None:
        selector["aigear.openai.com/release-id"] = release_id.bare
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name="predictor",
            uid="service-uid",
            resource_version=resource_version,
            labels={},
            annotations={"aigear.openai.com/fencing-token": str(fence)},
        ),
        spec=SimpleNamespace(selector=selector),
    )


def _request():
    return DeploymentCreateRequest(
        name="predictor-v1",
        service_name="predictor",
        release_id=_RELEASE,
        image_reference=f"repo/predictor@{_RELEASE.typed}",
        manifest_digest=_RELEASE,
        deployment_spec_digest=_SPEC,
        service_account_name="service-runtime-sa",
        config_references=(
            KubernetesConfigReference(
                kind="secret_manager",
                name="predictor-token",
                version="7",
                content_digest=TypedId.from_bare("cc" * 32),
            ),
        ),
        workload_security=_workload_security(),
        runtime_authorization_required=True,
        replicas=2,
        fencing_token=3,
    )


class AppsApi:
    def __init__(self, resource=None, error=None):
        self.resource = resource
        self.error = error
        self.created_body = None

    def read_namespaced_deployment(self, **_kwargs):
        if self.error:
            raise self.error
        return self.resource

    def create_namespaced_deployment(self, *, body, **_kwargs):
        self.created_body = body
        if self.error:
            raise self.error
        return self.resource


class CoreApi:
    def __init__(self, current, patched=None, error=None):
        self.current = current
        self.patched = patched
        self.error = error
        self.patch_body = None

    def read_namespaced_service(self, **_kwargs):
        return self.current

    def patch_namespaced_service(self, *, body, **_kwargs):
        self.patch_body = body
        if self.error:
            raise self.error
        return self.patched


class DiscoveryApi:
    def __init__(self, result=None, error=None):
        self.result = result or SimpleNamespace(
            metadata=SimpleNamespace(resource_version="31"), items=[]
        )
        self.error = error

    def list_namespaced_endpoint_slice(self, **_kwargs):
        if self.error:
            raise self.error
        return self.result


def _adapter(*, apps=None, core=None, discovery=None):
    return GkeKubernetesReleaseAdapter(
        "aigear",
        apps_api=apps or AppsApi(),
        core_api=core or CoreApi(_service_resource()),
        discovery_api=discovery or DiscoveryApi(),
        models=Models(),
    )


def test_create_uses_structured_deployment_with_release_and_fence():
    request = _request()
    apps = AppsApi(_deployment_resource(request))
    state = _adapter(apps=apps).create_deployment(request)

    assert state.request == request
    assert apps.created_body.kind == "Deployment"
    assert apps.created_body.metadata.annotations[
        "aigear.openai.com/fencing-token"
    ] == "3"
    assert apps.created_body.spec.template.spec.containers[0].image == request.image_reference
    assert apps.created_body.spec.template.spec.service_account_name == "service-runtime-sa"
    container = apps.created_body.spec.template.spec.containers[0]
    assert container.startup_probe.http_get.path == "/startupz"
    assert container.liveness_probe.http_get.path == "/livez"
    assert container.security_context.read_only_root_filesystem is True
    assert container.security_context.allow_privilege_escalation is False
    assert container.security_context.capabilities.drop == ["ALL"]
    assert container.resources.requests == {"cpu": "250m", "memory": "256Mi"}
    assert container.resources.limits == {"cpu": "1", "memory": "1Gi"}
    pod_spec = apps.created_body.spec.template.spec
    assert pod_spec.security_context.run_as_non_root is True
    assert pod_spec.security_context.seccomp_profile.type == "RuntimeDefault"


def test_create_binds_external_pickle_sandbox_to_pod():
    request = _request()
    request = replace(
        request,
        workload_security=_workload_security(
            sandbox_runtime_class="gvisor",
            sandbox_policy_id="external-pickle-v1",
        ),
    )
    apps = AppsApi(_deployment_resource(request))

    state = _adapter(apps=apps).create_deployment(request)

    assert state.request == request
    pod = apps.created_body.spec.template
    assert pod.spec.runtime_class_name == "gvisor"
    assert pod.metadata.annotations[
        "aigear.openai.com/sandbox-policy-id"
    ] == "external-pickle-v1"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_create_status_is_uncertain(status):
    with pytest.raises(KubernetesMutationUncertain, match="exact-read"):
        _adapter(apps=AppsApi(error=ApiError(status))).create_deployment(_request())


def test_create_409_is_a_closed_resource_conflict():
    with pytest.raises(KubernetesResourceConflict):
        _adapter(apps=AppsApi(error=ApiError(409))).create_deployment(_request())


def test_create_timeout_is_uncertain_and_requires_readback():
    with pytest.raises(KubernetesMutationUncertain, match="exact-read"):
        _adapter(apps=AppsApi(error=TimeoutError())).create_deployment(_request())


def test_get_404_returns_none_but_other_read_failures_are_closed():
    assert _adapter(apps=AppsApi(error=ApiError(404))).get_deployment("missing") is None
    with pytest.raises(ValueError, match="read failed"):
        _adapter(apps=AppsApi(error=ApiError(503))).get_deployment("predictor-v1")


def test_service_patch_sends_uid_resource_version_and_fence():
    current = _service_resource()
    patched = _service_resource(release_id=_RELEASE, fence=4, resource_version="22")
    core = CoreApi(current, patched=patched)
    result = _adapter(core=core).patch_service_traffic(
        ServiceTrafficPatch(
            service_name="predictor",
            expected_uid="service-uid",
            expected_resource_version="21",
            target_release_id=_RELEASE,
            fencing_token=4,
        )
    )

    assert result == _adapter(core=CoreApi(patched)).get_service("predictor")
    assert core.patch_body.metadata.uid == "service-uid"
    assert core.patch_body.metadata.resource_version == "21"
    assert core.patch_body.metadata.annotations[
        "aigear.openai.com/fencing-token"
    ] == "4"


def test_service_patch_rejects_stale_observed_identity_before_mutation():
    core = CoreApi(_service_resource(resource_version="22"))
    with pytest.raises(KubernetesResourceConflict, match="UID"):
        _adapter(core=core).patch_service_traffic(
            ServiceTrafficPatch(
                service_name="predictor",
                expected_uid="service-uid",
                expected_resource_version="21",
                target_release_id=_RELEASE,
                fencing_token=4,
            )
        )
    assert core.patch_body is None


def test_endpoint_slice_maps_sorted_pod_uids_and_release_identity():
    item = SimpleNamespace(
        metadata=SimpleNamespace(
            labels={"aigear.openai.com/release-id": _RELEASE.bare},
            annotations={},
        ),
        endpoints=[
            SimpleNamespace(
                target_ref=SimpleNamespace(uid="pod-b"),
                conditions=SimpleNamespace(ready=False),
            ),
            SimpleNamespace(
                target_ref=SimpleNamespace(uid="pod-a"),
                conditions=SimpleNamespace(ready=True),
            ),
        ],
    )
    discovery = DiscoveryApi(
        SimpleNamespace(
            metadata=SimpleNamespace(resource_version="31"), items=[item]
        )
    )

    snapshot = _adapter(discovery=discovery).get_endpoint_slice("predictor")
    assert [endpoint.pod_uid for endpoint in snapshot.endpoints] == ["pod-a", "pod-b"]
    assert snapshot.endpoints[0].release_id == _RELEASE


def test_endpoint_slice_disconnect_fails_closed():
    with pytest.raises(ValueError, match="read failed"):
        _adapter(discovery=DiscoveryApi(error=ConnectionError())).get_endpoint_slice(
            "predictor"
        )

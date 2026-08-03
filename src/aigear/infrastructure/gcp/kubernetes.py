from __future__ import annotations

import json
from typing import Any, Callable, Optional

from aigear.common import run_sh
from aigear.common.logger import Logging
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_kubernetes import (
    DeploymentCreateRequest,
    DeploymentState,
    DrainResult,
    EndpointSliceState,
    EndpointState,
    KubernetesConfigReference,
    KubernetesMutationUncertain,
    KubernetesReleaseError,
    KubernetesResourceConflict,
    ProbeRequest,
    ProbeResult,
    ServiceTrafficPatch,
    StableServiceState,
)

logger = Logging(log_name=__name__).console_logging()


class KubernetesCluster:
    def __init__(self, cluster_name, zone, num_nodes, min_nodes, max_nodes, project_id):
        self.cluster_name = cluster_name
        self.zone = zone
        self.num_nodes = num_nodes
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        self.project_id = project_id

    def create(self):
        command = [
            "gcloud",
            "container",
            "clusters",
            "create",
            self.cluster_name,
            f"--region={self.zone}",
            f"--node-locations={self.zone}-a",
            "--enable-autoscaling",
            f"--num-nodes={self.num_nodes}",
            f"--min-nodes={self.min_nodes}",
            f"--max-nodes={self.max_nodes}",
            f"--project={self.project_id}",
            "--async",
            "--quiet",
        ]
        run_sh(command, check=True)

    def describe(self):
        is_exist = False
        command = [
            "gcloud",
            "container",
            "clusters",
            "describe",
            self.cluster_name,
            f"--zone={self.zone}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "ERROR" not in event:
            is_exist = True
        elif "Not found" not in event and "NOT_FOUND" not in event:
            logger.error(
                f"Unexpected error describing cluster ({self.cluster_name}): {event}"
            )
        return is_exist

    def delete(self):
        command = [
            "gcloud",
            "container",
            "clusters",
            "delete",
            self.cluster_name,
            f"--location={self.zone}",
            f"--project={self.project_id}",
            "--async",
            "--quiet",
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(
                f"Error occurred while deleting GKE cluster ({self.cluster_name}): {event}"
            )
        else:
            logger.info(
                f"GKE cluster '{self.cluster_name}' deletion initiated (async)."
            )

    def update(self):
        command_autoscaling = [
            "gcloud",
            "container",
            "clusters",
            "update",
            self.cluster_name,
            "--enable-autoscaling",
            f"--min-nodes={self.min_nodes}",
            f"--max-nodes={self.max_nodes}",
            f"--region={self.zone}",
            "--node-pool=default-pool",
            f"--project={self.project_id}",
            "--quiet",
        ]
        run_sh(command_autoscaling, check=True, timeout=600)

        command_resize = [
            "gcloud",
            "container",
            "clusters",
            "resize",
            self.cluster_name,
            f"--num-nodes={self.num_nodes}",
            f"--region={self.zone}",
            "--node-pool=default-pool",
            f"--project={self.project_id}",
            "--async",
            "--quiet",
        ]
        run_sh(command_resize, check=True)


_RELEASE_LABEL = "aigear.openai.com/release-id"
_FENCE_ANNOTATION = "aigear.openai.com/fencing-token"
_MANIFEST_ANNOTATION = "aigear.openai.com/manifest-digest"
_SPEC_ANNOTATION = "aigear.openai.com/deployment-spec-digest"
_CONFIG_REFS_ANNOTATION = "aigear.openai.com/config-references"
_RUNTIME_AUTH_ANNOTATION = "aigear.openai.com/runtime-authorization-required"
_RUNTIME_PROBE_PORT = 8081


class GkeKubernetesReleaseAdapter:
    """Kubernetes-client implementation of the Pipeline V2 release port."""

    def __init__(
        self,
        namespace: str,
        *,
        apps_api: Any = None,
        core_api: Any = None,
        discovery_api: Any = None,
        models: Any = None,
        probe_client: Optional[Callable[[ProbeRequest], ProbeResult]] = None,
        drain_client: Optional[Callable[[str], DrainResult]] = None,
        load_incluster_config: bool = True,
    ) -> None:
        if not isinstance(namespace, str) or not namespace:
            raise KubernetesReleaseError("namespace must be a non-empty str")
        if any(value is None for value in (apps_api, core_api, discovery_api, models)):
            try:
                from kubernetes import client, config
            except ImportError as exc:  # pragma: no cover - optional GCP dependency
                raise KubernetesReleaseError(
                    "GkeKubernetesReleaseAdapter requires the kubernetes package"
                ) from exc
            if load_incluster_config:
                config.load_incluster_config()
            else:
                config.load_kube_config()
            apps_api = apps_api or client.AppsV1Api()
            core_api = core_api or client.CoreV1Api()
            discovery_api = discovery_api or client.DiscoveryV1Api()
            models = models or client
        self.namespace = namespace
        self.apps_api = apps_api
        self.core_api = core_api
        self.discovery_api = discovery_api
        self.models = models
        self.probe_client = probe_client
        self.drain_client = drain_client

    @staticmethod
    def _status(exc: Exception) -> Optional[int]:
        status = getattr(exc, "status", None)
        return status if isinstance(status, int) else None

    @classmethod
    def _read_error(cls, exc: Exception) -> None:
        raise KubernetesReleaseError("Kubernetes API read failed") from exc

    @classmethod
    def _mutation_error(cls, exc: Exception) -> None:
        status = cls._status(exc)
        if status == 409:
            raise KubernetesResourceConflict("Kubernetes resource conflict") from exc
        if isinstance(exc, TimeoutError) or status == 429 or (
            status is not None and status >= 500
        ):
            raise KubernetesMutationUncertain(
                "Kubernetes mutation result is uncertain; exact-read required"
            ) from exc
        raise KubernetesReleaseError("Kubernetes mutation failed") from exc

    @staticmethod
    def _metadata(resource: Any) -> Any:
        metadata = getattr(resource, "metadata", None)
        if metadata is None:
            raise KubernetesReleaseError("Kubernetes response is missing metadata")
        return metadata

    @staticmethod
    def _annotations(metadata: Any) -> dict:
        return dict(getattr(metadata, "annotations", None) or {})

    @staticmethod
    def _labels(metadata: Any) -> dict:
        return dict(getattr(metadata, "labels", None) or {})

    def _deployment_state(self, resource: Any) -> DeploymentState:
        metadata = self._metadata(resource)
        annotations = self._annotations(metadata)
        labels = self._labels(metadata)
        try:
            release_id = TypedId.from_bare(labels[_RELEASE_LABEL])
            fencing_token = int(annotations[_FENCE_ANNOTATION])
            manifest_digest = TypedId.from_typed(annotations[_MANIFEST_ANNOTATION])
            deployment_spec_digest = TypedId.from_typed(
                annotations[_SPEC_ANNOTATION]
            )
            containers = resource.spec.template.spec.containers
            pod_spec = resource.spec.template.spec
            replicas = int(resource.spec.replicas)
            config_references = tuple(
                KubernetesConfigReference(
                    kind=value["kind"],
                    name=value["name"],
                    version=value["version"],
                    content_digest=TypedId.from_typed(value["content_digest"]),
                )
                for value in json.loads(annotations[_CONFIG_REFS_ANNOTATION])
            )
            request = DeploymentCreateRequest(
                name=metadata.name,
                service_name=labels["app.kubernetes.io/name"],
                release_id=release_id,
                image_reference=containers[0].image,
                manifest_digest=manifest_digest,
                deployment_spec_digest=deployment_spec_digest,
                service_account_name=pod_spec.service_account_name,
                config_references=config_references,
                startup_probe_path=containers[0].startup_probe.http_get.path,
                readiness_probe_path=containers[0].readiness_probe.http_get.path,
                runtime_authorization_required=(
                    annotations[_RUNTIME_AUTH_ANNOTATION] == "true"
                ),
                replicas=replicas,
                fencing_token=fencing_token,
            )
            available = int(getattr(resource.status, "available_replicas", 0) or 0)
            return DeploymentState(
                request=request,
                uid=metadata.uid,
                resource_version=metadata.resource_version,
                available_replicas=available,
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise KubernetesReleaseError(
                "Deployment response violates the release contract"
            ) from exc

    def get_deployment(self, name: str) -> Optional[DeploymentState]:
        try:
            resource = self.apps_api.read_namespaced_deployment(
                name=name, namespace=self.namespace
            )
        except Exception as exc:
            if self._status(exc) == 404:
                return None
            self._read_error(exc)
        return self._deployment_state(resource)

    def _deployment_body(self, request: DeploymentCreateRequest) -> Any:
        labels = {
            "app.kubernetes.io/name": request.service_name,
            _RELEASE_LABEL: request.release_id.bare,
        }
        annotations = {
            _FENCE_ANNOTATION: str(request.fencing_token),
            _MANIFEST_ANNOTATION: request.manifest_digest.typed,
            _SPEC_ANNOTATION: request.deployment_spec_digest.typed,
            _CONFIG_REFS_ANNOTATION: json.dumps(
                [value.to_dict() for value in request.config_references],
                sort_keys=True,
                separators=(",", ":"),
            ),
            _RUNTIME_AUTH_ANNOTATION: "true",
        }
        metadata = self.models.V1ObjectMeta(
            name=request.name, labels=labels, annotations=annotations
        )
        pod_metadata = self.models.V1ObjectMeta(
            labels=labels, annotations=annotations
        )
        config_json = annotations[_CONFIG_REFS_ANNOTATION]
        container = self.models.V1Container(
            name="runtime",
            image=request.image_reference,
            env=[
                self.models.V1EnvVar(
                    name="AIGEAR_RELEASE_ID", value=request.release_id.typed
                ),
                self.models.V1EnvVar(
                    name="AIGEAR_CONFIG_REFERENCES", value=config_json
                ),
            ],
            startup_probe=self.models.V1Probe(
                http_get=self.models.V1HTTPGetAction(
                    path=request.startup_probe_path, port=_RUNTIME_PROBE_PORT
                )
            ),
            readiness_probe=self.models.V1Probe(
                http_get=self.models.V1HTTPGetAction(
                    path=request.readiness_probe_path, port=_RUNTIME_PROBE_PORT
                )
            ),
        )
        pod_spec = self.models.V1PodSpec(
            containers=[container],
            service_account_name=request.service_account_name,
        )
        template = self.models.V1PodTemplateSpec(
            metadata=pod_metadata, spec=pod_spec
        )
        selector = self.models.V1LabelSelector(
            match_labels={_RELEASE_LABEL: request.release_id.bare}
        )
        spec = self.models.V1DeploymentSpec(
            replicas=request.replicas,
            selector=selector,
            template=template,
        )
        return self.models.V1Deployment(
            api_version="apps/v1",
            kind="Deployment",
            metadata=metadata,
            spec=spec,
        )

    def create_deployment(self, request: DeploymentCreateRequest) -> DeploymentState:
        if not isinstance(request, DeploymentCreateRequest):
            raise KubernetesReleaseError("request must be DeploymentCreateRequest")
        try:
            resource = self.apps_api.create_namespaced_deployment(
                namespace=self.namespace,
                body=self._deployment_body(request),
            )
        except Exception as exc:
            self._mutation_error(exc)
        state = self._deployment_state(resource)
        if state.request.immutable_identity != request.immutable_identity:
            raise KubernetesReleaseError("created Deployment does not match request")
        return state

    def _service_state(self, resource: Any) -> StableServiceState:
        metadata = self._metadata(resource)
        selector = dict(getattr(resource.spec, "selector", None) or {})
        release_value = selector.get(_RELEASE_LABEL)
        try:
            release_id = (
                None if release_value is None else TypedId.from_bare(release_value)
            )
            fence = int(self._annotations(metadata).get(_FENCE_ANNOTATION, "0"))
            return StableServiceState(
                service_name=metadata.name,
                uid=metadata.uid,
                resource_version=metadata.resource_version,
                traffic_release_id=release_id,
                fencing_token=fence,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise KubernetesReleaseError(
                "Service response violates the release contract"
            ) from exc

    def get_service(self, service_name: str) -> Optional[StableServiceState]:
        try:
            resource = self.core_api.read_namespaced_service(
                name=service_name, namespace=self.namespace
            )
        except Exception as exc:
            if self._status(exc) == 404:
                return None
            self._read_error(exc)
        return self._service_state(resource)

    def patch_service_traffic(self, patch: ServiceTrafficPatch) -> StableServiceState:
        if not isinstance(patch, ServiceTrafficPatch):
            raise KubernetesReleaseError("patch must be ServiceTrafficPatch")
        current = self.get_service(patch.service_name)
        if current is None:
            raise KubernetesResourceConflict("stable Service does not exist")
        if (
            current.uid != patch.expected_uid
            or current.resource_version != patch.expected_resource_version
            or current.fencing_token > patch.fencing_token
        ):
            raise KubernetesResourceConflict(
                "Service UID, resourceVersion, or fence changed"
            )
        body = self.models.V1Service(
            metadata=self.models.V1ObjectMeta(
                uid=patch.expected_uid,
                resource_version=patch.expected_resource_version,
                annotations={_FENCE_ANNOTATION: str(patch.fencing_token)},
            ),
            spec=self.models.V1ServiceSpec(
                selector={_RELEASE_LABEL: patch.target_release_id.bare}
            ),
        )
        try:
            resource = self.core_api.patch_namespaced_service(
                name=patch.service_name,
                namespace=self.namespace,
                body=body,
            )
        except Exception as exc:
            self._mutation_error(exc)
        state = self._service_state(resource)
        if (
            state.uid != patch.expected_uid
            or state.traffic_release_id != patch.target_release_id
            or state.fencing_token != patch.fencing_token
        ):
            raise KubernetesReleaseError("patched Service does not match request")
        return state

    def get_endpoint_slice(self, service_name: str) -> EndpointSliceState:
        try:
            result = self.discovery_api.list_namespaced_endpoint_slice(
                namespace=self.namespace,
                label_selector=f"kubernetes.io/service-name={service_name}",
            )
        except Exception as exc:
            self._read_error(exc)
        endpoints = []
        for item in getattr(result, "items", ()):
            release_value = self._labels(self._metadata(item)).get(_RELEASE_LABEL)
            if release_value is None:
                continue
            release_id = TypedId.from_bare(release_value)
            for endpoint in getattr(item, "endpoints", ()):
                target = getattr(endpoint, "target_ref", None)
                pod_uid = getattr(target, "uid", None)
                if not pod_uid:
                    raise KubernetesReleaseError("EndpointSlice endpoint lacks Pod UID")
                conditions = getattr(endpoint, "conditions", None)
                endpoints.append(
                    EndpointState(
                        pod_uid=pod_uid,
                        release_id=release_id,
                        ready=bool(getattr(conditions, "ready", False)),
                    )
                )
        metadata = self._metadata(result)
        return EndpointSliceState(
            service_name=service_name,
            resource_version=metadata.resource_version,
            endpoints=tuple(sorted(endpoints, key=lambda value: value.pod_uid)),
        )

    def probe(self, request: ProbeRequest) -> ProbeResult:
        if self.probe_client is None:
            raise KubernetesReleaseError("probe client is not configured")
        try:
            result = self.probe_client(request)
        except (TimeoutError, OSError) as exc:
            raise KubernetesReleaseError("release probe failed closed") from exc
        if not isinstance(result, ProbeResult):
            raise KubernetesReleaseError("probe client returned an invalid result")
        return result

    def drain(self, deployment_uid: str) -> DrainResult:
        if self.drain_client is None:
            raise KubernetesReleaseError("drain client is not configured")
        try:
            result = self.drain_client(deployment_uid)
        except (TimeoutError, OSError) as exc:
            raise KubernetesReleaseError("release drain failed closed") from exc
        if not isinstance(result, DrainResult):
            raise KubernetesReleaseError("drain client returned an invalid result")
        return result

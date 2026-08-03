"""Typed Kubernetes release port and deterministic in-memory fake."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Dict, Optional, Protocol, Tuple

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "KubernetesReleaseError",
    "KubernetesResourceConflict",
    "KubernetesMutationUncertain",
    "MutationFault",
    "DeploymentCreateRequest",
    "DeploymentState",
    "ServiceTrafficPatch",
    "StableServiceState",
    "EndpointState",
    "EndpointSliceState",
    "ProbeRequest",
    "ProbeResult",
    "DrainResult",
    "KubernetesReleasePort",
    "FakeKubernetesReleasePort",
]


class KubernetesReleaseError(ValueError):
    pass


class KubernetesResourceConflict(KubernetesReleaseError):
    pass


class KubernetesMutationUncertain(KubernetesReleaseError):
    """The caller must read the resource before deciding whether to retry."""


class MutationFault(str, Enum):
    ACK_LOST_AFTER_COMMIT = "ack_lost_after_commit"
    TIMEOUT_BEFORE_COMMIT = "timeout_before_commit"
    TIMEOUT_AFTER_COMMIT = "timeout_after_commit"


def _positive(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise KubernetesReleaseError(f"{field_name} must be a positive int")


def _typed(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise KubernetesReleaseError(f"{field_name} must be a TypedId")


@dataclass(frozen=True)
class DeploymentCreateRequest:
    name: str
    service_name: str
    release_id: TypedId
    image_reference: str
    manifest_digest: TypedId
    deployment_spec_digest: TypedId
    replicas: int
    fencing_token: int

    def __post_init__(self) -> None:
        for field_name in ("name", "service_name"):
            object.__setattr__(
                self,
                field_name,
                validate_segment(getattr(self, field_name), field_name=field_name),
            )
        for field_name in (
            "release_id",
            "manifest_digest",
            "deployment_spec_digest",
        ):
            _typed(field_name, getattr(self, field_name))
        if (
            not isinstance(self.image_reference, str)
            or self.image_reference.count("@sha256:") != 1
        ):
            raise KubernetesReleaseError("image_reference must be digest pinned")
        _positive("replicas", self.replicas)
        _positive("fencing_token", self.fencing_token)

    @property
    def immutable_identity(self) -> tuple:
        return (
            self.name,
            self.service_name,
            self.release_id,
            self.image_reference,
            self.manifest_digest,
            self.deployment_spec_digest,
            self.replicas,
            self.fencing_token,
        )


@dataclass(frozen=True)
class DeploymentState:
    request: DeploymentCreateRequest
    uid: str
    resource_version: str
    available_replicas: int

    def __post_init__(self) -> None:
        if not isinstance(self.request, DeploymentCreateRequest):
            raise KubernetesReleaseError("request must be DeploymentCreateRequest")
        for field_name in ("uid", "resource_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise KubernetesReleaseError(f"{field_name} must be a non-empty str")
        if (
            isinstance(self.available_replicas, bool)
            or not isinstance(self.available_replicas, int)
            or not 0 <= self.available_replicas <= self.request.replicas
        ):
            raise KubernetesReleaseError("available_replicas is outside replica bounds")


@dataclass(frozen=True)
class ServiceTrafficPatch:
    service_name: str
    expected_uid: str
    expected_resource_version: str
    target_release_id: TypedId
    fencing_token: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "service_name",
            validate_segment(self.service_name, field_name="service_name"),
        )
        for field_name in ("expected_uid", "expected_resource_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise KubernetesReleaseError(f"{field_name} must be a non-empty str")
        _typed("target_release_id", self.target_release_id)
        _positive("fencing_token", self.fencing_token)


@dataclass(frozen=True)
class StableServiceState:
    service_name: str
    uid: str
    resource_version: str
    traffic_release_id: Optional[TypedId]
    fencing_token: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "service_name",
            validate_segment(self.service_name, field_name="service_name"),
        )
        if not self.uid or not self.resource_version:
            raise KubernetesReleaseError("service UID and resourceVersion are required")
        if self.traffic_release_id is not None:
            _typed("traffic_release_id", self.traffic_release_id)
        if isinstance(self.fencing_token, bool) or self.fencing_token < 0:
            raise KubernetesReleaseError("fencing_token must be non-negative")


@dataclass(frozen=True)
class EndpointState:
    pod_uid: str
    release_id: TypedId
    ready: bool


@dataclass(frozen=True)
class EndpointSliceState:
    service_name: str
    service_resource_version: str
    endpoints: Tuple[EndpointState, ...]


@dataclass(frozen=True)
class ProbeRequest:
    service_name: str
    release_id: TypedId
    pod_uid: str
    fencing_token: int


@dataclass(frozen=True)
class ProbeResult:
    passed: bool
    evidence_digest: TypedId
    summary: str = ""


@dataclass(frozen=True)
class DrainResult:
    deployment_uid: str
    active_connections: int
    complete: bool


class KubernetesReleasePort(Protocol):
    def get_deployment(self, name: str) -> Optional[DeploymentState]: ...

    def create_deployment(self, request: DeploymentCreateRequest) -> DeploymentState: ...

    def get_service(self, service_name: str) -> Optional[StableServiceState]: ...

    def patch_service_traffic(self, patch: ServiceTrafficPatch) -> StableServiceState: ...

    def get_endpoint_slice(self, service_name: str) -> EndpointSliceState: ...

    def probe(self, request: ProbeRequest) -> ProbeResult: ...

    def drain(self, deployment_uid: str) -> DrainResult: ...


class FakeKubernetesReleasePort:
    """Deterministic fake with explicit fault and convergence controls."""

    def __init__(self) -> None:
        self._resource_version = 0
        self._deployments: Dict[str, DeploymentState] = {}
        self._services: Dict[str, StableServiceState] = {}
        self._faults: Dict[str, list[MutationFault]] = {}
        self._endpoint_delay: Dict[TypedId, int] = {}
        self._endpoint_reads: Dict[TypedId, int] = {}
        self._probe_results: Dict[str, ProbeResult] = {}
        self._connections: Dict[str, tuple[int, int]] = {}

    def _next_resource_version(self) -> str:
        self._resource_version += 1
        return str(self._resource_version)

    def queue_fault(self, operation: str, fault: MutationFault) -> None:
        if operation not in {"create_deployment", "patch_service_traffic"}:
            raise KubernetesReleaseError("unknown mutation operation")
        if not isinstance(fault, MutationFault):
            raise KubernetesReleaseError("fault must be MutationFault")
        self._faults.setdefault(operation, []).append(fault)

    def _fault(self, operation: str, *, after_commit: bool) -> None:
        queued = self._faults.get(operation, [])
        if not queued:
            return
        fault = queued[0]
        applies_after = fault in {
            MutationFault.ACK_LOST_AFTER_COMMIT,
            MutationFault.TIMEOUT_AFTER_COMMIT,
        }
        if applies_after != after_commit:
            return
        queued.pop(0)
        raise KubernetesMutationUncertain(fault.value)

    def seed_service(self, service_name: str) -> StableServiceState:
        service_name = validate_segment(service_name, field_name="service_name")
        existing = self._services.get(service_name)
        if existing is not None:
            return existing
        state = StableServiceState(
            service_name=service_name,
            uid=f"service-{service_name}",
            resource_version=self._next_resource_version(),
            traffic_release_id=None,
            fencing_token=0,
        )
        self._services[service_name] = state
        return state

    def get_deployment(self, name: str) -> Optional[DeploymentState]:
        return self._deployments.get(name)

    def create_deployment(self, request: DeploymentCreateRequest) -> DeploymentState:
        if not isinstance(request, DeploymentCreateRequest):
            raise KubernetesReleaseError("request must be DeploymentCreateRequest")
        existing = self._deployments.get(request.name)
        if existing is not None:
            if existing.request.immutable_identity != request.immutable_identity:
                raise KubernetesResourceConflict(
                    "deployment name has conflicting immutable content"
                )
            return existing
        self._fault("create_deployment", after_commit=False)
        state = DeploymentState(
            request=request,
            uid=f"deployment-{len(self._deployments) + 1}",
            resource_version=self._next_resource_version(),
            available_replicas=request.replicas,
        )
        self._deployments[request.name] = state
        self._fault("create_deployment", after_commit=True)
        return state

    def get_service(self, service_name: str) -> Optional[StableServiceState]:
        return self._services.get(service_name)

    def patch_service_traffic(self, patch: ServiceTrafficPatch) -> StableServiceState:
        if not isinstance(patch, ServiceTrafficPatch):
            raise KubernetesReleaseError("patch must be ServiceTrafficPatch")
        current = self._services.get(patch.service_name)
        if current is None:
            raise KubernetesResourceConflict("stable service does not exist")
        if (
            current.uid != patch.expected_uid
            or current.resource_version != patch.expected_resource_version
        ):
            raise KubernetesResourceConflict("service resourceVersion CAS failed")
        if patch.fencing_token < current.fencing_token:
            raise KubernetesResourceConflict("service fencing token moved forward")
        if not any(
            deployment.request.release_id == patch.target_release_id
            for deployment in self._deployments.values()
        ):
            raise KubernetesResourceConflict("target deployment does not exist")
        self._fault("patch_service_traffic", after_commit=False)
        updated = replace(
            current,
            resource_version=self._next_resource_version(),
            traffic_release_id=patch.target_release_id,
            fencing_token=patch.fencing_token,
        )
        self._services[patch.service_name] = updated
        self._fault("patch_service_traffic", after_commit=True)
        return updated

    def configure_endpoint_delay(self, release_id: TypedId, reads: int) -> None:
        _typed("release_id", release_id)
        if isinstance(reads, bool) or not isinstance(reads, int) or reads < 0:
            raise KubernetesReleaseError("reads must be a non-negative int")
        self._endpoint_delay[release_id] = reads
        self._endpoint_reads[release_id] = 0

    def get_endpoint_slice(self, service_name: str) -> EndpointSliceState:
        service = self._services.get(service_name)
        if service is None:
            raise KubernetesResourceConflict("stable service does not exist")
        release_id = service.traffic_release_id
        endpoints: Tuple[EndpointState, ...] = ()
        if release_id is not None:
            reads = self._endpoint_reads.get(release_id, 0) + 1
            self._endpoint_reads[release_id] = reads
            if reads > self._endpoint_delay.get(release_id, 0):
                deployment = next(
                    (
                        value
                        for value in self._deployments.values()
                        if value.request.release_id == release_id
                    ),
                    None,
                )
                if deployment is not None:
                    endpoints = tuple(
                        EndpointState(
                            pod_uid=f"{deployment.uid}-pod-{index}",
                            release_id=release_id,
                            ready=index <= deployment.available_replicas,
                        )
                        for index in range(1, deployment.request.replicas + 1)
                    )
        return EndpointSliceState(
            service_name=service_name,
            service_resource_version=service.resource_version,
            endpoints=endpoints,
        )

    def set_probe_result(self, pod_uid: str, result: ProbeResult) -> None:
        if not pod_uid or not isinstance(result, ProbeResult):
            raise KubernetesReleaseError("pod_uid and ProbeResult are required")
        self._probe_results[pod_uid] = result

    def probe(self, request: ProbeRequest) -> ProbeResult:
        if not isinstance(request, ProbeRequest):
            raise KubernetesReleaseError("request must be ProbeRequest")
        result = self._probe_results.get(request.pod_uid)
        if result is None:
            raise KubernetesResourceConflict("pod has no configured probe result")
        return result

    def set_active_connections(
        self, deployment_uid: str, *, count: int, close_per_drain: int
    ) -> None:
        if not deployment_uid:
            raise KubernetesReleaseError("deployment_uid is required")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (count, close_per_drain)
        ):
            raise KubernetesReleaseError("connection controls must be non-negative ints")
        self._connections[deployment_uid] = (count, close_per_drain)

    def drain(self, deployment_uid: str) -> DrainResult:
        if deployment_uid not in self._connections:
            raise KubernetesResourceConflict("deployment connection state is unknown")
        count, close_per_drain = self._connections[deployment_uid]
        remaining = max(0, count - close_per_drain)
        self._connections[deployment_uid] = (remaining, close_per_drain)
        return DrainResult(
            deployment_uid=deployment_uid,
            active_connections=remaining,
            complete=remaining == 0,
        )

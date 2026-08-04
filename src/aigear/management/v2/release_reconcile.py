"""One bounded, fact-driven reconciliation pass for a service release Saga."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timezone
from enum import Enum

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.release import (
    ReleaseOperationRecord,
    ReleasePhase,
    ServiceReleaseState,
)
from aigear.management.v2.records.runtime_evidence import (
    RuntimeEvidenceKind,
    RuntimeEvidenceRecord,
    compute_runtime_evidence_id,
)
from aigear.management.v2.release_kubernetes import (
    EndpointSliceState,
    KubernetesReleaseError,
    KubernetesReleasePort,
    ServiceTrafficPatch,
    StableServiceState,
)
from aigear.management.v2.release_lease import (
    ReleaseExternalState,
    ReleaseLeaseError,
    require_release_lease,
    takeover_release_lease,
)

__all__ = [
    "ReleaseReconcileError",
    "ReleaseReconcileAction",
    "ReleaseReconcileResult",
    "reconcile_release_once",
]


class ReleaseReconcileError(ValueError):
    pass


class ReleaseReconcileAction(str, Enum):
    ADOPT_FENCE = "adopt_fence"
    FINALIZE_TRAFFIC = "finalize_traffic"
    COMPENSATE_TRAFFIC = "compensate_traffic"
    DRAIN_PROGRESS = "drain_progress"
    COMPLETE_DRAIN = "complete_drain"


@dataclass(frozen=True)
class ReleaseReconcileResult:
    operation: ReleaseOperationRecord
    service_state: ServiceReleaseState
    actions: tuple[ReleaseReconcileAction, ...]
    unknown: bool = False


def _deployment_name(release_id: TypedId) -> str:
    return f"release-{release_id.bare}"


def _external_reader(registry, kubernetes, operation_id: str):
    def read(service_name: str) -> ReleaseExternalState:
        operation = registry.get_release_operation(operation_id)
        if operation is None:
            raise ReleaseReconcileError("release operation disappeared")
        deployment = kubernetes.get_deployment(
            _deployment_name(operation.target_release_id)
        )
        service = kubernetes.get_service(service_name)
        return ReleaseExternalState(
            deployment_uid=None if deployment is None else deployment.uid,
            deployment_resource_version=(
                None if deployment is None else deployment.resource_version
            ),
            service_resource_version=(
                None if service is None else service.resource_version
            ),
        )

    return read


def _target_ready(
    endpoint_slice: EndpointSliceState,
    *,
    release_id: TypedId,
    replicas: int,
) -> bool:
    return (
        len(endpoint_slice.endpoints) == replicas
        and all(
            endpoint.release_id == release_id and endpoint.ready
            for endpoint in endpoint_slice.endpoints
        )
    )


def _update_after_service_patch(
    registry,
    *,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    service: StableServiceState,
) -> tuple[ReleaseOperationRecord, ServiceReleaseState]:
    def commit(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = tx.get_service_release_state(operation.service_name)
        if (
            operation.phase is not ReleasePhase.RECONCILING
            or state is None
            or state.revision != operation.expected_service_revision
            or state.active_operation_id != operation.operation_id
        ):
            raise ReleaseReconcileError(
                "Registry state changed after conditional Service patch"
            )
        service_is_registry_traffic = (
            service.traffic_release_id == state.traffic_release_id
        )
        service_is_target = service.traffic_release_id == operation.target_release_id
        new_state = state
        if service_is_registry_traffic and state.traffic_release_id is not None:
            new_state = replace(
                state,
                revision=state.revision + 1,
                traffic_k8s_resource_version=service.resource_version,
                updated_at=operation.updated_at,
            )
        elif service_is_target and state.traffic_release_id == operation.target_release_id:
            new_state = replace(
                state,
                revision=state.revision + 1,
                traffic_k8s_resource_version=service.resource_version,
                updated_at=operation.updated_at,
            )
        updated = replace(
            operation,
            revision=operation.revision + 1,
            expected_service_revision=new_state.revision,
            expected_service_resource_version=service.resource_version,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(updated)
        if new_state is not state:
            tx.put_service_release_state(new_state)
        return updated, new_state

    return registry.run_atomic(commit)


def _patch_service(
    registry,
    kubernetes: KubernetesReleasePort,
    *,
    operation: ReleaseOperationRecord,
    service: StableServiceState,
    owner_principal: str,
    target_release_id: TypedId | None,
) -> tuple[ReleaseOperationRecord, ServiceReleaseState, StableServiceState]:
    try:
        kubernetes.patch_service_traffic(
            ServiceTrafficPatch(
                service_name=service.service_name,
                expected_uid=service.uid,
                expected_resource_version=service.resource_version,
                target_release_id=target_release_id,
                fencing_token=operation.fencing_token,
            )
        )
        observed = kubernetes.get_service(service.service_name)
    except KubernetesReleaseError as exc:
        raise ReleaseReconcileError(
            "conditional Service reconciliation is uncertain"
        ) from exc
    if (
        observed is None
        or observed.uid != service.uid
        or observed.resource_version == service.resource_version
        or observed.traffic_release_id != target_release_id
        or observed.fencing_token != operation.fencing_token
    ):
        raise ReleaseReconcileError(
            "conditional Service reconciliation could not be proven"
        )
    updated, state = _update_after_service_patch(
        registry,
        operation_id=operation.operation_id,
        owner_principal=owner_principal,
        fencing_token=operation.fencing_token,
        service=observed,
    )
    return updated, state, observed


def _finalize_traffic_facts(
    registry,
    *,
    operation: ReleaseOperationRecord,
    owner_principal: str,
    service: StableServiceState,
) -> tuple[ReleaseOperationRecord, ServiceReleaseState]:
    def commit(tx):
        current = require_release_lease(
            tx,
            operation_id=operation.operation_id,
            owner_principal=owner_principal,
            fencing_token=operation.fencing_token,
        )
        state = tx.get_service_release_state(current.service_name)
        evidence = (
            None
            if current.traffic_evidence_id is None
            else tx.get_runtime_evidence(
                current.service_name, current.traffic_evidence_id
            )
        )
        if (
            current.phase is not ReleasePhase.RECONCILING
            or state is None
            or state.revision != current.expected_service_revision
            or state.desired_release_id != current.target_release_id
            or state.observed_release_id != current.target_release_id
            or service.traffic_release_id != current.target_release_id
            or service.fencing_token != current.fencing_token
            or evidence is None
            or evidence.kind is not RuntimeEvidenceKind.TRAFFIC
            or evidence.release_id != current.target_release_id
        ):
            raise ReleaseReconcileError(
                "traffic facts are not sufficient for reconciliation finalize"
            )
        previous = (
            state.previous_release_id
            if state.champion_release_id == current.target_release_id
            else state.champion_release_id
        )
        new_state = replace(
            state,
            revision=state.revision + 1,
            traffic_release_id=current.target_release_id,
            traffic_k8s_resource_version=service.resource_version,
            champion_release_id=current.target_release_id,
            previous_release_id=previous,
            updated_at=current.updated_at,
        )
        updated = replace(
            current,
            revision=current.revision + 1,
            expected_service_revision=new_state.revision,
            expected_service_resource_version=service.resource_version,
            updated_at=current.updated_at,
        )
        tx.put_release_operation(updated)
        tx.put_service_release_state(new_state)
        return updated, new_state

    return registry.run_atomic(commit)


def _drain_evidence(
    *,
    operation: ReleaseOperationRecord,
    state: ServiceReleaseState,
    traffic_evidence: RuntimeEvidenceRecord,
    service: StableServiceState,
    endpoint_slice: EndpointSliceState,
    old_deployment_uid: str | None,
    issuer_principal: str,
) -> RuntimeEvidenceRecord:
    payload_digest = TypedId.from_bare(
        digest_sha256_of_jcs(
            {
                "domain": "aigear.release-reconcile-drain.v2",
                "release_id": operation.target_release_id.typed,
                "previous_release_id": (
                    None
                    if state.previous_release_id is None
                    else state.previous_release_id.typed
                ),
                "old_deployment_uid": old_deployment_uid,
                "active_connections": 0,
                "service_resource_version": service.resource_version,
                "endpoint_slice_resource_version": endpoint_slice.resource_version,
                "fencing_token": operation.fencing_token,
            }
        )
    )
    bindings = tuple(
        sorted(
            (
                "active-connections=0",
                f"service={service.uid}@{service.resource_version}",
                f"endpoints={endpoint_slice.resource_version}",
                f"previous={state.previous_release_id}",
            )
        )
    )
    pod_uid = old_deployment_uid or traffic_evidence.pod_uid
    return RuntimeEvidenceRecord(
        schema_version="2.0",
        environment_fingerprint=state.environment_fingerprint,
        evidence_id=compute_runtime_evidence_id(
            kind=RuntimeEvidenceKind.DRAIN,
            release_id=operation.target_release_id,
            pod_uid=pod_uid,
            k8s_resource_version=endpoint_slice.resource_version,
            binding_tuple=bindings,
            fencing_token=operation.fencing_token,
            payload_digest=payload_digest,
        ),
        kind=RuntimeEvidenceKind.DRAIN,
        release_id=operation.target_release_id,
        pod_uid=pod_uid,
        k8s_resource_version=endpoint_slice.resource_version,
        binding_tuple=bindings,
        policy_decision_epoch=traffic_evidence.policy_decision_epoch,
        security_watermark=traffic_evidence.security_watermark,
        fencing_token=operation.fencing_token,
        issuer_principal=issuer_principal,
        payload_digest=payload_digest,
        issued_at=traffic_evidence.issued_at,
        expires_at=traffic_evidence.expires_at,
    )


def _commit_success(
    registry,
    *,
    operation: ReleaseOperationRecord,
    owner_principal: str,
    service: StableServiceState,
    evidence: RuntimeEvidenceRecord,
) -> tuple[ReleaseOperationRecord, ServiceReleaseState]:
    def commit(tx):
        current = require_release_lease(
            tx,
            operation_id=operation.operation_id,
            owner_principal=owner_principal,
            fencing_token=operation.fencing_token,
        )
        state = tx.get_service_release_state(current.service_name)
        traffic_evidence = tx.get_runtime_evidence(
            current.service_name, current.traffic_evidence_id
        )
        if (
            current.phase is not ReleasePhase.RECONCILING
            or state is None
            or state.revision != current.expected_service_revision
            or state.desired_release_id != current.target_release_id
            or state.observed_release_id != current.target_release_id
            or state.traffic_release_id != current.target_release_id
            or state.champion_release_id != current.target_release_id
            or state.traffic_k8s_resource_version != service.resource_version
            or service.traffic_release_id != current.target_release_id
            or service.fencing_token != current.fencing_token
            or traffic_evidence is None
        ):
            raise ReleaseReconcileError(
                "terminal reconciliation facts changed before commit"
            )
        tx.put_runtime_evidence(current.service_name, evidence)
        server_time = tx.get_server_read_time().astimezone(timezone.utc).isoformat()
        succeeded_state = replace(
            state,
            revision=state.revision + 1,
            active_operation_phase=ReleasePhase.SUCCEEDED,
            updated_at=server_time,
        )
        succeeded = replace(
            current,
            phase=ReleasePhase.SUCCEEDED,
            revision=current.revision + 1,
            expected_service_revision=succeeded_state.revision,
            drain_evidence_id=evidence.evidence_id,
            lease_expires_at=None,
            error_class=None,
            error_summary=None,
            updated_at=server_time,
            finished_at=server_time,
        )
        tx.put_release_operation(succeeded)
        tx.put_service_release_state(succeeded_state)
        return succeeded, succeeded_state

    return registry.run_atomic(commit)


def reconcile_release_once(
    registry,
    kubernetes: KubernetesReleasePort,
    *,
    operation_id: str,
    owner_principal: str,
    evidence_issuer_principal: str,
    max_actions: int = 3,
    lease_ttl_seconds: int = 120,
) -> ReleaseReconcileResult:
    if (
        isinstance(max_actions, bool)
        or not isinstance(max_actions, int)
        or not 1 <= max_actions <= 16
    ):
        raise ReleaseReconcileError("max_actions must be an int in [1, 16]")
    existing = registry.get_release_operation(operation_id)
    if existing is None:
        raise ReleaseReconcileError("release operation does not exist")
    existing_state = registry.get_service_release_state(existing.service_name)
    if existing_state is None:
        raise ReleaseReconcileError("service release state does not exist")
    if existing.finished_at is not None:
        return ReleaseReconcileResult(existing, existing_state, ())
    try:
        operation = takeover_release_lease(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            read_external_state=_external_reader(
                registry, kubernetes, operation_id
            ),
            lease_ttl_seconds=lease_ttl_seconds,
        )
    except ReleaseLeaseError as exc:
        raise ReleaseReconcileError("release lease takeover failed") from exc
    state = registry.get_service_release_state(operation.service_name)
    actions: list[ReleaseReconcileAction] = []
    unknown = False

    while len(actions) < max_actions:
        try:
            service = kubernetes.get_service(operation.service_name)
            deployment = kubernetes.get_deployment(
                _deployment_name(operation.target_release_id)
            )
            endpoint_slice = (
                None
                if service is None
                else kubernetes.get_endpoint_slice(operation.service_name)
            )
        except KubernetesReleaseError:
            unknown = True
            break
        if service is None:
            unknown = True
            break
        if service.traffic_release_id == operation.target_release_id:
            if operation.traffic_evidence_id is None:
                operation, state, _ = _patch_service(
                    registry,
                    kubernetes,
                    operation=operation,
                    service=service,
                    owner_principal=owner_principal,
                    target_release_id=state.traffic_release_id,
                )
                actions.append(ReleaseReconcileAction.COMPENSATE_TRAFFIC)
                continue
            if service.fencing_token != operation.fencing_token:
                operation, state, _ = _patch_service(
                    registry,
                    kubernetes,
                    operation=operation,
                    service=service,
                    owner_principal=owner_principal,
                    target_release_id=operation.target_release_id,
                )
                actions.append(ReleaseReconcileAction.ADOPT_FENCE)
                continue
            if (
                deployment is None
                or deployment.uid != operation.expected_deployment_uid
                or deployment.request.release_id != operation.target_release_id
                or endpoint_slice is None
                or not _target_ready(
                    endpoint_slice,
                    release_id=operation.target_release_id,
                    replicas=deployment.request.replicas,
                )
            ):
                unknown = True
                break
            facts_equal = (
                state.desired_release_id == operation.target_release_id
                and state.observed_release_id == operation.target_release_id
                and state.traffic_release_id == operation.target_release_id
                and state.champion_release_id == operation.target_release_id
                and state.traffic_k8s_resource_version == service.resource_version
            )
            if not facts_equal:
                if (
                    state.desired_release_id != operation.target_release_id
                    or state.observed_release_id != operation.target_release_id
                ):
                    unknown = True
                    break
                operation, state = _finalize_traffic_facts(
                    registry,
                    operation=operation,
                    owner_principal=owner_principal,
                    service=service,
                )
                actions.append(ReleaseReconcileAction.FINALIZE_TRAFFIC)
                continue
            traffic_evidence = registry.get_runtime_evidence(
                operation.service_name, operation.traffic_evidence_id
            )
            if traffic_evidence is None:
                unknown = True
                break
            old_uid = None
            if state.previous_release_id is not None:
                old = kubernetes.get_deployment(
                    _deployment_name(state.previous_release_id)
                )
                if old is None or old.request.release_id != state.previous_release_id:
                    unknown = True
                    break
                drained = kubernetes.drain(old.uid)
                if (
                    drained.deployment_uid != old.uid
                    or drained.active_connections < 0
                ):
                    unknown = True
                    break
                if not drained.complete or drained.active_connections != 0:
                    actions.append(ReleaseReconcileAction.DRAIN_PROGRESS)
                    break
                old_uid = old.uid
            evidence = _drain_evidence(
                operation=operation,
                state=state,
                traffic_evidence=traffic_evidence,
                service=service,
                endpoint_slice=endpoint_slice,
                old_deployment_uid=old_uid,
                issuer_principal=evidence_issuer_principal,
            )
            operation, state = _commit_success(
                registry,
                operation=operation,
                owner_principal=owner_principal,
                service=service,
                evidence=evidence,
            )
            actions.append(ReleaseReconcileAction.COMPLETE_DRAIN)
            break
        if service.traffic_release_id == state.traffic_release_id:
            unknown = not actions
            break
        unknown = True
        break

    return ReleaseReconcileResult(
        operation=operation,
        service_state=state,
        actions=tuple(actions),
        unknown=unknown,
    )

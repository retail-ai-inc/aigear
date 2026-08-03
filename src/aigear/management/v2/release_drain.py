"""Drain the previous release before committing terminal release success."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timezone

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
from aigear.management.v2.release_finalize import ReleaseFinalizeResult
from aigear.management.v2.release_kubernetes import (
    DeploymentState,
    EndpointSliceState,
    KubernetesReleaseError,
    KubernetesReleasePort,
    StableServiceState,
)
from aigear.management.v2.release_lease import (
    heartbeat_release_lease,
    require_release_lease,
)
from aigear.management.v2.release_manifest import SignedReleaseManifest

__all__ = [
    "ReleaseDrainError",
    "ReleaseDrainConflict",
    "ReleaseDrainUncertain",
    "ReleaseDrainResult",
    "complete_release_drain",
]


class ReleaseDrainError(ValueError):
    pass


class ReleaseDrainConflict(ReleaseDrainError):
    pass


class ReleaseDrainUncertain(ReleaseDrainError):
    pass


@dataclass(frozen=True)
class ReleaseDrainResult:
    operation: ReleaseOperationRecord
    service_state: ServiceReleaseState
    drain_evidence: RuntimeEvidenceRecord


def _enter_draining(
    registry,
    *,
    manifest: SignedReleaseManifest,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
) -> tuple[ReleaseOperationRecord, ServiceReleaseState]:
    def enter(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = tx.get_service_release_state(operation.service_name)
        if (
            operation.phase not in {ReleasePhase.FINALIZING, ReleasePhase.DRAINING}
            or operation.target_release_id != manifest.release_id
            or state is None
            or state.revision != operation.expected_service_revision
            or state.active_operation_id != operation.operation_id
            or state.fencing_token != fencing_token
            or state.desired_release_id != manifest.release_id
            or state.observed_release_id != manifest.release_id
            or state.traffic_release_id != manifest.release_id
            or state.champion_release_id != manifest.release_id
        ):
            raise ReleaseDrainConflict(
                "Registry traffic facts are not ready for drain"
            )
        if operation.phase is ReleasePhase.DRAINING:
            return operation, state
        new_state = replace(
            state,
            revision=state.revision + 1,
            active_operation_phase=ReleasePhase.DRAINING,
            updated_at=operation.updated_at,
        )
        draining = replace(
            operation,
            phase=ReleasePhase.DRAINING,
            revision=operation.revision + 1,
            expected_service_revision=new_state.revision,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(draining)
        tx.put_service_release_state(new_state)
        return draining, new_state

    return registry.run_atomic(enter)


def _mark_reconciling(
    registry,
    *,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    error_class: str,
    error_summary: str,
) -> None:
    def mark(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = tx.get_service_release_state(operation.service_name)
        if (
            operation.phase is not ReleasePhase.DRAINING
            or state is None
            or state.revision != operation.expected_service_revision
            or state.active_operation_id != operation.operation_id
        ):
            raise ReleaseDrainUncertain(
                "service state changed while marking reconciliation"
            )
        new_state = replace(
            state,
            revision=state.revision + 1,
            active_operation_phase=ReleasePhase.RECONCILING,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(
            replace(
                operation,
                phase=ReleasePhase.RECONCILING,
                revision=operation.revision + 1,
                expected_service_revision=new_state.revision,
                error_class=error_class,
                error_summary=error_summary,
                updated_at=operation.updated_at,
            )
        )
        tx.put_service_release_state(new_state)

    registry.run_atomic(mark)


def _ready_target_endpoints(
    endpoint_slice: EndpointSliceState,
    *,
    release_id: TypedId,
    replicas: int,
) -> bool:
    return (
        len(endpoint_slice.endpoints) == replicas
        and all(
            endpoint.ready and endpoint.release_id == release_id
            for endpoint in endpoint_slice.endpoints
        )
    )


def _drain_evidence(
    *,
    manifest: SignedReleaseManifest,
    finalize: ReleaseFinalizeResult,
    old_deployment: DeploymentState | None,
    service: StableServiceState,
    endpoint_slice: EndpointSliceState,
    fencing_token: int,
    issuer_principal: str,
) -> RuntimeEvidenceRecord:
    traffic = finalize.traffic_switch.traffic_evidence
    previous = finalize.service_state.previous_release_id
    payload = {
        "domain": "aigear.release-drain.v2",
        "release_id": manifest.release_id.typed,
        "previous_release_id": None if previous is None else previous.typed,
        "previous_deployment_uid": (
            None if old_deployment is None else old_deployment.uid
        ),
        "previous_deployment_resource_version": (
            None if old_deployment is None else old_deployment.resource_version
        ),
        "active_connections": 0,
        "service_uid": service.uid,
        "service_resource_version": service.resource_version,
        "endpoint_slice_resource_version": endpoint_slice.resource_version,
        "fencing_token": fencing_token,
    }
    payload_digest = TypedId.from_bare(digest_sha256_of_jcs(payload))
    binding_tuple = tuple(
        sorted(
            (
                f"previous={None if previous is None else previous.typed}",
                "active-connections=0",
                f"service={service.uid}@{service.resource_version}",
                f"endpoints={endpoint_slice.resource_version}",
            )
        )
    )
    pod_uid = (
        traffic.pod_uid if old_deployment is None else old_deployment.uid
    )
    return RuntimeEvidenceRecord(
        schema_version="2.0",
        environment_fingerprint=manifest.core.environment_fingerprint,
        evidence_id=compute_runtime_evidence_id(
            kind=RuntimeEvidenceKind.DRAIN,
            release_id=manifest.release_id,
            pod_uid=pod_uid,
            k8s_resource_version=endpoint_slice.resource_version,
            binding_tuple=binding_tuple,
            fencing_token=fencing_token,
            payload_digest=payload_digest,
        ),
        kind=RuntimeEvidenceKind.DRAIN,
        release_id=manifest.release_id,
        pod_uid=pod_uid,
        k8s_resource_version=endpoint_slice.resource_version,
        binding_tuple=binding_tuple,
        policy_decision_epoch=traffic.policy_decision_epoch,
        security_watermark=traffic.security_watermark,
        fencing_token=fencing_token,
        issuer_principal=issuer_principal,
        payload_digest=payload_digest,
        issued_at=traffic.issued_at,
        expires_at=traffic.expires_at,
    )


def _commit_terminal(
    registry,
    *,
    manifest: SignedReleaseManifest,
    finalize: ReleaseFinalizeResult,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    service: StableServiceState,
    endpoint_slice: EndpointSliceState,
    evidence: RuntimeEvidenceRecord,
) -> ReleaseDrainResult:
    def commit(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = tx.get_service_release_state(operation.service_name)
        traffic_evidence = tx.get_runtime_evidence(
            operation.service_name,
            finalize.traffic_switch.traffic_evidence.evidence_id,
        )
        if (
            operation.phase is not ReleasePhase.DRAINING
            or operation.target_release_id != manifest.release_id
            or operation.expected_service_resource_version
            != service.resource_version
            or state is None
            or state.revision != operation.expected_service_revision
            or state.active_operation_id != operation.operation_id
            or state.active_operation_phase is not ReleasePhase.DRAINING
            or state.fencing_token != fencing_token
            or state.desired_release_id != manifest.release_id
            or state.observed_release_id != manifest.release_id
            or state.traffic_release_id != manifest.release_id
            or state.champion_release_id != manifest.release_id
            or state.traffic_k8s_resource_version != service.resource_version
            or traffic_evidence != finalize.traffic_switch.traffic_evidence
        ):
            raise ReleaseDrainConflict(
                "terminal release facts changed before drain commit"
            )
        tx.put_runtime_evidence(operation.service_name, evidence)
        server_time = tx.get_server_read_time().astimezone(timezone.utc).isoformat()
        succeeded_state = replace(
            state,
            revision=state.revision + 1,
            active_operation_phase=ReleasePhase.SUCCEEDED,
            updated_at=server_time,
        )
        succeeded = replace(
            operation,
            phase=ReleasePhase.SUCCEEDED,
            revision=operation.revision + 1,
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
        return ReleaseDrainResult(succeeded, succeeded_state, evidence)

    return registry.run_atomic(commit)


def complete_release_drain(
    registry,
    kubernetes: KubernetesReleasePort,
    *,
    manifest: SignedReleaseManifest,
    finalize: ReleaseFinalizeResult,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    evidence_issuer_principal: str,
    max_drain_reads: int = 3,
    lease_ttl_seconds: int = 120,
) -> ReleaseDrainResult:
    if (
        isinstance(max_drain_reads, bool)
        or not isinstance(max_drain_reads, int)
        or max_drain_reads < 1
    ):
        raise ReleaseDrainError("max_drain_reads must be a positive int")
    heartbeat_release_lease(
        registry,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    operation, state = _enter_draining(
        registry,
        manifest=manifest,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
    )
    if (
        finalize.operation.operation_id != operation.operation_id
        or finalize.operation.phase is not ReleasePhase.FINALIZING
        or finalize.traffic_switch.traffic_evidence.evidence_id
        != operation.traffic_evidence_id
    ):
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            error_class="FinalizeEvidenceMismatch",
            error_summary="finalize result is not bound to the draining operation",
        )
        raise ReleaseDrainConflict("finalize result does not match the operation")
    previous = state.previous_release_id
    old_deployment = None
    try:
        if previous is not None:
            old_deployment = kubernetes.get_deployment(
                f"release-{previous.bare}"
            )
            if (
                old_deployment is None
                or old_deployment.request.release_id != previous
            ):
                raise ReleaseDrainConflict(
                    "previous release Deployment is missing or drifted"
                )
            complete = False
            for _ in range(max_drain_reads):
                heartbeat_release_lease(
                    registry,
                    operation_id=operation_id,
                    owner_principal=owner_principal,
                    fencing_token=fencing_token,
                    lease_ttl_seconds=lease_ttl_seconds,
                )
                drained = kubernetes.drain(old_deployment.uid)
                if (
                    drained.deployment_uid != old_deployment.uid
                    or drained.active_connections < 0
                ):
                    raise ReleaseDrainConflict(
                        "drain response does not match the previous Deployment"
                    )
                if drained.complete and drained.active_connections == 0:
                    complete = True
                    break
            if not complete:
                raise ReleaseDrainUncertain(
                    "previous release connections did not drain within the read bound"
                )
        service = kubernetes.get_service(manifest.core.service_name)
        endpoint_slice = kubernetes.get_endpoint_slice(manifest.core.service_name)
        candidate = kubernetes.get_deployment(f"release-{manifest.release_id.bare}")
        if (
            service is None
            or service.uid != finalize.traffic_switch.service_after.uid
            or service.resource_version
            != operation.expected_service_resource_version
            or service.traffic_release_id != manifest.release_id
            or service.fencing_token != fencing_token
            or candidate is None
            or candidate.uid != operation.expected_deployment_uid
            or not _ready_target_endpoints(
                endpoint_slice,
                release_id=manifest.release_id,
                replicas=candidate.request.replicas,
            )
        ):
            raise ReleaseDrainConflict(
                "selector or EndpointSlice changed before terminal commit"
            )
        evidence = _drain_evidence(
            manifest=manifest,
            finalize=finalize,
            old_deployment=old_deployment,
            service=service,
            endpoint_slice=endpoint_slice,
            fencing_token=fencing_token,
            issuer_principal=evidence_issuer_principal,
        )
        return _commit_terminal(
            registry,
            manifest=manifest,
            finalize=finalize,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            service=service,
            endpoint_slice=endpoint_slice,
            evidence=evidence,
        )
    except (KubernetesReleaseError, ReleaseDrainError) as exc:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            error_class=type(exc).__name__,
            error_summary=str(exc),
        )
        if isinstance(exc, ReleaseDrainUncertain):
            raise
        raise ReleaseDrainUncertain(
            "release drain requires reconciliation"
        ) from exc

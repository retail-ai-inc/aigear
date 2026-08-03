"""Conditionally switch stable Service traffic and prove endpoint convergence."""

from __future__ import annotations

from dataclasses import dataclass, replace

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
    KubernetesMutationUncertain,
    KubernetesReleaseError,
    KubernetesReleasePort,
    KubernetesResourceConflict,
    ServiceTrafficPatch,
    StableServiceState,
)
from aigear.management.v2.release_lease import (
    heartbeat_release_lease,
    require_release_lease,
)
from aigear.management.v2.release_manifest import SignedReleaseManifest
from aigear.management.v2.release_verify import CandidateVerificationResult

__all__ = [
    "TrafficSwitchError",
    "TrafficSwitchConflict",
    "TrafficSwitchUncertain",
    "TrafficSwitchResult",
    "compute_traffic_switch_evidence_digest",
    "switch_release_traffic",
]


class TrafficSwitchError(ValueError):
    pass


class TrafficSwitchConflict(TrafficSwitchError):
    pass


class TrafficSwitchUncertain(TrafficSwitchError):
    pass


@dataclass(frozen=True)
class TrafficSwitchResult:
    operation: ReleaseOperationRecord
    service_state: ServiceReleaseState
    service_before: StableServiceState
    endpoint_slice_before: EndpointSliceState
    service_after: StableServiceState
    endpoint_slice_after: EndpointSliceState
    traffic_evidence: RuntimeEvidenceRecord


def _endpoint_payload(value: EndpointSliceState) -> dict:
    return {
        "resource_version": value.resource_version,
        "endpoints": [
            {
                "pod_uid": endpoint.pod_uid,
                "release_id": endpoint.release_id.typed,
                "ready": endpoint.ready,
            }
            for endpoint in sorted(value.endpoints, key=lambda item: item.pod_uid)
        ],
    }


def _service_payload(value: StableServiceState) -> dict:
    return {
        "uid": value.uid,
        "resource_version": value.resource_version,
        "traffic_release_id": (
            None
            if value.traffic_release_id is None
            else value.traffic_release_id.typed
        ),
        "fencing_token": value.fencing_token,
    }


def _traffic_payload(
    *,
    manifest: SignedReleaseManifest,
    fencing_token: int,
    service_before: StableServiceState,
    endpoint_slice_before: EndpointSliceState,
    service_after: StableServiceState,
    endpoint_slice_after: EndpointSliceState,
) -> dict:
    return {
        "domain": "aigear.release-traffic-switch.v2",
        "service_name": manifest.core.service_name,
        "release_id": manifest.release_id.typed,
        "fencing_token": fencing_token,
        "before": {
            "service": _service_payload(service_before),
            "endpoint_slice": _endpoint_payload(endpoint_slice_before),
        },
        "after": {
            "service": _service_payload(service_after),
            "endpoint_slice": _endpoint_payload(endpoint_slice_after),
        },
    }


def compute_traffic_switch_evidence_digest(
    *,
    manifest: SignedReleaseManifest,
    fencing_token: int,
    service_before: StableServiceState,
    endpoint_slice_before: EndpointSliceState,
    service_after: StableServiceState,
    endpoint_slice_after: EndpointSliceState,
) -> TypedId:
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            _traffic_payload(
                manifest=manifest,
                fencing_token=fencing_token,
                service_before=service_before,
                endpoint_slice_before=endpoint_slice_before,
                service_after=service_after,
                endpoint_slice_after=endpoint_slice_after,
            )
        )
    )


def _traffic_evidence(
    *,
    manifest: SignedReleaseManifest,
    verification: CandidateVerificationResult,
    fencing_token: int,
    issuer_principal: str,
    service_before: StableServiceState,
    endpoint_slice_before: EndpointSliceState,
    service_after: StableServiceState,
    endpoint_slice_after: EndpointSliceState,
) -> RuntimeEvidenceRecord:
    payload_digest = compute_traffic_switch_evidence_digest(
        manifest=manifest,
        fencing_token=fencing_token,
        service_before=service_before,
        endpoint_slice_before=endpoint_slice_before,
        service_after=service_after,
        endpoint_slice_after=endpoint_slice_after,
    )
    bindings = tuple(
        sorted(
            (
                f"service-before={service_before.uid}@{service_before.resource_version}",
                f"selector-before={service_before.traffic_release_id}",
                f"endpoints-before={endpoint_slice_before.resource_version}",
                f"service-after={service_after.uid}@{service_after.resource_version}",
                f"selector-after={service_after.traffic_release_id.typed}",
                f"endpoints-after={endpoint_slice_after.resource_version}",
            )
        )
    )
    smoke = verification.smoke_evidence
    return RuntimeEvidenceRecord(
        schema_version="2.0",
        environment_fingerprint=manifest.core.environment_fingerprint,
        evidence_id=compute_runtime_evidence_id(
            kind=RuntimeEvidenceKind.TRAFFIC,
            release_id=manifest.release_id,
            pod_uid=smoke.pod_uid,
            k8s_resource_version=endpoint_slice_after.resource_version,
            binding_tuple=bindings,
            fencing_token=fencing_token,
            payload_digest=payload_digest,
        ),
        kind=RuntimeEvidenceKind.TRAFFIC,
        release_id=manifest.release_id,
        pod_uid=smoke.pod_uid,
        k8s_resource_version=endpoint_slice_after.resource_version,
        binding_tuple=bindings,
        policy_decision_epoch=smoke.policy_decision_epoch,
        security_watermark=smoke.security_watermark,
        fencing_token=fencing_token,
        issuer_principal=issuer_principal,
        payload_digest=payload_digest,
        issued_at=smoke.issued_at,
        expires_at=smoke.expires_at,
    )


def _mark_reconciling(
    registry,
    *,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    observed_service: StableServiceState | None,
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
            state is None
            or state.revision != operation.expected_service_revision
            or state.active_operation_id != operation.operation_id
            or state.active_operation_phase is not operation.phase
        ):
            raise TrafficSwitchUncertain(
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
                expected_service_resource_version=(
                    operation.expected_service_resource_version
                    if observed_service is None
                    else observed_service.resource_version
                ),
                error_class=error_class,
                error_summary=error_summary,
                updated_at=operation.updated_at,
            )
        )
        tx.put_service_release_state(new_state)

    registry.run_atomic(mark)


def _converged(
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


def _commit_switch(
    registry,
    *,
    manifest: SignedReleaseManifest,
    verification: CandidateVerificationResult,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    service_before: StableServiceState,
    endpoint_slice_before: EndpointSliceState,
    service_after: StableServiceState,
    endpoint_slice_after: EndpointSliceState,
    traffic_evidence: RuntimeEvidenceRecord,
) -> TrafficSwitchResult:
    def commit(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = tx.get_service_release_state(operation.service_name)
        stored_smoke = tx.get_runtime_evidence(
            operation.service_name, verification.smoke_evidence.evidence_id
        )
        if (
            operation.phase is not ReleasePhase.VERIFYING
            or operation.target_release_id != manifest.release_id
            or state is None
            or state.revision != operation.expected_service_revision
            or state.observed_release_id != manifest.release_id
            or state.active_operation_id != operation.operation_id
            or stored_smoke != verification.smoke_evidence
        ):
            raise TrafficSwitchUncertain(
                "verified Registry state changed before traffic evidence commit"
            )
        tx.put_runtime_evidence(operation.service_name, traffic_evidence)
        new_state = replace(
            state,
            revision=state.revision + 1,
            active_operation_phase=ReleasePhase.SWITCHING_TRAFFIC,
            updated_at=operation.updated_at,
        )
        switched = replace(
            operation,
            phase=ReleasePhase.SWITCHING_TRAFFIC,
            revision=operation.revision + 1,
            expected_service_revision=new_state.revision,
            expected_service_resource_version=service_after.resource_version,
            traffic_evidence_id=traffic_evidence.evidence_id,
            error_class=None,
            error_summary=None,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(switched)
        tx.put_service_release_state(new_state)
        return TrafficSwitchResult(
            switched,
            new_state,
            service_before,
            endpoint_slice_before,
            service_after,
            endpoint_slice_after,
            traffic_evidence,
        )

    return registry.run_atomic(commit)


def switch_release_traffic(
    registry,
    kubernetes: KubernetesReleasePort,
    *,
    manifest: SignedReleaseManifest,
    verification: CandidateVerificationResult,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    evidence_issuer_principal: str,
    max_endpoint_reads: int = 3,
    lease_ttl_seconds: int = 120,
) -> TrafficSwitchResult:
    if (
        isinstance(max_endpoint_reads, bool)
        or not isinstance(max_endpoint_reads, int)
        or max_endpoint_reads < 1
    ):
        raise TrafficSwitchError("max_endpoint_reads must be a positive int")
    operation = heartbeat_release_lease(
        registry,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    if (
        operation.phase is not ReleasePhase.VERIFYING
        or verification.operation.operation_id != operation.operation_id
        or verification.smoke_evidence.release_id != manifest.release_id
    ):
        raise TrafficSwitchConflict("release operation is not ready to switch")
    deployment = kubernetes.get_deployment(
        f"release-{manifest.release_id.bare}"
    )
    if (
        deployment is None
        or deployment.uid != operation.expected_deployment_uid
        or deployment.resource_version
        != operation.expected_deployment_resource_version
        or deployment.request.release_id != manifest.release_id
    ):
        raise TrafficSwitchConflict("exact candidate Deployment is missing")
    service_before = kubernetes.get_service(manifest.core.service_name)
    if service_before is None or service_before.traffic_release_id == manifest.release_id:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=service_before,
            error_class="TrafficPreconditionUnknown",
            error_summary="stable Service pre-switch selector cannot be proven",
        )
        raise TrafficSwitchUncertain("stable Service pre-switch state is unknown")
    try:
        endpoint_slice_before = kubernetes.get_endpoint_slice(
            manifest.core.service_name
        )
        kubernetes.patch_service_traffic(
            ServiceTrafficPatch(
                service_name=manifest.core.service_name,
                expected_uid=service_before.uid,
                expected_resource_version=service_before.resource_version,
                target_release_id=manifest.release_id,
                fencing_token=fencing_token,
            )
        )
    except (KubernetesMutationUncertain, KubernetesResourceConflict) as exc:
        observed = kubernetes.get_service(manifest.core.service_name)
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=observed,
            error_class=type(exc).__name__,
            error_summary="conditional Service patch requires reconciliation",
        )
        raise TrafficSwitchUncertain(
            "conditional Service patch requires reconciliation"
        ) from exc
    except KubernetesReleaseError as exc:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=None,
            error_class=type(exc).__name__,
            error_summary="pre-switch Kubernetes state could not be read",
        )
        raise TrafficSwitchUncertain(
            "pre-switch Kubernetes state requires reconciliation"
        ) from exc
    try:
        service_after = kubernetes.get_service(manifest.core.service_name)
    except KubernetesReleaseError as exc:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=None,
            error_class=type(exc).__name__,
            error_summary="Service readback could not prove the requested selector",
        )
        raise TrafficSwitchUncertain(
            "Service selector update requires reconciliation"
        ) from exc
    if (
        service_after is None
        or service_after.uid != service_before.uid
        or service_after.resource_version == service_before.resource_version
        or service_after.traffic_release_id != manifest.release_id
        or service_after.fencing_token != fencing_token
    ):
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=service_after,
            error_class="TrafficPatchMismatch",
            error_summary="Service readback does not prove the requested selector",
        )
        raise TrafficSwitchUncertain("Service selector update is not proven")
    endpoint_slice_after = None
    try:
        for _ in range(max_endpoint_reads):
            observed_endpoints = kubernetes.get_endpoint_slice(
                manifest.core.service_name
            )
            if _converged(
                observed_endpoints,
                release_id=manifest.release_id,
                replicas=deployment.request.replicas,
            ):
                endpoint_slice_after = observed_endpoints
                break
    except KubernetesReleaseError as exc:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=service_after,
            error_class=type(exc).__name__,
            error_summary="EndpointSlice convergence could not be read",
        )
        raise TrafficSwitchUncertain(
            "EndpointSlice convergence requires reconciliation"
        ) from exc
    if endpoint_slice_after is None:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=service_after,
            error_class="EndpointSliceNotConverged",
            error_summary="new release endpoints did not converge within the read bound",
        )
        raise TrafficSwitchUncertain(
            "EndpointSlice convergence requires reconciliation"
        )
    final_service = kubernetes.get_service(manifest.core.service_name)
    if final_service != service_after:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed_service=final_service,
            error_class="ConcurrentServiceChange",
            error_summary="stable Service changed during EndpointSlice convergence",
        )
        raise TrafficSwitchUncertain("stable Service changed concurrently")
    evidence = _traffic_evidence(
        manifest=manifest,
        verification=verification,
        fencing_token=fencing_token,
        issuer_principal=evidence_issuer_principal,
        service_before=service_before,
        endpoint_slice_before=endpoint_slice_before,
        service_after=service_after,
        endpoint_slice_after=endpoint_slice_after,
    )
    return _commit_switch(
        registry,
        manifest=manifest,
        verification=verification,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        service_before=service_before,
        endpoint_slice_before=endpoint_slice_before,
        service_after=service_after,
        endpoint_slice_after=endpoint_slice_after,
        traffic_evidence=evidence,
    )

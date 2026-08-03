"""Atomically finalize Registry traffic facts after a proven Service switch."""

from __future__ import annotations

from dataclasses import dataclass, replace

from aigear.management.v2.records.release import (
    ReleaseOperationRecord,
    ReleasePhase,
    ServiceReleaseState,
)
from aigear.management.v2.records.runtime_evidence import RuntimeEvidenceKind
from aigear.management.v2.release_kubernetes import (
    KubernetesReleaseError,
    KubernetesReleasePort,
    ServiceTrafficPatch,
    StableServiceState,
)
from aigear.management.v2.release_lease import (
    heartbeat_release_lease,
    require_release_lease,
)
from aigear.management.v2.release_manifest import SignedReleaseManifest
from aigear.management.v2.traffic_switch import (
    TrafficSwitchResult,
    compute_traffic_switch_evidence_digest,
)

__all__ = [
    "ReleaseFinalizeError",
    "ReleaseFinalizeConflict",
    "ReleaseFinalizeUncertain",
    "ReleaseFinalizeResult",
    "finalize_release_traffic",
]


class ReleaseFinalizeError(ValueError):
    pass


class ReleaseFinalizeConflict(ReleaseFinalizeError):
    pass


class ReleaseFinalizeUncertain(ReleaseFinalizeError):
    pass


@dataclass(frozen=True)
class ReleaseFinalizeResult:
    operation: ReleaseOperationRecord
    service_state: ServiceReleaseState
    traffic_switch: TrafficSwitchResult


def _validate_switch(
    manifest: SignedReleaseManifest,
    traffic_switch: TrafficSwitchResult,
    *,
    operation: ReleaseOperationRecord,
    fencing_token: int,
) -> None:
    evidence = traffic_switch.traffic_evidence
    expected_digest = compute_traffic_switch_evidence_digest(
        manifest=manifest,
        fencing_token=fencing_token,
        service_before=traffic_switch.service_before,
        endpoint_slice_before=traffic_switch.endpoint_slice_before,
        service_after=traffic_switch.service_after,
        endpoint_slice_after=traffic_switch.endpoint_slice_after,
    )
    if (
        traffic_switch.operation.operation_id != operation.operation_id
        or traffic_switch.operation.fencing_token != fencing_token
        or traffic_switch.operation.traffic_evidence_id != evidence.evidence_id
        or operation.traffic_evidence_id != evidence.evidence_id
        or evidence.kind is not RuntimeEvidenceKind.TRAFFIC
        or evidence.release_id != manifest.release_id
        or evidence.fencing_token != fencing_token
        or evidence.payload_digest != expected_digest
        or traffic_switch.service_after.traffic_release_id != manifest.release_id
        or traffic_switch.service_after.fencing_token != fencing_token
        or operation.expected_service_resource_version
        != traffic_switch.service_after.resource_version
        or not traffic_switch.endpoint_slice_after.endpoints
        or any(
            not endpoint.ready or endpoint.release_id != manifest.release_id
            for endpoint in traffic_switch.endpoint_slice_after.endpoints
        )
    ):
        raise ReleaseFinalizeConflict(
            "traffic evidence is not bound to the fenced release switch"
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
        ):
            raise ReleaseFinalizeUncertain(
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


def _conditional_rollback(
    kubernetes: KubernetesReleasePort,
    traffic_switch: TrafficSwitchResult,
    *,
    fencing_token: int,
) -> StableServiceState | None:
    current = kubernetes.get_service(traffic_switch.service_after.service_name)
    if current != traffic_switch.service_after:
        return current
    try:
        kubernetes.patch_service_traffic(
            ServiceTrafficPatch(
                service_name=current.service_name,
                expected_uid=current.uid,
                expected_resource_version=current.resource_version,
                target_release_id=traffic_switch.service_before.traffic_release_id,
                fencing_token=fencing_token,
            )
        )
    except KubernetesReleaseError:
        return kubernetes.get_service(current.service_name)
    return kubernetes.get_service(current.service_name)


def _commit_finalize(
    registry,
    *,
    manifest: SignedReleaseManifest,
    traffic_switch: TrafficSwitchResult,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
) -> ReleaseFinalizeResult:
    def commit(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = tx.get_service_release_state(operation.service_name)
        evidence = tx.get_runtime_evidence(
            operation.service_name, traffic_switch.traffic_evidence.evidence_id
        )
        if (
            operation.phase is not ReleasePhase.SWITCHING_TRAFFIC
            or operation.target_release_id != manifest.release_id
            or operation.traffic_evidence_id != traffic_switch.traffic_evidence.evidence_id
            or state is None
            or state.revision != operation.expected_service_revision
            or state.active_operation_id != operation.operation_id
            or state.active_operation_phase is not ReleasePhase.SWITCHING_TRAFFIC
            or state.desired_release_id != manifest.release_id
            or state.observed_release_id != manifest.release_id
            or state.fencing_token != fencing_token
            or evidence != traffic_switch.traffic_evidence
        ):
            raise ReleaseFinalizeConflict(
                "Registry traffic facts changed before finalize"
            )
        previous = (
            state.previous_release_id
            if state.champion_release_id == manifest.release_id
            else state.champion_release_id
        )
        new_state = replace(
            state,
            revision=state.revision + 1,
            traffic_release_id=manifest.release_id,
            traffic_k8s_resource_version=(
                traffic_switch.service_after.resource_version
            ),
            champion_release_id=manifest.release_id,
            previous_release_id=previous,
            active_operation_phase=ReleasePhase.FINALIZING,
            updated_at=operation.updated_at,
        )
        finalized = replace(
            operation,
            phase=ReleasePhase.FINALIZING,
            revision=operation.revision + 1,
            expected_service_revision=new_state.revision,
            error_class=None,
            error_summary=None,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(finalized)
        tx.put_service_release_state(new_state)
        return ReleaseFinalizeResult(finalized, new_state, traffic_switch)

    return registry.run_atomic(commit)


def finalize_release_traffic(
    registry,
    kubernetes: KubernetesReleasePort,
    *,
    manifest: SignedReleaseManifest,
    traffic_switch: TrafficSwitchResult,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    lease_ttl_seconds: int = 120,
) -> ReleaseFinalizeResult:
    operation = heartbeat_release_lease(
        registry,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    failure: Exception | None = None
    try:
        if operation.phase is not ReleasePhase.SWITCHING_TRAFFIC:
            raise ReleaseFinalizeConflict("release operation is not finalizable")
        _validate_switch(
            manifest,
            traffic_switch,
            operation=operation,
            fencing_token=fencing_token,
        )
        current_service = kubernetes.get_service(manifest.core.service_name)
        current_endpoints = kubernetes.get_endpoint_slice(
            manifest.core.service_name
        )
        if (
            current_service != traffic_switch.service_after
            or current_endpoints != traffic_switch.endpoint_slice_after
        ):
            raise ReleaseFinalizeConflict(
                "Kubernetes traffic facts changed before finalize"
            )
        return _commit_finalize(
            registry,
            manifest=manifest,
            traffic_switch=traffic_switch,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
    except Exception as exc:
        failure = exc
    try:
        observed = _conditional_rollback(
            kubernetes,
            traffic_switch,
            fencing_token=fencing_token,
        )
    except KubernetesReleaseError:
        observed = None
    _mark_reconciling(
        registry,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        observed_service=observed,
        error_class=type(failure).__name__,
        error_summary="traffic finalize failed and conditional rollback was attempted",
    )
    raise ReleaseFinalizeUncertain(
        "release traffic finalize requires reconciliation"
    ) from failure

"""Create or recover one isolated, revisioned candidate Deployment."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from aigear.management.v2.release_kubernetes import (
    DeploymentCreateRequest,
    DeploymentState,
    KubernetesConfigReference,
    KubernetesMutationUncertain,
    KubernetesReleasePort,
    KubernetesResourceConflict,
)
from aigear.management.v2.release_lease import (
    heartbeat_release_lease,
    require_release_lease,
)
from aigear.management.v2.release_manifest import SignedReleaseManifest
from aigear.management.v2.records.release import (
    ReleaseOperationRecord,
    ReleasePhase,
    ServiceReleaseState,
)

__all__ = [
    "CandidateDeploymentError",
    "CandidateDeploymentConflict",
    "CandidateDeploymentUncertain",
    "CandidateDeploymentResult",
    "candidate_deployment_name",
    "deploy_release_candidate",
]


class CandidateDeploymentError(ValueError):
    pass


class CandidateDeploymentConflict(CandidateDeploymentError):
    pass


class CandidateDeploymentUncertain(CandidateDeploymentError):
    pass


@dataclass(frozen=True)
class CandidateDeploymentResult:
    operation: ReleaseOperationRecord
    service_state: ServiceReleaseState
    deployment: DeploymentState


def candidate_deployment_name(manifest: SignedReleaseManifest) -> str:
    if not isinstance(manifest, SignedReleaseManifest):
        raise CandidateDeploymentError("manifest must be SignedReleaseManifest")
    return f"release-{manifest.release_id.bare}"


def _request(
    manifest: SignedReleaseManifest,
    *,
    service_account_name: str,
    replicas: int,
    fencing_token: int,
) -> DeploymentCreateRequest:
    references = tuple(
        KubernetesConfigReference(
            kind=value.kind.value,
            name=value.name,
            version=value.version,
            content_digest=value.content_digest,
        )
        for value in manifest.core.config_references
    )
    return DeploymentCreateRequest(
        name=candidate_deployment_name(manifest),
        service_name=manifest.core.service_name,
        release_id=manifest.release_id,
        image_reference=manifest.core.image.image_reference,
        manifest_digest=manifest.release_id,
        deployment_spec_digest=manifest.core.deployment_spec_digest,
        service_account_name=service_account_name,
        config_references=references,
        startup_probe_path="/startupz",
        readiness_probe_path="/readyz",
        runtime_authorization_required=True,
        replicas=replicas,
        fencing_token=fencing_token,
    )


def _validate_registry_state(registry, manifest, operation):
    release = registry.get_release(manifest.release_id)
    state = registry.get_service_release_state(manifest.core.service_name)
    if (
        release is None
        or release.environment_fingerprint != manifest.core.environment_fingerprint
        or release.service_name != manifest.core.service_name
        or release.deployment_target_id != manifest.core.deployment_target_id
        or release.manifest_digest != manifest.release_id
        or release.signature_attestation_id != manifest.attestation.attestation_id
        or state is None
        or state.desired_release_id != manifest.release_id
        or state.active_operation_id != operation.operation_id
        or state.fencing_token != operation.fencing_token
    ):
        raise CandidateDeploymentConflict(
            "prepared Registry release or service state changed"
        )
    return state


def _mark_reconciling(
    registry,
    *,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    observed: DeploymentState | None,
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
            raise CandidateDeploymentConflict(
                "service state changed while marking reconciliation"
            )
        new_state = replace(
            state,
            revision=state.revision + 1,
            active_operation_phase=ReleasePhase.RECONCILING,
            updated_at=operation.updated_at,
        )
        reconciling = replace(
            operation,
            phase=ReleasePhase.RECONCILING,
            revision=operation.revision + 1,
            expected_service_revision=new_state.revision,
            expected_deployment_uid=(
                operation.expected_deployment_uid
                if observed is None
                else observed.uid
            ),
            expected_deployment_resource_version=(
                operation.expected_deployment_resource_version
                if observed is None
                else observed.resource_version
            ),
            error_class=error_class,
            error_summary=error_summary,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(reconciling)
        tx.put_service_release_state(new_state)

    registry.run_atomic(mark)


def _commit_deployed(
    registry,
    *,
    manifest: SignedReleaseManifest,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    deployment: DeploymentState,
) -> CandidateDeploymentResult:
    def commit(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = _validate_registry_state(tx, manifest, operation)
        if operation.phase not in {ReleasePhase.PREPARING, ReleasePhase.DEPLOYING}:
            raise CandidateDeploymentConflict("release operation is not deployable")
        if (
            operation.phase is ReleasePhase.DEPLOYING
            and operation.expected_deployment_uid == deployment.uid
            and operation.expected_deployment_resource_version
            == deployment.resource_version
        ):
            return CandidateDeploymentResult(operation, state, deployment)
        new_state = replace(
            state,
            revision=state.revision + 1,
            active_operation_phase=ReleasePhase.DEPLOYING,
            updated_at=operation.updated_at,
        )
        deployed = replace(
            operation,
            phase=ReleasePhase.DEPLOYING,
            revision=operation.revision + 1,
            expected_service_revision=new_state.revision,
            expected_deployment_uid=deployment.uid,
            expected_deployment_resource_version=deployment.resource_version,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(deployed)
        tx.put_service_release_state(new_state)
        return CandidateDeploymentResult(deployed, new_state, deployment)

    return registry.run_atomic(commit)


def deploy_release_candidate(
    registry,
    kubernetes: KubernetesReleasePort,
    *,
    manifest: SignedReleaseManifest,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    service_account_name: str,
    allowed_service_accounts: Sequence[str],
    replicas: int,
    lease_ttl_seconds: int = 120,
) -> CandidateDeploymentResult:
    allowed = frozenset(allowed_service_accounts)
    if not allowed or service_account_name not in allowed:
        raise CandidateDeploymentError(
            "candidate service account is not allowlisted"
        )
    operation = heartbeat_release_lease(
        registry,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    if operation.phase not in {ReleasePhase.PREPARING, ReleasePhase.DEPLOYING}:
        raise CandidateDeploymentConflict("release operation is not deployable")
    _validate_registry_state(registry, manifest, operation)
    request = _request(
        manifest,
        service_account_name=service_account_name,
        replicas=replicas,
        fencing_token=fencing_token,
    )
    stable_before = kubernetes.get_service(manifest.core.service_name)
    observed = kubernetes.get_deployment(request.name)
    if observed is not None and (
        observed.request.immutable_identity != request.immutable_identity
    ):
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            observed=observed,
            error_class="CandidateDeploymentConflict",
            error_summary="candidate name exists with different immutable spec",
        )
        raise CandidateDeploymentConflict(
            "candidate name exists with different immutable spec"
        )
    if observed is None:
        try:
            observed = kubernetes.create_deployment(request)
        except (KubernetesMutationUncertain, KubernetesResourceConflict) as exc:
            observed = kubernetes.get_deployment(request.name)
            if observed is None or (
                observed.request.immutable_identity != request.immutable_identity
            ):
                _mark_reconciling(
                    registry,
                    operation_id=operation_id,
                    owner_principal=owner_principal,
                    fencing_token=fencing_token,
                    observed=observed,
                    error_class=type(exc).__name__,
                    error_summary=(
                        "candidate create result could not be proven by exact read"
                    ),
                )
                raise CandidateDeploymentUncertain(
                    "candidate create result requires reconciliation"
                ) from exc
    if observed.request.immutable_identity != request.immutable_identity:
        raise CandidateDeploymentConflict("created candidate does not match request")
    if kubernetes.get_service(manifest.core.service_name) != stable_before:
        raise CandidateDeploymentConflict(
            "stable Service changed during isolated candidate creation"
        )
    return _commit_deployed(
        registry,
        manifest=manifest,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        deployment=observed,
    )

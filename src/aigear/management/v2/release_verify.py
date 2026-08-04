"""Verify one isolated release candidate before any traffic switch."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timezone
from typing import Sequence

from aigear.management.v2.attestation import AttestationVerifier, verify_attestation
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
    KubernetesReleaseError,
    KubernetesReleasePort,
    ProbeRequest,
    ProbeResult,
)
from aigear.management.v2.release_lease import (
    heartbeat_release_lease,
    require_release_lease,
)
from aigear.management.v2.release_manifest import SignedReleaseManifest
from aigear.management.v2.service_runtime import (
    ServiceRuntimeStartupResult,
    require_service_runtime_readiness,
)

__all__ = [
    "CandidateVerificationError",
    "CandidateVerificationFailed",
    "CandidateVerificationUncertain",
    "CandidateVerificationResult",
    "verify_release_candidate",
]


class CandidateVerificationError(ValueError):
    pass


class CandidateVerificationFailed(CandidateVerificationError):
    pass


class CandidateVerificationUncertain(CandidateVerificationError):
    pass


@dataclass(frozen=True)
class CandidateVerificationResult:
    operation: ReleaseOperationRecord
    service_state: ServiceReleaseState
    smoke_evidence: RuntimeEvidenceRecord


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
            state is None
            or state.revision != operation.expected_service_revision
            or state.active_operation_id != operation.operation_id
            or state.active_operation_phase is not operation.phase
        ):
            raise CandidateVerificationUncertain(
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


def _validate_startup(
    registry,
    *,
    manifest: SignedReleaseManifest,
    startup: ServiceRuntimeStartupResult,
    operation: ReleaseOperationRecord,
    runtime_attestation_verifier: AttestationVerifier,
    runtime_key_versions: Sequence[str],
    non_runtime_key_versions: Sequence[str],
    renewal_margin_seconds: int,
) -> None:
    runtime_keys = frozenset(runtime_key_versions)
    other_keys = frozenset(non_runtime_key_versions)
    if (
        not runtime_keys
        or not other_keys
        or runtime_keys & other_keys
        or startup.observed_attestation.key_version not in runtime_keys
    ):
        raise CandidateVerificationFailed(
            "runtime observed attestation key is not independently allowlisted"
        )
    try:
        verify_attestation(startup.observed_attestation, runtime_attestation_verifier)
    except ValueError as exc:
        raise CandidateVerificationFailed(
            "runtime observed attestation is invalid"
        ) from exc
    evidence = startup.observed_evidence
    subject = startup.observed_attestation.unsigned_envelope.get("subject", {})
    if (
        startup.observed_attestation.attestation_kind != "runtime_observed"
        or startup.observed_attestation.environment_fingerprint
        != manifest.core.environment_fingerprint
        or evidence.kind is not RuntimeEvidenceKind.POD_OBSERVED
        or evidence.release_id != manifest.release_id
        or evidence.pod_uid != startup.lease.pod_uid
        or evidence.fencing_token != operation.fencing_token
        or startup.lease.release_id != manifest.release_id
        or subject.get("evidence_id") != evidence.evidence_id.typed
        or subject.get("release_id") != manifest.release_id.typed
        or subject.get("pod_uid") != evidence.pod_uid
        or subject.get("runtime_lease_id") != startup.lease.lease_id.typed
        or subject.get("fencing_token") != operation.fencing_token
    ):
        raise CandidateVerificationFailed(
            "runtime observed evidence is not bound to the fenced candidate"
        )
    at = registry.get_server_read_time().astimezone(timezone.utc)
    try:
        require_service_runtime_readiness(
            registry,
            startup,
            at=at,
            renewal_margin_seconds=renewal_margin_seconds,
        )
    except ValueError as exc:
        raise CandidateVerificationFailed(
            "candidate runtime authorization is not readiness-safe"
        ) from exc


def _expected_asset_ids(manifest: SignedReleaseManifest) -> tuple:
    return tuple(
        sorted(
            (value.asset_version_id for value in manifest.core.assets),
            key=lambda value: value.typed,
        )
    )


def _validate_probe(
    manifest: SignedReleaseManifest, result: ProbeResult
) -> None:
    if not result.passed:
        raise CandidateVerificationFailed("candidate smoke contract failed")
    if (
        result.release_id != manifest.release_id
        or result.image_digest != manifest.core.image.image_digest
        or result.asset_version_ids != _expected_asset_ids(manifest)
        or result.runtime_contract_digest
        != manifest.core.runtime_contract.shape_contract_digest
    ):
        raise CandidateVerificationFailed(
            "candidate smoke response identity does not match the release"
        )


def _smoke_evidence(
    manifest: SignedReleaseManifest,
    startup: ServiceRuntimeStartupResult,
    result: ProbeResult,
    *,
    issuer_principal: str,
    fencing_token: int,
) -> RuntimeEvidenceRecord:
    observed = startup.observed_evidence
    binding_tuple = tuple(
        sorted(
            (
                *observed.binding_tuple,
                f"smoke={result.evidence_digest.typed}",
            )
        )
    )
    return RuntimeEvidenceRecord(
        schema_version="2.0",
        environment_fingerprint=manifest.core.environment_fingerprint,
        evidence_id=compute_runtime_evidence_id(
            kind=RuntimeEvidenceKind.SMOKE,
            release_id=manifest.release_id,
            pod_uid=observed.pod_uid,
            k8s_resource_version=observed.k8s_resource_version,
            binding_tuple=binding_tuple,
            fencing_token=fencing_token,
            payload_digest=result.evidence_digest,
        ),
        kind=RuntimeEvidenceKind.SMOKE,
        release_id=manifest.release_id,
        pod_uid=observed.pod_uid,
        k8s_resource_version=observed.k8s_resource_version,
        binding_tuple=binding_tuple,
        policy_decision_epoch=observed.policy_decision_epoch,
        security_watermark=startup.lease.security_watermark,
        fencing_token=fencing_token,
        issuer_principal=issuer_principal,
        payload_digest=result.evidence_digest,
        issued_at=startup.lease.issued_at,
        expires_at=startup.lease.expires_at,
    )


def _commit_verified(
    registry,
    *,
    manifest: SignedReleaseManifest,
    startup: ServiceRuntimeStartupResult,
    smoke_evidence: RuntimeEvidenceRecord,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
) -> CandidateVerificationResult:
    def commit(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        state = tx.get_service_release_state(operation.service_name)
        if (
            operation.target_release_id != manifest.release_id
            or operation.phase not in {ReleasePhase.DEPLOYING, ReleasePhase.VERIFYING}
            or state is None
            or state.revision != operation.expected_service_revision
            or state.desired_release_id != manifest.release_id
            or state.active_operation_id != operation.operation_id
            or state.fencing_token != fencing_token
        ):
            raise CandidateVerificationUncertain(
                "Registry release state changed before evidence commit"
            )
        tx.put_attestation(startup.observed_attestation)
        tx.put_runtime_evidence(operation.service_name, startup.observed_evidence)
        tx.put_runtime_evidence(operation.service_name, smoke_evidence)
        if (
            operation.phase is ReleasePhase.VERIFYING
            and state.observed_release_id == manifest.release_id
        ):
            return CandidateVerificationResult(operation, state, smoke_evidence)
        new_state = replace(
            state,
            revision=state.revision + 1,
            observed_release_id=manifest.release_id,
            observed_evidence_revision=state.observed_evidence_revision + 1,
            active_operation_phase=ReleasePhase.VERIFYING,
            security_watermark=startup.lease.security_watermark,
            updated_at=operation.updated_at,
        )
        verified = replace(
            operation,
            phase=ReleasePhase.VERIFYING,
            revision=operation.revision + 1,
            expected_service_revision=new_state.revision,
            error_class=None,
            error_summary=None,
            updated_at=operation.updated_at,
        )
        tx.put_release_operation(verified)
        tx.put_service_release_state(new_state)
        return CandidateVerificationResult(verified, new_state, smoke_evidence)

    return registry.run_atomic(commit)


def verify_release_candidate(
    registry,
    kubernetes: KubernetesReleasePort,
    *,
    manifest: SignedReleaseManifest,
    startup: ServiceRuntimeStartupResult,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    runtime_attestation_verifier: AttestationVerifier,
    runtime_key_versions: Sequence[str],
    non_runtime_key_versions: Sequence[str],
    evidence_issuer_principal: str,
    renewal_margin_seconds: int = 30,
    lease_ttl_seconds: int = 120,
) -> CandidateVerificationResult:
    operation = heartbeat_release_lease(
        registry,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    if (
        operation.phase not in {ReleasePhase.DEPLOYING, ReleasePhase.VERIFYING}
        or operation.target_release_id != manifest.release_id
        or operation.expected_deployment_uid is None
    ):
        raise CandidateVerificationFailed("release operation is not verifiable")
    stable_before = kubernetes.get_service(manifest.core.service_name)
    try:
        _validate_startup(
            registry,
            manifest=manifest,
            startup=startup,
            operation=operation,
            runtime_attestation_verifier=runtime_attestation_verifier,
            runtime_key_versions=runtime_key_versions,
            non_runtime_key_versions=non_runtime_key_versions,
            renewal_margin_seconds=renewal_margin_seconds,
        )
        result = kubernetes.probe(
            ProbeRequest(
                service_name=manifest.core.service_name,
                release_id=manifest.release_id,
                pod_uid=startup.observed_evidence.pod_uid,
                fencing_token=fencing_token,
            )
        )
        _validate_probe(manifest, result)
    except KubernetesReleaseError as exc:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            error_class=type(exc).__name__,
            error_summary="candidate smoke result could not be determined",
        )
        raise CandidateVerificationUncertain(
            "candidate smoke result requires reconciliation"
        ) from exc
    except CandidateVerificationFailed as exc:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            error_class=type(exc).__name__,
            error_summary=str(exc),
        )
        raise
    if kubernetes.get_service(manifest.core.service_name) != stable_before:
        _mark_reconciling(
            registry,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            error_class="StableServiceChanged",
            error_summary="stable Service changed during candidate verification",
        )
        raise CandidateVerificationUncertain(
            "stable Service changed during candidate verification"
        )
    smoke_evidence = _smoke_evidence(
        manifest,
        startup,
        result,
        issuer_principal=evidence_issuer_principal,
        fencing_token=fencing_token,
    )
    return _commit_verified(
        registry,
        manifest=manifest,
        startup=startup,
        smoke_evidence=smoke_evidence,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=fencing_token,
    )

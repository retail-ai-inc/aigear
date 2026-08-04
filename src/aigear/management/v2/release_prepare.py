"""Resolve, verify, and atomically prepare one immutable service release."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Mapping, Optional, Sequence

from aigear.management.v2.attestation import AttestationVerifier
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.release_lease import (
    ReleaseExternalState,
    acquire_release_lease,
    heartbeat_release_lease,
    require_release_lease,
)
from aigear.management.v2.release_manifest import (
    ReleaseImageBinding,
    RuntimeContract,
    SignedReleaseManifest,
    verify_release_manifest,
)
from aigear.management.v2.resolver import Selector, UsageContext, resolve
from aigear.management.v2.records.release import (
    ReleaseOperationRecord,
    ReleasePhase,
    ReleaseRecord,
    ServiceReleaseState,
)

__all__ = [
    "ReleasePrepareError",
    "ReleasePrepareConflict",
    "ReleasePrepareResult",
    "prepare_release",
]


class ReleasePrepareError(ValueError):
    pass


class ReleasePrepareConflict(ReleasePrepareError):
    pass


@dataclass(frozen=True)
class ReleasePrepareResult:
    operation: ReleaseOperationRecord
    release: ReleaseRecord
    service_state: ServiceReleaseState


def _resolve_assets(
    registry,
    *,
    control: ControlDocument,
    layout: GcsLayoutV2,
    manifest: SignedReleaseManifest,
    now: datetime,
    verifier: AttestationVerifier,
) -> Mapping[str, object]:
    handles = {}
    for binding in manifest.core.assets:
        handles[binding.binding_name] = resolve(
            registry,
            control,
            layout,
            Selector.by_asset_version(binding.asset_version_id),
            UsageContext.RELEASE,
            now,
            attestation_verifier=verifier,
            required_policy_version=binding.policy_version,
        )
    return handles


def prepare_release(
    registry,
    *,
    control: ControlDocument,
    layout: GcsLayoutV2,
    manifest: SignedReleaseManifest,
    service_name: str,
    deployment_target_id: str,
    expected_runtime_contract: RuntimeContract,
    asset_attestation_verifier: AttestationVerifier,
    release_attestation_verifier: AttestationVerifier,
    release_key_versions: Sequence[str],
    non_release_key_versions: Sequence[str],
    verify_current_image: Callable[[ReleaseImageBinding, datetime], None],
    idempotency_key: str,
    owner_principal: str,
    read_external_state: Optional[Callable[[str], ReleaseExternalState]] = None,
    lease_ttl_seconds: int = 120,
) -> ReleasePrepareResult:
    """Reserve first, verify outside Firestore, then commit only Registry facts."""

    if not isinstance(control, ControlDocument) or control.authority != "v2":
        raise ReleasePrepareError("an authoritative V2 control document is required")
    if not isinstance(manifest, SignedReleaseManifest):
        raise ReleasePrepareError("manifest must be a SignedReleaseManifest")
    if (
        manifest.core.environment_fingerprint != control.environment_fingerprint
        or manifest.core.service_name != service_name
        or manifest.core.deployment_target_id != deployment_target_id
    ):
        raise ReleasePrepareConflict(
            "release does not match the controlled environment or service target"
        )

    operation = acquire_release_lease(
        registry,
        environment_fingerprint=control.environment_fingerprint,
        service_name=service_name,
        target_release_id=manifest.release_id,
        idempotency_key=idempotency_key,
        owner_principal=owner_principal,
        read_external_state=read_external_state,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    operation = heartbeat_release_lease(
        registry,
        operation_id=operation.operation_id,
        owner_principal=owner_principal,
        fencing_token=operation.fencing_token,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    verification_time = datetime.fromisoformat(operation.updated_at)
    try:
        if not callable(verify_current_image):
            raise ReleasePrepareError("current image policy verifier is required")
        verify_current_image(manifest.core.image, verification_time)
        resolved_assets = _resolve_assets(
            registry,
            control=control,
            layout=layout,
            manifest=manifest,
            now=verification_time,
            verifier=asset_attestation_verifier,
        )
        verify_release_manifest(
            manifest,
            verifier=release_attestation_verifier,
            release_key_versions=release_key_versions,
            non_release_key_versions=non_release_key_versions,
            expected_environment_fingerprint=control.environment_fingerprint,
            expected_service_name=service_name,
            expected_deployment_target_id=deployment_target_id,
            expected_runtime_contract=expected_runtime_contract,
            resolved_assets=resolved_assets,
            now=verification_time,
        )
    except ValueError as exc:
        raise ReleasePrepareError("release verification failed") from exc

    runner = getattr(registry, "run_atomic", None)
    if not callable(runner):
        raise ReleasePrepareError("Registry lacks the required atomic transaction boundary")

    def commit(tx):
        current = require_release_lease(
            tx,
            operation_id=operation.operation_id,
            owner_principal=owner_principal,
            fencing_token=operation.fencing_token,
        )
        if (
            current.target_release_id != manifest.release_id
            or current.phase not in {ReleasePhase.RESERVED, ReleasePhase.PREPARING}
        ):
            raise ReleasePrepareConflict("release operation is not preparable")
        get_control = getattr(tx, "get_control_document", None)
        if not callable(get_control) or get_control() != control:
            raise ReleasePrepareConflict(
                "control/binding/write epoch changed before release commit"
            )
        state = tx.get_service_release_state(service_name)
        if (
            state is None
            or state.active_operation_id != current.operation_id
            or state.fencing_token != current.fencing_token
            or state.revision != current.expected_service_revision
        ):
            raise ReleasePrepareConflict("service release revision or fence changed")

        existing = tx.get_release(manifest.release_id)
        counter = state.display_version_counter
        if existing is None:
            counter += 1
            release = manifest.to_release_record(
                creation_operation_id=current.operation_id,
                display_version=f"service-v{counter}",
                created_at=current.updated_at,
            )
            tx.put_release(release)
        else:
            candidate = manifest.to_release_record(
                creation_operation_id=existing.creation_operation_id,
                display_version=existing.display_version,
                created_at=existing.created_at,
            )
            if existing.immutable_identity != candidate.immutable_identity:
                raise ReleasePrepareConflict(
                    "release ID has conflicting immutable Registry content"
                )
            release = existing

        already_prepared = (
            current.phase is ReleasePhase.PREPARING
            and state.desired_release_id == manifest.release_id
        )
        if already_prepared:
            return ReleasePrepareResult(current, release, state)

        desired_changed = state.desired_release_id != manifest.release_id
        new_state = replace(
            state,
            revision=state.revision + 1,
            display_version_counter=counter,
            desired_release_id=manifest.release_id,
            desired_revision=state.desired_revision + int(desired_changed),
            active_operation_phase=ReleasePhase.PREPARING,
            updated_at=current.updated_at,
        )
        prepared = replace(
            current,
            phase=ReleasePhase.PREPARING,
            revision=current.revision + 1,
            expected_service_revision=new_state.revision,
            updated_at=current.updated_at,
        )
        tx.put_release_operation(prepared)
        tx.put_service_release_state(new_state)
        return ReleasePrepareResult(prepared, release, new_state)

    return runner(commit)

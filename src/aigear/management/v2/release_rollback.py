"""Start an exact rollback by reusing the normal release preparation Saga."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from aigear.management.v2.attestation import AttestationVerifier
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.release import ReleaseRecord
from aigear.management.v2.release_lease import ReleaseExternalState
from aigear.management.v2.release_manifest import (
    ImmutableConfigReference,
    ReleaseImageBinding,
    RuntimeContract,
    SignedReleaseManifest,
)
from aigear.management.v2.release_prepare import (
    ReleasePrepareResult,
    prepare_release,
)

__all__ = [
    "ReleaseRollbackError",
    "ReleaseRollbackConflict",
    "ReleaseRollbackResult",
    "rollback_release",
]


class ReleaseRollbackError(ValueError):
    pass


class ReleaseRollbackConflict(ReleaseRollbackError):
    pass


@dataclass(frozen=True)
class ReleaseRollbackResult:
    from_release_id: TypedId
    target_release: ReleaseRecord
    prepared: ReleasePrepareResult


def _server_time(registry) -> datetime:
    value = registry.get_server_read_time()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ReleaseRollbackError(
            "Registry server read time must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def rollback_release(
    registry,
    *,
    control: ControlDocument,
    layout: GcsLayoutV2,
    manifest: SignedReleaseManifest,
    service_name: str,
    deployment_target_id: str,
    expected_service_revision: int,
    expected_runtime_contract: RuntimeContract,
    asset_attestation_verifier: AttestationVerifier,
    release_attestation_verifier: AttestationVerifier,
    release_key_versions: Sequence[str],
    non_release_key_versions: Sequence[str],
    verify_current_image: Callable[[ReleaseImageBinding, datetime], None],
    verify_current_config_reference: Callable[
        [ImmutableConfigReference, datetime], None
    ],
    idempotency_key: str,
    owner_principal: str,
    target_release_id: Optional[TypedId] = None,
    read_external_state: Optional[
        Callable[[str], ReleaseExternalState]
    ] = None,
    lease_ttl_seconds: int = 120,
) -> ReleaseRollbackResult:
    state = registry.get_service_release_state(service_name)
    if state is None or state.revision != expected_service_revision:
        raise ReleaseRollbackConflict(
            "service release state does not match expected revision"
        )
    if state.champion_release_id is None:
        raise ReleaseRollbackConflict("service has no current champion release")
    target_id = state.previous_release_id if target_release_id is None else target_release_id
    if target_id is None:
        raise ReleaseRollbackConflict("service has no previous release to roll back to")
    if target_id == state.champion_release_id:
        raise ReleaseRollbackConflict("rollback target is already the champion")
    if not isinstance(manifest, SignedReleaseManifest) or manifest.release_id != target_id:
        raise ReleaseRollbackConflict(
            "rollback manifest does not match the exact target release"
        )
    target = registry.get_release(target_id)
    if target is None:
        raise ReleaseRollbackConflict("rollback target release is not registered")
    candidate = manifest.to_release_record(
        creation_operation_id=target.creation_operation_id,
        display_version=target.display_version,
        created_at=target.created_at,
    )
    if target.immutable_identity != candidate.immutable_identity:
        raise ReleaseRollbackConflict(
            "rollback manifest conflicts with the registered release"
        )
    if not callable(verify_current_image) or not callable(
        verify_current_config_reference
    ):
        raise ReleaseRollbackError(
            "current image and config verifiers are required"
        )
    verification_time = _server_time(registry)
    try:
        verify_current_image(manifest.core.image, verification_time)
        for reference in manifest.core.config_references:
            verify_current_config_reference(reference, verification_time)
    except ValueError as exc:
        raise ReleaseRollbackError(
            "rollback image, config, or secret is no longer allowed"
        ) from exc
    try:
        prepared = prepare_release(
            registry,
            control=control,
            layout=layout,
            manifest=manifest,
            service_name=service_name,
            deployment_target_id=deployment_target_id,
            expected_runtime_contract=expected_runtime_contract,
            asset_attestation_verifier=asset_attestation_verifier,
            release_attestation_verifier=release_attestation_verifier,
            release_key_versions=release_key_versions,
            non_release_key_versions=non_release_key_versions,
            idempotency_key=idempotency_key,
            owner_principal=owner_principal,
            read_external_state=read_external_state,
            lease_ttl_seconds=lease_ttl_seconds,
        )
    except ValueError as exc:
        raise ReleaseRollbackError(
            "rollback target failed current release preparation"
        ) from exc
    return ReleaseRollbackResult(
        from_release_id=state.champion_release_id,
        target_release=target,
        prepared=prepared,
    )

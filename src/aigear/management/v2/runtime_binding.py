"""Canonical runtime bindings and current Registry revalidation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from aigear.management.v2.attestation import AttestationVerifier
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import LifecycleState, TrustState
from aigear.management.v2.records.blob import AvailabilityState
from aigear.management.v2.records.policy import require_effective_policy_head
from aigear.management.v2.release_manifest import SignedReleaseManifest
from aigear.management.v2.resolver import (
    ResolvedHandle,
    Selector,
    UsageContext,
    resolve,
)
from aigear.management.v2.revocation import (
    RevocationUsage,
    require_non_revoked_policy_head,
)

__all__ = [
    "RuntimeBindingError",
    "RuntimeBindingConflict",
    "VerifiedJournalWatermark",
    "compute_runtime_binding_digest",
    "resolve_runtime_assets",
    "revalidate_current_runtime_bindings",
]


_MAX_JOURNAL_FRESHNESS_SECONDS = 15 * 60


class RuntimeBindingError(ValueError):
    pass


class RuntimeBindingConflict(RuntimeBindingError):
    pass


def _aware_utc(field_name: str, value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise RuntimeBindingError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class VerifiedJournalWatermark:
    security_watermark: int
    head_entry_id: TypedId
    verified_at: datetime
    fresh_for_seconds: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.security_watermark, bool)
            or not isinstance(self.security_watermark, int)
            or self.security_watermark < 0
        ):
            raise RuntimeBindingError(
                "security_watermark must be a non-negative int"
            )
        if not isinstance(self.head_entry_id, TypedId):
            raise RuntimeBindingError("head_entry_id must be a TypedId")
        object.__setattr__(
            self, "verified_at", _aware_utc("verified_at", self.verified_at)
        )
        if (
            isinstance(self.fresh_for_seconds, bool)
            or not isinstance(self.fresh_for_seconds, int)
            or not 1
            <= self.fresh_for_seconds
            <= _MAX_JOURNAL_FRESHNESS_SECONDS
        ):
            raise RuntimeBindingError(
                "fresh_for_seconds must be between 1 and 900"
            )

    @property
    def fresh_until(self) -> datetime:
        return self.verified_at + timedelta(seconds=self.fresh_for_seconds)


def _binding_values(
    manifest: SignedReleaseManifest,
    handles: Mapping[str, ResolvedHandle],
    journal: VerifiedJournalWatermark,
) -> list:
    assets = []
    for binding in manifest.core.assets:
        handle = handles[binding.binding_name]
        assets.append(
            {
                "binding_name": binding.binding_name,
                "asset_version_id": handle.asset_version_id.typed,
                "asset_record_revision": handle.asset_record_revision,
                "policy_attestation_id": handle.policy_decision_head_ref.typed,
                "policy_decision_epoch": handle.policy_decision_epoch,
                "policy_version": handle.policy_version,
                "policy_valid_until": handle.policy_valid_until,
                "blobs": [
                    {
                        "role": blob.role,
                        "logical_name": blob.logical_name,
                        "blob_id": blob.blob_id.typed,
                        "generation": blob.generation,
                        "location_revision": blob.location_revision,
                        "location_attestation_id": blob.location_attestation_ref.typed,
                    }
                    for blob in handle.blobs
                ],
            }
        )
    return [
        "aigear.runtime-binding.v2",
        manifest.release_id.typed,
        manifest.core.image.image_digest.typed,
        manifest.core.deployment_spec_digest.typed,
        assets,
        journal.security_watermark,
        journal.head_entry_id.typed,
    ]


def compute_runtime_binding_digest(
    manifest: SignedReleaseManifest,
    handles: Mapping[str, ResolvedHandle],
    journal: VerifiedJournalWatermark,
) -> TypedId:
    if not isinstance(manifest, SignedReleaseManifest):
        raise RuntimeBindingError("manifest must be a SignedReleaseManifest")
    if set(handles) != {binding.binding_name for binding in manifest.core.assets}:
        raise RuntimeBindingError("runtime handle binding set is incomplete")
    return TypedId.from_bare(
        digest_sha256_of_jcs(_binding_values(manifest, handles, journal))
    )


def resolve_runtime_assets(
    registry,
    *,
    control: ControlDocument,
    layout: GcsLayoutV2,
    manifest: SignedReleaseManifest,
    at: datetime,
    verifier: AttestationVerifier,
) -> dict[str, ResolvedHandle]:
    return {
        binding.binding_name: resolve(
            registry,
            control,
            layout,
            Selector.by_asset_version(binding.asset_version_id),
            UsageContext.SERVICE_RUNTIME,
            at,
            attestation_verifier=verifier,
            required_policy_version=binding.policy_version,
        )
        for binding in manifest.core.assets
    }


def revalidate_current_runtime_bindings(
    registry,
    *,
    handles: Mapping[str, ResolvedHandle],
    at: datetime,
) -> None:
    for handle in handles.values():
        asset = registry.get_asset_version(handle.asset_version_id)
        if (
            asset is None
            or asset.record_revision != handle.asset_record_revision
            or asset.policy_decision_head_ref != handle.policy_decision_head_ref
            or asset.lifecycle_state is not LifecycleState.ACTIVE
            or asset.trust_state is not TrustState.APPROVED
        ):
            raise RuntimeBindingConflict("asset binding changed before lease commit")
        head = registry.get_policy_decision_head(handle.asset_version_id)
        try:
            require_non_revoked_policy_head(
                head, usage=RevocationUsage.RUNTIME_LEASE
            )
            require_effective_policy_head(
                head,
                at=at.isoformat(),
                required_policy_version=handle.policy_version,
            )
        except ValueError as exc:
            raise RuntimeBindingConflict(
                "runtime policy head is no longer effective"
            ) from exc
        if (
            head.attestation_id != handle.policy_decision_head_ref
            or head.current_epoch != handle.policy_decision_epoch
            or head.valid_until != handle.policy_valid_until
        ):
            raise RuntimeBindingConflict("runtime policy binding changed")
        for blob_handle in handle.blobs:
            blob = registry.get_blob(blob_handle.blob_id)
            if (
                blob is None
                or blob.availability_state is not AvailabilityState.READY
                or blob.generation != blob_handle.generation
                or blob.current_location_revision != blob_handle.location_revision
                or blob.current_location_attestation_ref
                != blob_handle.location_attestation_ref
            ):
                raise RuntimeBindingConflict("runtime blob location changed")

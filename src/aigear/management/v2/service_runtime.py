"""Fail-closed Pod startup verification for an exact service release."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence, Tuple

from aigear.management.v2.attestation import (
    AttestationRecord,
    AttestationVerifier,
    DigestSigner,
    create_attestation,
)
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.runtime_evidence import (
    RuntimeAuthorizationLease,
    RuntimeEvidenceKind,
    RuntimeEvidenceRecord,
    compute_runtime_evidence_id,
)
from aigear.management.v2.release_kubernetes import KubernetesConfigReference
from aigear.management.v2.release_lease import require_release_lease
from aigear.management.v2.release_manifest import (
    ReleaseBlobBinding,
    RuntimeContract,
    SignedReleaseManifest,
)
from aigear.management.v2.runtime_authorization import (
    VerifiedJournalWatermark,
    issue_runtime_authorization,
    require_runtime_readiness,
)

__all__ = [
    "ServiceRuntimeError",
    "LoadedAssetObservation",
    "PodStartupObservation",
    "ServiceRuntimeStartupResult",
    "verify_service_runtime_startup",
    "require_service_runtime_readiness",
]


_OBSERVED_ATTESTATION_KIND = "runtime_observed"


class ServiceRuntimeError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedAssetObservation:
    binding_name: str
    asset_version_id: TypedId
    schema_contract_digest: TypedId
    runtime_contract_digest: TypedId
    blobs: Tuple[ReleaseBlobBinding, ...]


@dataclass(frozen=True)
class PodStartupObservation:
    pod_uid: str
    pod_resource_version: str
    deployment_uid: str
    deployment_resource_version: str
    release_id: TypedId
    image_digest: TypedId
    deployment_spec_digest: TypedId
    loaded_assets: Tuple[LoadedAssetObservation, ...]
    config_references: Tuple[KubernetesConfigReference, ...]
    runtime_contract: RuntimeContract
    fencing_token: int

    def __post_init__(self) -> None:
        for field_name in (
            "pod_uid",
            "pod_resource_version",
            "deployment_uid",
            "deployment_resource_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ServiceRuntimeError(f"{field_name} must be a non-empty str")
        if isinstance(self.loaded_assets, list):
            object.__setattr__(self, "loaded_assets", tuple(self.loaded_assets))
        if isinstance(self.config_references, list):
            object.__setattr__(
                self, "config_references", tuple(self.config_references)
            )
        asset_names = [value.binding_name for value in self.loaded_assets]
        if asset_names != sorted(asset_names) or len(set(asset_names)) != len(asset_names):
            raise ServiceRuntimeError("loaded assets must be sorted and unique")
        config_keys = [(value.kind, value.name) for value in self.config_references]
        if config_keys != sorted(config_keys) or len(set(config_keys)) != len(config_keys):
            raise ServiceRuntimeError("config references must be sorted and unique")
        if isinstance(self.fencing_token, bool) or self.fencing_token < 1:
            raise ServiceRuntimeError("fencing_token must be positive")


@dataclass(frozen=True)
class ServiceRuntimeStartupResult:
    lease: RuntimeAuthorizationLease
    observed_evidence: RuntimeEvidenceRecord
    observed_attestation: AttestationRecord


def _expected_config_references(manifest: SignedReleaseManifest) -> tuple:
    return tuple(
        KubernetesConfigReference(
            kind=value.kind.value,
            name=value.name,
            version=value.version,
            content_digest=value.content_digest,
        )
        for value in manifest.core.config_references
    )


def _expected_loaded_assets(manifest: SignedReleaseManifest) -> tuple:
    return tuple(
        LoadedAssetObservation(
            binding_name=value.binding_name,
            asset_version_id=value.asset_version_id,
            schema_contract_digest=value.schema_contract_digest,
            runtime_contract_digest=value.runtime_contract_digest,
            blobs=value.blobs,
        )
        for value in manifest.core.assets
    )


def _validate_observation(
    manifest: SignedReleaseManifest,
    observation: PodStartupObservation,
) -> None:
    if not isinstance(observation, PodStartupObservation):
        raise ServiceRuntimeError("observation must be PodStartupObservation")
    if (
        observation.release_id != manifest.release_id
        or observation.image_digest != manifest.core.image.image_digest
        or observation.deployment_spec_digest
        != manifest.core.deployment_spec_digest
    ):
        raise ServiceRuntimeError("release, image, or deployment digest drifted")
    if observation.runtime_contract != manifest.core.runtime_contract:
        raise ServiceRuntimeError("runtime ABI/schema/shape contract drifted")
    if observation.loaded_assets != _expected_loaded_assets(manifest):
        raise ServiceRuntimeError("loaded asset bundle is missing or drifted")
    if observation.config_references != _expected_config_references(manifest):
        raise ServiceRuntimeError(
            "immutable config or secret reference is missing or drifted"
        )


def _binding_tuple(
    manifest: SignedReleaseManifest,
    observation: PodStartupObservation,
) -> tuple[str, ...]:
    values = [
        f"release={manifest.release_id.typed}",
        f"image={observation.image_digest.typed}",
        f"runtime={observation.runtime_contract.shape_contract_digest.typed}",
    ]
    for asset in observation.loaded_assets:
        values.append(f"asset:{asset.binding_name}={asset.asset_version_id.typed}")
        for blob in asset.blobs:
            values.append(
                f"blob:{asset.binding_name}:{blob.role}:{blob.logical_name}="
                f"{blob.blob_id.typed}@{blob.generation}:{blob.location_revision}"
            )
    for value in observation.config_references:
        values.append(
            f"config:{value.kind}:{value.name}={value.version}@"
            f"{value.content_digest.typed}"
        )
    return tuple(sorted(values))


def _observed_subject(
    manifest: SignedReleaseManifest,
    observation: PodStartupObservation,
    lease: RuntimeAuthorizationLease,
    binding_tuple: tuple[str, ...],
) -> dict:
    return {
        "release_id": manifest.release_id.typed,
        "pod_uid": observation.pod_uid,
        "pod_resource_version": observation.pod_resource_version,
        "deployment_uid": observation.deployment_uid,
        "deployment_resource_version": observation.deployment_resource_version,
        "image_digest": observation.image_digest.typed,
        "deployment_spec_digest": observation.deployment_spec_digest.typed,
        "binding_tuple": list(binding_tuple),
        "runtime_contract": observation.runtime_contract.to_jcs_dict(),
        "runtime_lease_id": lease.lease_id.typed,
        "runtime_lease_expires_at": lease.expires_at,
        "policy_attestation_ids": [
            value.typed for value in lease.policy_attestation_ids
        ],
        "security_watermark": lease.security_watermark,
        "fencing_token": observation.fencing_token,
    }


def _runtime_key_allowlist(
    signer: DigestSigner,
    runtime_key_versions: Sequence[str],
    non_runtime_key_versions: Sequence[str],
) -> None:
    runtime_keys = frozenset(runtime_key_versions)
    other_keys = frozenset(non_runtime_key_versions)
    if (
        not runtime_keys
        or not other_keys
        or runtime_keys & other_keys
        or signer.key_version not in runtime_keys
    ):
        raise ServiceRuntimeError(
            "runtime evidence signer must be allowlisted and independent"
        )


def verify_service_runtime_startup(
    registry,
    *,
    control: ControlDocument,
    layout: GcsLayoutV2,
    manifest: SignedReleaseManifest,
    observation: PodStartupObservation,
    operation_id: str,
    owner_principal: str,
    expected_runtime_contract: RuntimeContract,
    asset_attestation_verifier: AttestationVerifier,
    release_attestation_verifier: AttestationVerifier,
    release_key_versions: Sequence[str],
    non_release_key_versions: Sequence[str],
    journal: VerifiedJournalWatermark,
    lease_issuer_principal: str,
    evidence_signer: DigestSigner,
    runtime_key_versions: Sequence[str],
    non_runtime_key_versions: Sequence[str],
    max_runtime_ttl_seconds: int = 300,
) -> ServiceRuntimeStartupResult:
    _validate_observation(manifest, observation)
    operation = require_release_lease(
        registry,
        operation_id=operation_id,
        owner_principal=owner_principal,
        fencing_token=observation.fencing_token,
    )
    if (
        operation.target_release_id != manifest.release_id
        or operation.expected_deployment_uid != observation.deployment_uid
    ):
        raise ServiceRuntimeError("Pod does not belong to the fenced candidate")
    _runtime_key_allowlist(
        evidence_signer, runtime_key_versions, non_runtime_key_versions
    )
    try:
        lease = issue_runtime_authorization(
            registry,
            control=control,
            layout=layout,
            manifest=manifest,
            service_name=manifest.core.service_name,
            deployment_target_id=manifest.core.deployment_target_id,
            pod_uid=observation.pod_uid,
            expected_runtime_contract=expected_runtime_contract,
            asset_attestation_verifier=asset_attestation_verifier,
            release_attestation_verifier=release_attestation_verifier,
            release_key_versions=release_key_versions,
            non_release_key_versions=non_release_key_versions,
            journal=journal,
            issuer_principal=lease_issuer_principal,
            max_ttl_seconds=max_runtime_ttl_seconds,
        )
    except ValueError as exc:
        raise ServiceRuntimeError("runtime authorization lease was not issued") from exc
    binding_tuple = _binding_tuple(manifest, observation)
    subject = _observed_subject(manifest, observation, lease, binding_tuple)
    payload_digest = TypedId.from_bare(digest_sha256_of_jcs(subject))
    evidence = RuntimeEvidenceRecord(
        schema_version="2.0",
        environment_fingerprint=manifest.core.environment_fingerprint,
        evidence_id=compute_runtime_evidence_id(
            kind=RuntimeEvidenceKind.POD_OBSERVED,
            release_id=manifest.release_id,
            pod_uid=observation.pod_uid,
            k8s_resource_version=observation.pod_resource_version,
            binding_tuple=binding_tuple,
            fencing_token=observation.fencing_token,
            payload_digest=payload_digest,
        ),
        kind=RuntimeEvidenceKind.POD_OBSERVED,
        release_id=manifest.release_id,
        pod_uid=observation.pod_uid,
        k8s_resource_version=observation.pod_resource_version,
        binding_tuple=binding_tuple,
        policy_decision_epoch=max(
            value.policy_decision_epoch for value in manifest.core.assets
        ),
        security_watermark=lease.security_watermark,
        fencing_token=observation.fencing_token,
        issuer_principal=lease_issuer_principal,
        payload_digest=payload_digest,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
    )
    attestation = create_attestation(
        schema_version="2.0",
        attestation_kind=_OBSERVED_ATTESTATION_KIND,
        environment_fingerprint=manifest.core.environment_fingerprint,
        subject={"evidence_id": evidence.evidence_id.typed, **subject},
        signer=evidence_signer,
    )
    return ServiceRuntimeStartupResult(lease, evidence, attestation)


def require_service_runtime_readiness(
    registry,
    result: ServiceRuntimeStartupResult,
    *,
    at: datetime,
    renewal_margin_seconds: int = 30,
) -> ServiceRuntimeStartupResult:
    if not isinstance(result, ServiceRuntimeStartupResult):
        raise ServiceRuntimeError("startup result is required for readiness")
    # Runtime leases are stored under service_name, not release_id. Deliberately
    # use the authoritative release record to derive that namespace.
    release = registry.get_release(result.observed_evidence.release_id)
    if release is None:
        raise ServiceRuntimeError("runtime release record is missing")
    stored = registry.get_runtime_authorization_lease(
        release.service_name, result.lease.lease_id
    )
    if stored != result.lease:
        raise ServiceRuntimeError("runtime authorization lease is missing")
    try:
        require_runtime_readiness(
            result.lease,
            at=at,
            renewal_margin_seconds=renewal_margin_seconds,
        )
    except ValueError as exc:
        raise ServiceRuntimeError("runtime authorization is not readiness-safe") from exc
    return result

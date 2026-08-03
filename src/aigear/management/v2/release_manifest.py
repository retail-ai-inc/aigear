"""Immutable, content-addressed and independently signed service releases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Mapping, Sequence, Tuple

from aigear.management.v2.attestation import (
    AttestationRecord,
    AttestationVerifier,
    DigestSigner,
    create_attestation,
    verify_attestation,
)
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.asset_version import AssetVersionRecord
from aigear.management.v2.records.release import ReleaseRecord
from aigear.management.v2.resolver import (
    ResolvedHandle,
    UsageContext,
    require_handle_usage_context,
)

__all__ = [
    "ReleaseManifestError",
    "ConfigReferenceKind",
    "ReleaseBlobBinding",
    "ReleaseAssetBinding",
    "ReleaseImageBinding",
    "ImmutableConfigReference",
    "ReleaseAssetContract",
    "RuntimeContract",
    "ReleaseManifestCore",
    "SignedReleaseManifest",
    "build_release_asset_binding",
    "compute_release_id",
    "sign_release_manifest",
    "verify_release_manifest",
]


RELEASE_DOMAIN = "aigear.release.v2"
RELEASE_ATTESTATION_KIND = "release_manifest"


class ReleaseManifestError(ValueError):
    pass


class ConfigReferenceKind(str, Enum):
    KUBERNETES_CONFIG_MAP = "kubernetes_config_map"
    KUBERNETES_SECRET = "kubernetes_secret"
    SECRET_MANAGER = "secret_manager"


def _typed(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise ReleaseManifestError(f"{field_name} must be a TypedId")


def _positive(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReleaseManifestError(f"{field_name} must be a positive int")


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ReleaseManifestError(f"{field_name} must be a non-empty str")


def _canonical_utc(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ReleaseManifestError(
            f"{field_name} must be a canonical UTC timestamp"
        ) from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat() != value
    ):
        raise ReleaseManifestError(f"{field_name} must be a canonical UTC timestamp")
    return parsed


def _as_tuple(value: object) -> tuple:
    return tuple(value) if isinstance(value, list) else value


@dataclass(frozen=True)
class ReleaseBlobBinding:
    role: str
    logical_name: str
    blob_id: TypedId
    generation: str
    location_revision: int
    sha256: str
    size_bytes: int
    location_attestation_id: TypedId

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", validate_segment(self.role, field_name="role"))
        object.__setattr__(
            self,
            "logical_name",
            validate_segment(self.logical_name, field_name="logical_name"),
        )
        _typed("blob_id", self.blob_id)
        if (
            not isinstance(self.generation, str)
            or not self.generation.isdigit()
            or int(self.generation) < 1
            or str(int(self.generation)) != self.generation
        ):
            raise ReleaseManifestError("generation must be an exact numeric generation")
        _positive("location_revision", self.location_revision)
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ReleaseManifestError("sha256 must be 64 lowercase hexadecimal characters")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ReleaseManifestError("size_bytes must be a non-negative int")
        _typed("location_attestation_id", self.location_attestation_id)

    def to_jcs_dict(self) -> dict:
        return {
            "role": self.role,
            "logical_name": self.logical_name,
            "blob_id": self.blob_id.typed,
            "generation": self.generation,
            "location_revision": self.location_revision,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "location_attestation_id": self.location_attestation_id.typed,
        }


@dataclass(frozen=True)
class ReleaseAssetBinding:
    binding_name: str
    asset_version_id: TypedId
    manifest_attestation_id: TypedId
    schema_contract_digest: TypedId
    runtime_contract_digest: TypedId
    policy_decision_head_id: TypedId
    policy_decision_epoch: int
    policy_version: str
    policy_valid_until: str
    blobs: Tuple[ReleaseBlobBinding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "binding_name",
            validate_segment(self.binding_name, field_name="binding_name"),
        )
        for field_name in (
            "asset_version_id",
            "manifest_attestation_id",
            "schema_contract_digest",
            "runtime_contract_digest",
            "policy_decision_head_id",
        ):
            _typed(field_name, getattr(self, field_name))
        _positive("policy_decision_epoch", self.policy_decision_epoch)
        _non_empty("policy_version", self.policy_version)
        _canonical_utc("policy_valid_until", self.policy_valid_until)
        object.__setattr__(self, "blobs", _as_tuple(self.blobs))
        if not self.blobs or not all(isinstance(blob, ReleaseBlobBinding) for blob in self.blobs):
            raise ReleaseManifestError("blobs must contain ReleaseBlobBinding values")
        keys = [(blob.role, blob.logical_name) for blob in self.blobs]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ReleaseManifestError("blobs must be sorted and unique by role/logical_name")

    def to_jcs_dict(self) -> dict:
        return {
            "binding_name": self.binding_name,
            "asset_version_id": self.asset_version_id.typed,
            "manifest_attestation_id": self.manifest_attestation_id.typed,
            "schema_contract_digest": self.schema_contract_digest.typed,
            "runtime_contract_digest": self.runtime_contract_digest.typed,
            "policy_decision_head_id": self.policy_decision_head_id.typed,
            "policy_decision_epoch": self.policy_decision_epoch,
            "policy_version": self.policy_version,
            "policy_valid_until": self.policy_valid_until,
            "blobs": [blob.to_jcs_dict() for blob in self.blobs],
        }


@dataclass(frozen=True)
class ReleaseImageBinding:
    image_reference: str
    image_digest: TypedId
    provenance_attestation_id: TypedId
    sbom_digest: TypedId

    def __post_init__(self) -> None:
        _non_empty("image_reference", self.image_reference)
        for field_name in (
            "image_digest",
            "provenance_attestation_id",
            "sbom_digest",
        ):
            _typed(field_name, getattr(self, field_name))
        if self.image_reference.count("@") != 1:
            raise ReleaseManifestError("image_reference must use an immutable digest")
        repository, digest = self.image_reference.split("@", 1)
        if (
            not repository
            or any(character.isspace() for character in repository)
            or digest != self.image_digest.typed
        ):
            raise ReleaseManifestError("image_reference must match image_digest exactly")

    def to_jcs_dict(self) -> dict:
        return {
            "image_reference": self.image_reference,
            "image_digest": self.image_digest.typed,
            "provenance_attestation_id": self.provenance_attestation_id.typed,
            "sbom_digest": self.sbom_digest.typed,
        }


@dataclass(frozen=True)
class ImmutableConfigReference:
    kind: ConfigReferenceKind
    name: str
    version: str
    content_digest: TypedId
    immutable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ConfigReferenceKind):
            raise ReleaseManifestError("kind must be ConfigReferenceKind")
        object.__setattr__(self, "name", validate_segment(self.name, field_name="name"))
        _typed("content_digest", self.content_digest)
        _non_empty("version", self.version)
        if self.immutable is not True:
            raise ReleaseManifestError("config and secret references must be immutable")
        if self.kind is ConfigReferenceKind.SECRET_MANAGER:
            if not self.version.isdigit() or int(self.version) < 1:
                raise ReleaseManifestError("Secret Manager version must be a positive integer")
        elif self.version != self.content_digest.typed:
            raise ReleaseManifestError(
                "Kubernetes config/secret version must equal its content digest"
            )

    def to_jcs_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "version": self.version,
            "content_digest": self.content_digest.typed,
            "immutable": self.immutable,
        }


@dataclass(frozen=True)
class ReleaseAssetContract:
    binding_name: str
    schema_contract_digest: TypedId
    runtime_contract_digest: TypedId

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "binding_name",
            validate_segment(self.binding_name, field_name="binding_name"),
        )
        _typed("schema_contract_digest", self.schema_contract_digest)
        _typed("runtime_contract_digest", self.runtime_contract_digest)

    def to_jcs_dict(self) -> dict:
        return {
            "binding_name": self.binding_name,
            "schema_contract_digest": self.schema_contract_digest.typed,
            "runtime_contract_digest": self.runtime_contract_digest.typed,
        }


@dataclass(frozen=True)
class RuntimeContract:
    protocol: str
    abi_version: str
    model_format: str
    input_schema_digest: TypedId
    output_schema_digest: TypedId
    shape_contract_digest: TypedId
    asset_contracts: Tuple[ReleaseAssetContract, ...]

    def __post_init__(self) -> None:
        for field_name in ("protocol", "abi_version", "model_format"):
            object.__setattr__(
                self,
                field_name,
                validate_segment(getattr(self, field_name), field_name=field_name),
            )
        for field_name in (
            "input_schema_digest",
            "output_schema_digest",
            "shape_contract_digest",
        ):
            _typed(field_name, getattr(self, field_name))
        object.__setattr__(self, "asset_contracts", _as_tuple(self.asset_contracts))
        if not self.asset_contracts or not all(
            isinstance(contract, ReleaseAssetContract)
            for contract in self.asset_contracts
        ):
            raise ReleaseManifestError(
                "asset_contracts must contain ReleaseAssetContract values"
            )
        names = [contract.binding_name for contract in self.asset_contracts]
        if names != sorted(names) or len(set(names)) != len(names):
            raise ReleaseManifestError(
                "asset_contracts must be sorted and unique by binding_name"
            )

    def to_jcs_dict(self) -> dict:
        return {
            "protocol": self.protocol,
            "abi_version": self.abi_version,
            "model_format": self.model_format,
            "input_schema_digest": self.input_schema_digest.typed,
            "output_schema_digest": self.output_schema_digest.typed,
            "shape_contract_digest": self.shape_contract_digest.typed,
            "asset_contracts": [
                contract.to_jcs_dict() for contract in self.asset_contracts
            ],
        }


@dataclass(frozen=True)
class ReleaseManifestCore:
    schema_version: str
    environment_fingerprint: TypedId
    service_name: str
    deployment_target_id: str
    image: ReleaseImageBinding
    assets: Tuple[ReleaseAssetBinding, ...]
    config_references: Tuple[ImmutableConfigReference, ...]
    runtime_contract: RuntimeContract
    deployment_spec_digest: TypedId

    def __post_init__(self) -> None:
        try:
            major, _minor = parse_schema_version(self.schema_version)
        except (TypeError, ValueError) as exc:
            raise ReleaseManifestError("schema_version is invalid") from exc
        if major != 2:
            raise ReleaseManifestError("release schema major must be 2")
        _typed("environment_fingerprint", self.environment_fingerprint)
        object.__setattr__(
            self, "service_name", validate_segment(self.service_name, field_name="service_name")
        )
        object.__setattr__(
            self,
            "deployment_target_id",
            validate_segment(self.deployment_target_id, field_name="deployment_target_id"),
        )
        if not isinstance(self.image, ReleaseImageBinding):
            raise ReleaseManifestError("image must be a ReleaseImageBinding")
        object.__setattr__(self, "assets", _as_tuple(self.assets))
        if not self.assets or not all(
            isinstance(asset, ReleaseAssetBinding) for asset in self.assets
        ):
            raise ReleaseManifestError("assets must contain ReleaseAssetBinding values")
        asset_names = [asset.binding_name for asset in self.assets]
        if asset_names != sorted(asset_names) or len(set(asset_names)) != len(asset_names):
            raise ReleaseManifestError("assets must be sorted and unique by binding_name")
        object.__setattr__(
            self, "config_references", _as_tuple(self.config_references)
        )
        if not all(isinstance(ref, ImmutableConfigReference) for ref in self.config_references):
            raise ReleaseManifestError("config_references contains an invalid value")
        config_keys = [(ref.kind.value, ref.name) for ref in self.config_references]
        if config_keys != sorted(config_keys) or len(set(config_keys)) != len(config_keys):
            raise ReleaseManifestError("config_references must be sorted and unique")
        if not isinstance(self.runtime_contract, RuntimeContract):
            raise ReleaseManifestError("runtime_contract must be RuntimeContract")
        expected_contracts = {
            contract.binding_name: (
                contract.schema_contract_digest,
                contract.runtime_contract_digest,
            )
            for contract in self.runtime_contract.asset_contracts
        }
        actual_contracts = {
            asset.binding_name: (
                asset.schema_contract_digest,
                asset.runtime_contract_digest,
            )
            for asset in self.assets
        }
        if actual_contracts != expected_contracts:
            raise ReleaseManifestError(
                "release assets do not satisfy schema/runtime contracts"
            )
        _typed("deployment_spec_digest", self.deployment_spec_digest)

    def to_jcs_dict(self) -> dict:
        return {
            "domain": RELEASE_DOMAIN,
            "schema_version": self.schema_version,
            "environment_fingerprint": self.environment_fingerprint.typed,
            "service_name": self.service_name,
            "deployment_target_id": self.deployment_target_id,
            "image": self.image.to_jcs_dict(),
            "assets": [asset.to_jcs_dict() for asset in self.assets],
            "config_references": [ref.to_jcs_dict() for ref in self.config_references],
            "runtime_contract": self.runtime_contract.to_jcs_dict(),
            "deployment_spec_digest": self.deployment_spec_digest.typed,
        }


def compute_release_id(core: ReleaseManifestCore) -> TypedId:
    if not isinstance(core, ReleaseManifestCore):
        raise ReleaseManifestError("core must be a ReleaseManifestCore")
    return TypedId.from_bare(digest_sha256_of_jcs(core.to_jcs_dict()))


@dataclass(frozen=True)
class SignedReleaseManifest:
    core: ReleaseManifestCore
    release_id: TypedId
    attestation: AttestationRecord

    def __post_init__(self) -> None:
        if not isinstance(self.core, ReleaseManifestCore):
            raise ReleaseManifestError("core must be a ReleaseManifestCore")
        _typed("release_id", self.release_id)
        if self.release_id != compute_release_id(self.core):
            raise ReleaseManifestError("release_id does not match release core")
        if not isinstance(self.attestation, AttestationRecord):
            raise ReleaseManifestError("attestation must be an AttestationRecord")
        expected_subject = {
            "release_id": self.release_id.typed,
            "release_core": self.core.to_jcs_dict(),
        }
        if (
            self.attestation.attestation_kind != RELEASE_ATTESTATION_KIND
            or self.attestation.environment_fingerprint
            != self.core.environment_fingerprint
            or self.attestation.unsigned_envelope.get("subject") != expected_subject
        ):
            raise ReleaseManifestError("release attestation does not match release core")

    def to_release_record(
        self, *, creation_operation_id: str, display_version: str, created_at: str
    ) -> ReleaseRecord:
        return ReleaseRecord(
            schema_version=self.core.schema_version,
            environment_fingerprint=self.core.environment_fingerprint,
            release_id=self.release_id,
            service_name=self.core.service_name,
            deployment_target_id=self.core.deployment_target_id,
            manifest_digest=self.release_id,
            signature_attestation_id=self.attestation.attestation_id,
            creation_operation_id=creation_operation_id,
            display_version=display_version,
            asset_version_ids=tuple(
                sorted(
                    (asset.asset_version_id for asset in self.core.assets),
                    key=lambda value: value.typed,
                )
            ),
            created_at=created_at,
        )


def build_release_asset_binding(
    binding_name: str,
    handle: ResolvedHandle,
    asset_version: AssetVersionRecord,
    *,
    now: datetime,
) -> ReleaseAssetBinding:
    try:
        require_handle_usage_context(handle, UsageContext.RELEASE)
    except ValueError as exc:
        raise ReleaseManifestError("asset handle is not authorized for release") from exc
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() != timedelta(0)
    ):
        raise ReleaseManifestError("now must be timezone-aware UTC")
    if now >= handle.expires_at:
        raise ReleaseManifestError("asset handle has expired")
    if (
        asset_version.asset_version_id != handle.asset_version_id
        or asset_version.environment_fingerprint != handle.environment_fingerprint
        or asset_version.record_revision != handle.asset_record_revision
    ):
        raise ReleaseManifestError("asset record does not match resolved handle")
    if (
        handle.policy_decision_head_ref is None
        or handle.policy_decision_epoch is None
        or handle.policy_version is None
        or handle.policy_valid_until is None
    ):
        raise ReleaseManifestError("release asset is missing current policy binding")
    blobs = tuple(
        ReleaseBlobBinding(
            role=blob.role,
            logical_name=blob.logical_name,
            blob_id=blob.blob_id,
            generation=blob.generation,
            location_revision=blob.location_revision,
            sha256=blob.sha256,
            size_bytes=blob.size_bytes,
            location_attestation_id=blob.location_attestation_ref,
        )
        for blob in handle.blobs
    )
    return ReleaseAssetBinding(
        binding_name=binding_name,
        asset_version_id=asset_version.asset_version_id,
        manifest_attestation_id=asset_version.manifest_integrity_attestation_ref,
        schema_contract_digest=asset_version.schema_contract_digest,
        runtime_contract_digest=asset_version.runtime_contract_digest,
        policy_decision_head_id=handle.policy_decision_head_ref,
        policy_decision_epoch=handle.policy_decision_epoch,
        policy_version=handle.policy_version,
        policy_valid_until=handle.policy_valid_until,
        blobs=blobs,
    )


def _release_key_sets(
    release_key_versions: Sequence[str], non_release_key_versions: Sequence[str]
) -> tuple[frozenset[str], frozenset[str]]:
    try:
        release_keys = frozenset(release_key_versions)
        other_keys = frozenset(non_release_key_versions)
    except TypeError as exc:
        raise ReleaseManifestError("key allowlists must be sequences") from exc
    if not release_keys or not other_keys or any(not key for key in release_keys | other_keys):
        raise ReleaseManifestError("release and non-release key allowlists are required")
    if release_keys & other_keys:
        raise ReleaseManifestError("release signing keys must be independent")
    return release_keys, other_keys


def sign_release_manifest(
    core: ReleaseManifestCore,
    *,
    signer: DigestSigner,
    release_key_versions: Sequence[str],
    non_release_key_versions: Sequence[str],
) -> SignedReleaseManifest:
    release_keys, _other_keys = _release_key_sets(
        release_key_versions, non_release_key_versions
    )
    if signer.key_version not in release_keys:
        raise ReleaseManifestError("release signer key version is not allowlisted")
    release_id = compute_release_id(core)
    attestation = create_attestation(
        schema_version=core.schema_version,
        attestation_kind=RELEASE_ATTESTATION_KIND,
        environment_fingerprint=core.environment_fingerprint,
        subject={"release_id": release_id.typed, "release_core": core.to_jcs_dict()},
        signer=signer,
    )
    return SignedReleaseManifest(
        core=core,
        release_id=release_id,
        attestation=attestation,
    )


def verify_release_manifest(
    manifest: SignedReleaseManifest,
    *,
    verifier: AttestationVerifier,
    release_key_versions: Sequence[str],
    non_release_key_versions: Sequence[str],
    expected_environment_fingerprint: TypedId,
    expected_service_name: str,
    expected_deployment_target_id: str,
    expected_runtime_contract: RuntimeContract,
    resolved_assets: Mapping[str, ResolvedHandle],
    now: datetime,
) -> SignedReleaseManifest:
    if not isinstance(manifest, SignedReleaseManifest):
        raise ReleaseManifestError("manifest must be a SignedReleaseManifest")
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() != timedelta(0)
    ):
        raise ReleaseManifestError("now must be timezone-aware UTC")
    if not isinstance(resolved_assets, Mapping):
        raise ReleaseManifestError("resolved_assets must be a mapping")
    release_keys, _other_keys = _release_key_sets(
        release_key_versions, non_release_key_versions
    )
    if manifest.attestation.key_version not in release_keys:
        raise ReleaseManifestError("release attestation key version is not allowlisted")
    try:
        verify_attestation(manifest.attestation, verifier)
    except ValueError as exc:
        raise ReleaseManifestError("release signature verification failed") from exc
    if (
        manifest.core.environment_fingerprint != expected_environment_fingerprint
        or manifest.core.service_name != expected_service_name
        or manifest.core.deployment_target_id != expected_deployment_target_id
    ):
        raise ReleaseManifestError("release target binding does not match")
    if manifest.core.runtime_contract != expected_runtime_contract:
        raise ReleaseManifestError("release runtime ABI/schema/shape contract is incompatible")
    if set(resolved_assets) != {asset.binding_name for asset in manifest.core.assets}:
        raise ReleaseManifestError("resolved asset binding set does not match release")
    for asset in manifest.core.assets:
        handle = resolved_assets[asset.binding_name]
        try:
            require_handle_usage_context(handle, UsageContext.RELEASE)
        except ValueError as exc:
            raise ReleaseManifestError("asset handle is not authorized for release") from exc
        expected_blobs = tuple(
            (
                blob.role,
                blob.logical_name,
                blob.blob_id,
                blob.generation,
                blob.location_revision,
                blob.sha256,
                blob.size_bytes,
                blob.location_attestation_id,
            )
            for blob in asset.blobs
        )
        actual_blobs = tuple(
            (
                blob.role,
                blob.logical_name,
                blob.blob_id,
                blob.generation,
                blob.location_revision,
                blob.sha256,
                blob.size_bytes,
                blob.location_attestation_ref,
            )
            for blob in handle.blobs
        )
        if (
            now >= handle.expires_at
            or handle.asset_version_id != asset.asset_version_id
            or handle.policy_decision_head_ref != asset.policy_decision_head_id
            or handle.policy_decision_epoch != asset.policy_decision_epoch
            or handle.policy_version != asset.policy_version
            or handle.policy_valid_until != asset.policy_valid_until
            or actual_blobs != expected_blobs
        ):
            raise ReleaseManifestError("resolved asset does not match signed release")
    return manifest

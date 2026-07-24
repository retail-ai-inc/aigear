"""``AssetVersionRecord`` (spec sections 5.3 and 8.2).

An AssetVersion is an immutable semantic version: ``asset_version_id =
SHA256(JCS(canonical_manifest))``. The canonical manifest is a strict,
closed subset of fields (spec 5.3) — it never contains a run/attempt id,
timestamp, metric or mutable state, so the same semantic artifact can be
safely reused by many Occurrences. ``lifecycle_state``/``trust_state`` are
therefore modeled as separate, independently-evolving state machines that
never touch the immutable manifest; only ``record_revision`` changes when
they do.

This module holds pure data types and identity computation only; real
Firestore CAS/transactions are out of scope here (later tasks).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidAssetVersionRecordError",
    "InvalidLifecycleTransitionError",
    "InvalidTrustTransitionError",
    "LifecycleState",
    "TrustState",
    "validate_lifecycle_transition",
    "validate_trust_transition",
    "AssetComponent",
    "InputBinding",
    "ProducerSpec",
    "compute_component_key",
    "compute_asset_version_id",
    "AssetVersionRecord",
]


class InvalidAssetVersionRecordError(ValueError):
    """Raised when an AssetVersionRecord (or a nested value) is malformed."""


class InvalidLifecycleTransitionError(ValueError):
    """Raised when a ``lifecycle_state`` transition is not allowed (spec 12.1)."""


class InvalidTrustTransitionError(ValueError):
    """Raised when a ``trust_state`` transition is not allowed (spec 12.2)."""


class LifecycleState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETE_PENDING = "delete_pending"
    DELETED = "deleted"
    RESTORING = "restoring"


class TrustState(str, Enum):
    QUARANTINED = "quarantined"
    VERIFIED = "verified"
    APPROVED = "approved"
    REVOKED = "revoked"


_VALID_LIFECYCLE_TRANSITIONS = {
    LifecycleState.ACTIVE: frozenset({LifecycleState.ARCHIVED}),
    LifecycleState.ARCHIVED: frozenset(
        {LifecycleState.DELETE_PENDING, LifecycleState.ACTIVE}
    ),
    LifecycleState.DELETE_PENDING: frozenset(
        {LifecycleState.DELETED, LifecycleState.ARCHIVED}
    ),
    LifecycleState.DELETED: frozenset({LifecycleState.RESTORING}),
    LifecycleState.RESTORING: frozenset({LifecycleState.ARCHIVED}),
}

# Spec 12.2 draws a single forward chain; there is no documented path back
# from a later trust state to an earlier one, and "revoked" is terminal.
_VALID_TRUST_TRANSITIONS = {
    TrustState.QUARANTINED: frozenset({TrustState.VERIFIED}),
    TrustState.VERIFIED: frozenset({TrustState.APPROVED}),
    TrustState.APPROVED: frozenset({TrustState.REVOKED}),
    TrustState.REVOKED: frozenset(),
}


def validate_lifecycle_transition(current: LifecycleState, target: LifecycleState) -> None:
    allowed = _VALID_LIFECYCLE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidLifecycleTransitionError(
            f"illegal AssetVersion lifecycle_state transition: "
            f"{current.value!r} -> {target.value!r}"
        )


def validate_trust_transition(current: TrustState, target: TrustState) -> None:
    allowed = _VALID_TRUST_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTrustTransitionError(
            f"illegal AssetVersion trust_state transition: {current.value!r} -> {target.value!r}"
        )


def _require_non_empty_str(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidAssetVersionRecordError(
            f"{field_name} must be a non-empty str, got {value!r}"
        )


def _require_typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidAssetVersionRecordError(
            f"{field_name} must be a TypedId, got {type(value)!r}"
        )


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidAssetVersionRecordError(
            f"{field_name} must be a non-negative int, got {value!r}"
        )


def compute_component_key(role: str, logical_name: str) -> TypedId:
    """``component_key`` per spec 5.3: a deterministic, collision-resistant
    path key for a component so callers never hand-splice ``role``/``logical_name``."""
    normalized_role = validate_segment(role, field_name="role")
    normalized_logical_name = validate_segment(logical_name, field_name="logical_name")
    payload = ["aigear.component-key.v2", normalized_role, normalized_logical_name]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


@dataclass(frozen=True)
class AssetComponent:
    """A single manifest component: ``{role, blob_id, logical_name, media_type}``."""

    role: str
    blob_id: TypedId
    logical_name: str
    media_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", validate_segment(self.role, field_name="role"))
        _require_typed_id("blob_id", self.blob_id)
        object.__setattr__(
            self, "logical_name", validate_segment(self.logical_name, field_name="logical_name")
        )
        _require_non_empty_str("media_type", self.media_type)

    @property
    def component_key(self) -> TypedId:
        return compute_component_key(self.role, self.logical_name)

    def to_manifest_dict(self) -> dict:
        return {
            "role": self.role,
            "blob_id": self.blob_id.typed,
            "logical_name": self.logical_name,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class InputBinding:
    """A single manifest input binding: ``{binding_name, asset_version_id}``."""

    binding_name: str
    asset_version_id: TypedId

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "binding_name", validate_segment(self.binding_name, field_name="binding_name")
        )
        _require_typed_id("asset_version_id", self.asset_version_id)

    def to_manifest_dict(self) -> dict:
        return {"binding_name": self.binding_name, "asset_version_id": self.asset_version_id.typed}


@dataclass(frozen=True)
class ProducerSpec:
    """``{source_commit, image_digest, code_digest, config_digest}`` (spec 5.3/8.2)."""

    source_commit: str
    image_digest: TypedId
    code_digest: TypedId
    config_digest: TypedId

    def __post_init__(self) -> None:
        _require_non_empty_str("source_commit", self.source_commit)
        _require_typed_id("image_digest", self.image_digest)
        _require_typed_id("code_digest", self.code_digest)
        _require_typed_id("config_digest", self.config_digest)

    def to_manifest_dict(self) -> dict:
        return {
            "source_commit": self.source_commit,
            "image_digest": self.image_digest.typed,
            "code_digest": self.code_digest.typed,
            "config_digest": self.config_digest.typed,
        }


def compute_asset_version_id(manifest: dict) -> TypedId:
    """``asset_version_id = SHA256(JCS(canonical_manifest))`` (spec 5.3).

    Purely hashes whatever dict is given; callers should build that dict via
    :meth:`AssetVersionRecord.canonical_manifest` (or an equivalent that
    enforces the same field set, sorting and uniqueness rules) rather than
    hand-assembling it.
    """
    return TypedId.from_bare(digest_sha256_of_jcs(manifest))


@dataclass(frozen=True)
class AssetVersionRecord:
    """The AssetVersion document (spec 8.2): immutable manifest + mutable state.

    ``asset_version_id``/``manifest_digest`` are required to hold the exact
    same digest (spec: ``asset_version_id = "sha256:<manifest_digest>"``);
    construction eagerly recomputes the manifest digest from the record's own
    fields and rejects any mismatch, so an ``AssetVersionRecord`` can never be
    constructed pointing at a manifest it doesn't actually match.
    """

    schema_version: str
    environment_id: str
    environment_fingerprint: TypedId
    asset_version_id: TypedId
    asset_type: str
    name: str
    manifest_digest: TypedId
    record_revision: int
    components: Tuple[AssetComponent, ...]
    input_bindings: Tuple[InputBinding, ...]
    producer_spec: ProducerSpec
    schema_contract_digest: TypedId
    runtime_contract_digest: TypedId
    lifecycle_state: LifecycleState
    trust_state: TrustState
    policy_version: str
    manifest_integrity_attestation_ref: TypedId
    reference_epoch: int = 0
    policy_decision_head_ref: Optional[TypedId] = None
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.components, list):
            object.__setattr__(self, "components", tuple(self.components))
        if isinstance(self.input_bindings, list):
            object.__setattr__(self, "input_bindings", tuple(self.input_bindings))

        parse_schema_version(self.schema_version)
        _require_non_empty_str("environment_id", self.environment_id)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("asset_version_id", self.asset_version_id)
        _require_typed_id("manifest_digest", self.manifest_digest)
        if self.asset_version_id != self.manifest_digest:
            raise InvalidAssetVersionRecordError(
                "asset_version_id must equal manifest_digest: "
                f"{self.asset_version_id.typed!r} != {self.manifest_digest.typed!r}"
            )
        object.__setattr__(self, "asset_type", validate_segment(self.asset_type, field_name="asset_type"))
        object.__setattr__(self, "name", validate_segment(self.name, field_name="name"))
        if isinstance(self.record_revision, bool) or not isinstance(self.record_revision, int) or self.record_revision < 1:
            raise InvalidAssetVersionRecordError(
                f"record_revision must be a positive int, got {self.record_revision!r}"
            )

        if not self.components or not all(
            isinstance(component, AssetComponent) for component in self.components
        ):
            raise InvalidAssetVersionRecordError(
                "components must be a non-empty sequence of AssetComponent"
            )
        sort_keys = [(component.role, component.logical_name) for component in self.components]
        if sort_keys != sorted(sort_keys):
            raise InvalidAssetVersionRecordError(
                "components must be sorted by (role, logical_name)"
            )
        if len(set(sort_keys)) != len(sort_keys):
            raise InvalidAssetVersionRecordError(
                "components must be unique by (role, logical_name)"
            )

        if not all(isinstance(binding, InputBinding) for binding in self.input_bindings):
            raise InvalidAssetVersionRecordError(
                "input_bindings must be a sequence of InputBinding"
            )
        binding_names = [binding.binding_name for binding in self.input_bindings]
        if binding_names != sorted(binding_names):
            raise InvalidAssetVersionRecordError("input_bindings must be sorted by binding_name")
        if len(set(binding_names)) != len(binding_names):
            raise InvalidAssetVersionRecordError("input_bindings must be unique by binding_name")

        if not isinstance(self.producer_spec, ProducerSpec):
            raise InvalidAssetVersionRecordError("producer_spec must be a ProducerSpec")
        _require_typed_id("schema_contract_digest", self.schema_contract_digest)
        _require_typed_id("runtime_contract_digest", self.runtime_contract_digest)
        if not isinstance(self.lifecycle_state, LifecycleState):
            raise InvalidAssetVersionRecordError(
                f"lifecycle_state must be a LifecycleState, got {self.lifecycle_state!r}"
            )
        if not isinstance(self.trust_state, TrustState):
            raise InvalidAssetVersionRecordError(
                f"trust_state must be a TrustState, got {self.trust_state!r}"
            )
        _require_non_empty_str("policy_version", self.policy_version)
        _require_typed_id(
            "manifest_integrity_attestation_ref", self.manifest_integrity_attestation_ref
        )
        _require_non_negative_int("reference_epoch", self.reference_epoch)
        if self.policy_decision_head_ref is not None:
            _require_typed_id("policy_decision_head_ref", self.policy_decision_head_ref)

        recomputed = compute_asset_version_id(self.canonical_manifest())
        if recomputed != self.asset_version_id:
            raise InvalidAssetVersionRecordError(
                "asset_version_id does not match the digest of this record's own "
                f"canonical_manifest(): expected {recomputed.typed!r}, got "
                f"{self.asset_version_id.typed!r}"
            )

    def canonical_manifest(self) -> dict:
        """Build the exact dict whose ``digest_sha256_of_jcs`` is ``asset_version_id`` (spec 5.3).

        Deliberately excludes everything the spec forbids from the manifest:
        run/attempt/operation ids, timestamps, metrics, mutable state and alias.
        """
        return {
            "environment_id": self.environment_id,
            "environment_fingerprint": self.environment_fingerprint.typed,
            "asset_type": self.asset_type,
            "name": self.name,
            "components": [component.to_manifest_dict() for component in self.components],
            "input_bindings": [binding.to_manifest_dict() for binding in self.input_bindings],
            "producer_spec": self.producer_spec.to_manifest_dict(),
            "schema_contract_digest": self.schema_contract_digest.typed,
            "runtime_contract_digest": self.runtime_contract_digest.typed,
            "policy_version": self.policy_version,
        }

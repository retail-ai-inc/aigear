"""``BlobRecord`` / ``BlobLocationRevision`` (spec section 8.1).

A Blob describes only physical bytes (digest, size, current GCS location); it
never carries run/step/metrics/lineage information. Two invariants matter
most here and are what this module enforces:

- ``availability_state`` only moves along the explicit state machine in
  section 8.1 (``pending -> ready -> missing/corrupt``,
  ``ready <-> delete_pending -> deleted``, ``* -> restoring -> ready/missing/
  corrupt``); every other transition is a bug, not a data error, and must
  raise loudly.
- The immutable location history chain (``location_revisions/{revision}``) is
  append-only, has a closed ``location_operation_kind`` enum, and its
  cryptographic head is derived with the exact two-step JCS formula from the
  spec (genesis head, then head-from-attestation) so every implementation
  computes byte-identical chain heads.

This module holds pure data types and validation only; real GCS/Firestore
interaction is out of scope here (later tasks).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "InvalidBlobRecordError",
    "InvalidAvailabilityTransitionError",
    "AvailabilityState",
    "LocationOperationKind",
    "validate_availability_transition",
    "compute_genesis_location_chain_head",
    "compute_location_chain_head",
    "BlobRecord",
    "BlobLocationRevision",
]


class InvalidBlobRecordError(ValueError):
    """Raised when a BlobRecord/BlobLocationRevision field is malformed."""


class InvalidAvailabilityTransitionError(ValueError):
    """Raised when an ``availability_state`` transition is not allowed."""


class AvailabilityState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    MISSING = "missing"
    CORRUPT = "corrupt"
    DELETE_PENDING = "delete_pending"
    DELETED = "deleted"
    RESTORING = "restoring"


class LocationOperationKind(str, Enum):
    """Closed enum per spec 8.1 for ``location_operation_kind``."""

    PIPELINE_FINALIZE = "pipeline_finalize"
    EXTERNAL_IMPORT = "external_import"
    LEGACY_MIGRATION = "legacy_migration"
    RESTORE = "restore"
    REHYDRATE = "rehydrate"
    SECURITY_REATTEST = "security_reattest"


_VALID_AVAILABILITY_TRANSITIONS = {
    AvailabilityState.PENDING: frozenset({AvailabilityState.READY}),
    AvailabilityState.READY: frozenset(
        {
            AvailabilityState.MISSING,
            AvailabilityState.CORRUPT,
            AvailabilityState.DELETE_PENDING,
            AvailabilityState.RESTORING,
        }
    ),
    AvailabilityState.MISSING: frozenset({AvailabilityState.RESTORING}),
    AvailabilityState.CORRUPT: frozenset({AvailabilityState.RESTORING}),
    AvailabilityState.DELETE_PENDING: frozenset(
        {AvailabilityState.DELETED, AvailabilityState.READY}
    ),
    AvailabilityState.DELETED: frozenset({AvailabilityState.RESTORING}),
    AvailabilityState.RESTORING: frozenset(
        {AvailabilityState.READY, AvailabilityState.MISSING, AvailabilityState.CORRUPT}
    ),
}


def validate_availability_transition(
    current: AvailabilityState, target: AvailabilityState
) -> None:
    """Raise :class:`InvalidAvailabilityTransitionError` for any edge not in spec 8.1."""
    allowed = _VALID_AVAILABILITY_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidAvailabilityTransitionError(
            f"illegal BlobRecord availability_state transition: "
            f"{current.value!r} -> {target.value!r}"
        )


def compute_genesis_location_chain_head(
    environment_fingerprint: TypedId, blob_id: TypedId
) -> TypedId:
    """``previous_chain_head`` for ``location_revision == 1`` (spec 8.1)."""
    payload = [
        "aigear.location-chain-genesis.v2",
        environment_fingerprint.typed,
        blob_id.typed,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def compute_location_chain_head(
    previous_chain_head: TypedId, attestation_id: TypedId
) -> TypedId:
    """Derive the next ``location_chain_head`` from the previous head and this
    revision's already-signed ``attestation_id`` (spec 8.1). The new head must
    never feed back into the attestation it is derived from."""
    payload = ["aigear.location-chain.v2", previous_chain_head.typed, attestation_id.typed]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def _require_positive_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidBlobRecordError(f"{field_name} must be a positive int, got {value!r}")


def _require_non_negative_int(field_name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidBlobRecordError(
            f"{field_name} must be a non-negative int, got {value!r}"
        )


def _require_non_empty_str(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidBlobRecordError(f"{field_name} must be a non-empty str, got {value!r}")


def _require_typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidBlobRecordError(f"{field_name} must be a TypedId, got {type(value)!r}")


@dataclass(frozen=True)
class BlobRecord:
    """The current-state Blob document (spec 8.1)."""

    schema_version: str
    environment_fingerprint: TypedId
    blob_id: TypedId
    sha256: str
    size_bytes: int
    crc32c: str
    bucket: str
    object_name: str
    generation: str
    current_location_revision: int
    current_location_attestation_ref: TypedId
    location_chain_head: TypedId
    availability_state: AvailabilityState
    reference_epoch: int = 0
    retention_class: str = "standard"
    legal_hold: bool = False
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("blob_id", self.blob_id)
        if self.sha256 != self.blob_id.bare:
            raise InvalidBlobRecordError(
                "sha256 must equal blob_id's bare digest: "
                f"{self.sha256!r} != {self.blob_id.bare!r}"
            )
        _require_non_negative_int("size_bytes", self.size_bytes)
        _require_non_empty_str("crc32c", self.crc32c)
        _require_non_empty_str("bucket", self.bucket)
        _require_non_empty_str("object_name", self.object_name)
        _require_non_empty_str("generation", self.generation)
        _require_positive_int("current_location_revision", self.current_location_revision)
        _require_typed_id(
            "current_location_attestation_ref", self.current_location_attestation_ref
        )
        _require_typed_id("location_chain_head", self.location_chain_head)
        if not isinstance(self.availability_state, AvailabilityState):
            raise InvalidBlobRecordError(
                f"availability_state must be an AvailabilityState, got {self.availability_state!r}"
            )
        _require_non_negative_int("reference_epoch", self.reference_epoch)
        _require_non_empty_str("retention_class", self.retention_class)
        if not isinstance(self.legal_hold, bool):
            raise InvalidBlobRecordError(
                f"legal_hold must be a bool, got {type(self.legal_hold)!r}"
            )


@dataclass(frozen=True)
class BlobLocationRevision:
    """One immutable entry of a Blob's append-only location history (spec 8.1)."""

    schema_version: str
    environment_fingerprint: TypedId
    blob_id: TypedId
    location_revision: int
    bucket: str
    object_name: str
    generation: str
    sha256: str
    crc32c: str
    size_bytes: int
    location_operation_id: str
    location_operation_kind: LocationOperationKind
    location_attestation_ref: TypedId
    location_chain_head: TypedId
    reason: str
    source_location_revision: Optional[int] = None
    previous_location_attestation_ref: Optional[TypedId] = None
    activated_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("blob_id", self.blob_id)
        _require_positive_int("location_revision", self.location_revision)
        _require_non_empty_str("bucket", self.bucket)
        _require_non_empty_str("object_name", self.object_name)
        _require_non_empty_str("generation", self.generation)
        if self.sha256 != self.blob_id.bare:
            raise InvalidBlobRecordError(
                "sha256 must equal blob_id's bare digest: "
                f"{self.sha256!r} != {self.blob_id.bare!r}"
            )
        _require_non_empty_str("crc32c", self.crc32c)
        _require_non_negative_int("size_bytes", self.size_bytes)
        _require_non_empty_str("location_operation_id", self.location_operation_id)
        if not isinstance(self.location_operation_kind, LocationOperationKind):
            raise InvalidBlobRecordError(
                "location_operation_kind must be a LocationOperationKind, "
                f"got {self.location_operation_kind!r}"
            )
        _require_typed_id("location_attestation_ref", self.location_attestation_ref)
        _require_typed_id("location_chain_head", self.location_chain_head)
        _require_non_empty_str("reason", self.reason)

        if self.location_revision == 1:
            if self.source_location_revision is not None:
                raise InvalidBlobRecordError(
                    "source_location_revision must be null for location_revision == 1"
                )
            if self.previous_location_attestation_ref is not None:
                raise InvalidBlobRecordError(
                    "previous_location_attestation_ref must be null for location_revision == 1"
                )
        else:
            if self.source_location_revision != self.location_revision - 1:
                raise InvalidBlobRecordError(
                    "source_location_revision must equal location_revision - 1 for "
                    f"revision > 1, got {self.source_location_revision!r} at revision "
                    f"{self.location_revision}"
                )
            if self.previous_location_attestation_ref is None:
                raise InvalidBlobRecordError(
                    "previous_location_attestation_ref is required for location_revision > 1"
                )

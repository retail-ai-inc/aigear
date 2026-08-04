"""Lineage / Component / Attachment edge types (spec section 11).

These three edge types are the authoritative reverse-reference graph the
finalize transaction (spec 10.4) writes alongside a committed Occurrence:

- ``LineageEdge``: Occurrence -> Occurrence execution provenance (one edge per
  resolved input binding).
- ``ComponentEdge``: AssetVersion -> Blob, for every component that
  participates in the AssetVersion's identity (manifest ``components``).
- ``AttachmentEdge``: Occurrence/Operation/Release -> Blob, for large
  non-identity artifacts (metrics, profiles, reports) that must not be
  disguised as AssetVersion components.

Each edge's ID is a pure function of its own fields (spec 11's JCS payloads),
and construction eagerly recomputes and cross-checks it, matching every other
self-verifying record type in this package (``AssetVersionRecord``,
``LabelRecord``, ``OccurrenceRecord``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidLineageEdgeError",
    "OwnerKind",
    "compute_lineage_edge_id",
    "compute_component_edge_id",
    "compute_attachment_edge_id",
    "LineageEdge",
    "ComponentEdge",
    "AttachmentEdge",
]


class InvalidLineageEdgeError(ValueError):
    """Raised when a lineage/component/attachment edge (or a nested value) is malformed."""


class OwnerKind(str, Enum):
    """Closed enum of attachment edge owner kinds (spec 11: "Occurrence/Operation/Release 到非
    identity Blob 的权威引用")."""

    OCCURRENCE = "occurrence"
    OPERATION = "operation"
    RELEASE = "release"


def _require_non_empty_str(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidLineageEdgeError(f"{field_name} must be a non-empty str, got {value!r}")


def _require_typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidLineageEdgeError(f"{field_name} must be a TypedId, got {type(value)!r}")


def compute_lineage_edge_id(
    output_occurrence_id: TypedId, binding_name: str, input_occurrence_id: TypedId
) -> TypedId:
    """``edge_id`` per spec 11's lineage edge JCS payload."""
    normalized_binding_name = validate_segment(binding_name, field_name="binding_name")
    payload = [
        "aigear.lineage-edge.v2",
        output_occurrence_id.typed,
        normalized_binding_name,
        input_occurrence_id.typed,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def compute_component_edge_id(
    asset_version_id: TypedId, role: str, logical_name: str, blob_id: TypedId
) -> TypedId:
    """``component_edge_id`` per spec 11's component edge JCS payload."""
    normalized_role = validate_segment(role, field_name="role")
    normalized_logical_name = validate_segment(logical_name, field_name="logical_name")
    payload = [
        "aigear.component-edge.v2",
        asset_version_id.typed,
        normalized_role,
        normalized_logical_name,
        blob_id.typed,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def compute_attachment_edge_id(
    owner_kind: "OwnerKind", owner_id: str, attachment_kind: str, logical_name: str, blob_id: TypedId
) -> TypedId:
    """``attachment_edge_id`` per spec 11's attachment edge JCS payload."""
    if not isinstance(owner_kind, OwnerKind):
        raise InvalidLineageEdgeError(f"owner_kind must be an OwnerKind, got {owner_kind!r}")
    _require_non_empty_str("owner_id", owner_id)
    normalized_attachment_kind = validate_segment(attachment_kind, field_name="attachment_kind")
    normalized_logical_name = validate_segment(logical_name, field_name="logical_name")
    payload = [
        "aigear.attachment-edge.v2",
        owner_kind.value,
        owner_id,
        normalized_attachment_kind,
        normalized_logical_name,
        blob_id.typed,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


@dataclass(frozen=True)
class LineageEdge:
    """Occurrence -> Occurrence execution provenance edge (spec 11)."""

    schema_version: str
    environment_fingerprint: TypedId
    edge_id: TypedId
    output_occurrence_id: TypedId
    input_occurrence_id: TypedId
    binding_name: str
    run_id: str
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("edge_id", self.edge_id)
        _require_typed_id("output_occurrence_id", self.output_occurrence_id)
        _require_typed_id("input_occurrence_id", self.input_occurrence_id)
        object.__setattr__(
            self, "binding_name", validate_segment(self.binding_name, field_name="binding_name")
        )
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))

        recomputed = compute_lineage_edge_id(
            self.output_occurrence_id, self.binding_name, self.input_occurrence_id
        )
        if recomputed != self.edge_id:
            raise InvalidLineageEdgeError(
                "edge_id does not match compute_lineage_edge_id(output_occurrence_id, "
                f"binding_name, input_occurrence_id): expected {recomputed.typed!r}, got "
                f"{self.edge_id.typed!r}"
            )


@dataclass(frozen=True)
class ComponentEdge:
    """AssetVersion -> Blob identity-component edge (spec 11)."""

    schema_version: str
    environment_fingerprint: TypedId
    component_edge_id: TypedId
    asset_version_id: TypedId
    blob_id: TypedId
    role: str
    logical_name: str
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("component_edge_id", self.component_edge_id)
        _require_typed_id("asset_version_id", self.asset_version_id)
        _require_typed_id("blob_id", self.blob_id)
        object.__setattr__(self, "role", validate_segment(self.role, field_name="role"))
        object.__setattr__(
            self, "logical_name", validate_segment(self.logical_name, field_name="logical_name")
        )

        recomputed = compute_component_edge_id(
            self.asset_version_id, self.role, self.logical_name, self.blob_id
        )
        if recomputed != self.component_edge_id:
            raise InvalidLineageEdgeError(
                "component_edge_id does not match compute_component_edge_id(asset_version_id, "
                f"role, logical_name, blob_id): expected {recomputed.typed!r}, got "
                f"{self.component_edge_id.typed!r}"
            )


@dataclass(frozen=True)
class AttachmentEdge:
    """Occurrence/Operation/Release -> non-identity Blob edge (spec 11)."""

    schema_version: str
    environment_fingerprint: TypedId
    attachment_edge_id: TypedId
    owner_kind: OwnerKind
    owner_id: str
    attachment_kind: str
    logical_name: str
    blob_id: TypedId
    media_type: str
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("attachment_edge_id", self.attachment_edge_id)
        if not isinstance(self.owner_kind, OwnerKind):
            raise InvalidLineageEdgeError(f"owner_kind must be an OwnerKind, got {self.owner_kind!r}")
        _require_non_empty_str("owner_id", self.owner_id)
        object.__setattr__(
            self,
            "attachment_kind",
            validate_segment(self.attachment_kind, field_name="attachment_kind"),
        )
        object.__setattr__(
            self, "logical_name", validate_segment(self.logical_name, field_name="logical_name")
        )
        _require_typed_id("blob_id", self.blob_id)
        _require_non_empty_str("media_type", self.media_type)

        recomputed = compute_attachment_edge_id(
            self.owner_kind, self.owner_id, self.attachment_kind, self.logical_name, self.blob_id
        )
        if recomputed != self.attachment_edge_id:
            raise InvalidLineageEdgeError(
                "attachment_edge_id does not match compute_attachment_edge_id(owner_kind, "
                f"owner_id, attachment_kind, logical_name, blob_id): expected "
                f"{recomputed.typed!r}, got {self.attachment_edge_id.typed!r}"
            )

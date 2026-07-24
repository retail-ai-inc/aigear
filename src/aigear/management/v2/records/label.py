"""``LabelRecord`` (spec section 8.3).

A label is an immutable, unrebindable pointer from a
``(asset_type, asset_name, display_version)`` display path to exactly one
``AssetVersion``. ``label_id`` is computed only from the ASCII path-key
segments; optional human-facing ``asset_display_name``/``display_label``
never participate in identity, path or ordering. ``readable_manifest`` tracks
the GCS projection *delivery* state for this label and is not part of any
identity computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidLabelRecordError",
    "compute_label_id",
    "ReadableManifestProjection",
    "LabelRecord",
]


class InvalidLabelRecordError(ValueError):
    """Raised when a LabelRecord (or a nested value) is malformed."""


def compute_label_id(asset_type: str, asset_name: str, display_version: str) -> TypedId:
    """``label_id = SHA256(JCS(["aigear.label.v2", asset_type, normalized_asset_name,
    normalized_display_version]))`` (spec 8.3).

    ``asset_name``/``display_version`` are the ASCII path keys that also
    appear in the GCS asset projection path; they are run through the same
    NFC+segment validator used everywhere else so the identity and the path
    agree by construction.
    """
    normalized_asset_type = validate_segment(asset_type, field_name="asset_type")
    normalized_asset_name = validate_segment(asset_name, field_name="asset_name")
    normalized_display_version = validate_segment(display_version, field_name="display_version")
    payload = [
        "aigear.label.v2",
        normalized_asset_type,
        normalized_asset_name,
        normalized_display_version,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def _require_non_empty_str(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidLabelRecordError(f"{field_name} must be a non-empty str, got {value!r}")


def _require_typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidLabelRecordError(f"{field_name} must be a TypedId, got {type(value)!r}")


def _require_non_negative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidLabelRecordError(f"{field_name} must be a non-negative int, got {value!r}")


def _require_optional_non_negative_int(field_name: str, value: Optional[int]) -> None:
    if value is not None:
        _require_non_negative_int(field_name, value)


@dataclass(frozen=True)
class ReadableManifestProjection:
    """The GCS ``manifest.json`` projection delivery state for one label.

    ``status`` is intentionally left as a validated non-empty string rather
    than a closed enum: spec 6/8.3 only names ``pending`` (the required
    initial value, see :meth:`initial`) and ``ready`` explicitly; the rest of
    the projection-repair state space is defined by the projection
    consumer/repair task, out of scope for Phase A.
    """

    uri: str
    status: str
    projection_mutation_fence: int
    projection_schema_version: str
    observed_generation: Optional[str] = None
    observed_content_sha256: Optional[str] = None
    observed_source_revision: Optional[int] = None
    observed_repair_epoch: Optional[int] = None
    applied_source_revision: Optional[int] = None
    applied_repair_epoch: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri.startswith("gs://"):
            raise InvalidLabelRecordError(f"uri must be a gs:// URI, got {self.uri!r}")
        _require_non_empty_str("status", self.status)
        _require_non_negative_int("projection_mutation_fence", self.projection_mutation_fence)
        parse_schema_version(self.projection_schema_version)
        _require_optional_non_negative_int(
            "observed_source_revision", self.observed_source_revision
        )
        _require_optional_non_negative_int("observed_repair_epoch", self.observed_repair_epoch)
        _require_optional_non_negative_int(
            "applied_source_revision", self.applied_source_revision
        )
        _require_optional_non_negative_int("applied_repair_epoch", self.applied_repair_epoch)

    @classmethod
    def initial(cls, uri: str, *, projection_schema_version: str = "1.0") -> "ReadableManifestProjection":
        """The required initial state: ``pending`` with no observed generation yet."""
        return cls(
            uri=uri,
            status="pending",
            projection_mutation_fence=0,
            projection_schema_version=projection_schema_version,
        )


@dataclass(frozen=True)
class LabelRecord:
    """The Label document (spec 8.3): an immutable pointer to one AssetVersion.

    Construction eagerly recomputes ``label_id`` from
    ``(asset_type, asset_name, display_version)`` and rejects any mismatch,
    so a ``LabelRecord`` can never be constructed with an ID that does not
    actually correspond to its own path keys.
    """

    schema_version: str
    environment_fingerprint: TypedId
    label_id: TypedId
    asset_type: str
    asset_name: str
    display_version: str
    asset_version_id: TypedId
    projection_source_revision: int
    readable_manifest: ReadableManifestProjection
    created_by: str
    projection_repair_epoch: int = 0
    asset_display_name: Optional[str] = None
    display_label: Optional[str] = None
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("label_id", self.label_id)
        _require_typed_id("asset_version_id", self.asset_version_id)

        object.__setattr__(self, "asset_type", validate_segment(self.asset_type, field_name="asset_type"))
        object.__setattr__(
            self, "asset_name", validate_segment(self.asset_name, field_name="asset_name")
        )
        object.__setattr__(
            self,
            "display_version",
            validate_segment(self.display_version, field_name="display_version"),
        )

        recomputed = compute_label_id(self.asset_type, self.asset_name, self.display_version)
        if recomputed != self.label_id:
            raise InvalidLabelRecordError(
                "label_id does not match compute_label_id(asset_type, asset_name, "
                f"display_version): expected {recomputed.typed!r}, got {self.label_id.typed!r}"
            )

        _require_non_negative_int("projection_source_revision", self.projection_source_revision)
        _require_non_negative_int("projection_repair_epoch", self.projection_repair_epoch)
        if not isinstance(self.readable_manifest, ReadableManifestProjection):
            raise InvalidLabelRecordError(
                "readable_manifest must be a ReadableManifestProjection"
            )
        _require_non_empty_str("created_by", self.created_by)
        if self.asset_display_name is not None:
            _require_non_empty_str("asset_display_name", self.asset_display_name)
        if self.display_label is not None:
            _require_non_empty_str("display_label", self.display_label)

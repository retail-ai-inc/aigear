"""``OccurrenceRecord`` (spec section 8.4).

An Occurrence is the exact-provenance fact of one execution producing one
output. Unlike a Blob or an AssetVersion, an Occurrence is *created*
provisionally (at Attempt-lease time, with ``asset_version_id=null``) and
only later *committed* by the finalize transaction; the two committed-only
identities below therefore commute cleanly with that provisional state:

- ``occurrence_id`` identifies this exact ``(run, step, attempt, output)``
  execution slot and exists (server-side) from the moment the Attempt is
  leased, before anything is produced.
- ``committed_output_key`` identifies the ``(run, step, output)`` slot
  *across all attempts*, and is what enforces "at most one committed
  Occurrence per slot" via a create-once Firestore document.

This module holds pure data types and identity computation only; the actual
lease/finalize transactions are out of scope here (Phase B).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.label import ReadableManifestProjection

__all__ = [
    "InvalidOccurrenceRecordError",
    "InvalidOccurrenceStatusTransitionError",
    "OccurrenceStatus",
    "validate_occurrence_status_transition",
    "compute_occurrence_id",
    "compute_committed_output_key",
    "ResolvedInputBinding",
    "compute_resolved_inputs_digest",
    "compute_metrics_digest",
    "AttachmentRef",
    "OccurrenceRecord",
]


class InvalidOccurrenceRecordError(ValueError):
    """Raised when an OccurrenceRecord (or a nested value) is malformed."""


class InvalidOccurrenceStatusTransitionError(ValueError):
    """Raised when a ``status`` transition is not allowed (spec 12.1)."""


class OccurrenceStatus(str, Enum):
    PROVISIONAL = "provisional"
    COMMITTED = "committed"
    ARCHIVED = "archived"
    DELETE_PENDING = "delete_pending"
    DELETED_TOMBSTONE = "deleted_tombstone"
    ABORTED = "aborted"


_VALID_STATUS_TRANSITIONS = {
    OccurrenceStatus.PROVISIONAL: frozenset(
        {OccurrenceStatus.COMMITTED, OccurrenceStatus.ABORTED}
    ),
    OccurrenceStatus.COMMITTED: frozenset({OccurrenceStatus.ARCHIVED}),
    OccurrenceStatus.ARCHIVED: frozenset({OccurrenceStatus.DELETE_PENDING}),
    OccurrenceStatus.DELETE_PENDING: frozenset(
        {OccurrenceStatus.DELETED_TOMBSTONE, OccurrenceStatus.ARCHIVED}
    ),
    OccurrenceStatus.DELETED_TOMBSTONE: frozenset(),
    OccurrenceStatus.ABORTED: frozenset(),
}


def validate_occurrence_status_transition(
    current: OccurrenceStatus, target: OccurrenceStatus
) -> None:
    allowed = _VALID_STATUS_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidOccurrenceStatusTransitionError(
            f"illegal Occurrence status transition: {current.value!r} -> {target.value!r}"
        )


def _require_non_empty_str(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidOccurrenceRecordError(
            f"{field_name} must be a non-empty str, got {value!r}"
        )


def _require_typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidOccurrenceRecordError(
            f"{field_name} must be a TypedId, got {type(value)!r}"
        )


def _require_optional_typed_id(field_name: str, value: Optional[object]) -> None:
    if value is not None:
        _require_typed_id(field_name, value)


def _require_non_negative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidOccurrenceRecordError(
            f"{field_name} must be a non-negative int, got {value!r}"
        )


def _require_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidOccurrenceRecordError(f"{field_name} must be a positive int, got {value!r}")


def compute_occurrence_id(
    run_id: str, step_name: str, attempt_no: int, output_name: str
) -> TypedId:
    """``occurrence_id`` per spec 8.4: identifies one exact execution slot."""
    payload = ["aigear.occurrence.v2", run_id, step_name, attempt_no, output_name]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def compute_committed_output_key(run_id: str, step_name: str, output_name: str) -> TypedId:
    """``committed_output_key`` per spec 8.4: identifies one ``(run, step, output)``
    slot across all attempts, enforcing at most one committed Occurrence per slot."""
    payload = ["aigear.committed-output.v2", run_id, step_name, output_name]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


@dataclass(frozen=True)
class ResolvedInputBinding:
    """One sealed entry of ``resolved_input_bindings`` (spec 8.4).

    ``occurrence_id``/``source_label_id``/``policy_decision_epoch`` may be
    ``None`` depending on the input's provenance, but per spec they must
    always be present as explicit fields (never omitted) -- this dataclass
    enforces that by always serializing all five keys in
    :meth:`to_manifest_dict`.
    """

    binding_name: str
    asset_version_id: TypedId
    occurrence_id: Optional[TypedId] = None
    source_label_id: Optional[TypedId] = None
    policy_decision_epoch: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "binding_name", validate_segment(self.binding_name, field_name="binding_name")
        )
        _require_typed_id("asset_version_id", self.asset_version_id)
        _require_optional_typed_id("occurrence_id", self.occurrence_id)
        _require_optional_typed_id("source_label_id", self.source_label_id)
        if self.policy_decision_epoch is not None:
            _require_non_negative_int("policy_decision_epoch", self.policy_decision_epoch)

    def to_manifest_dict(self) -> dict:
        return {
            "binding_name": self.binding_name,
            "asset_version_id": self.asset_version_id.typed,
            "occurrence_id": self.occurrence_id.typed if self.occurrence_id else None,
            "source_label_id": self.source_label_id.typed if self.source_label_id else None,
            "policy_decision_epoch": self.policy_decision_epoch,
        }


def compute_resolved_inputs_digest(
    resolved_input_bindings: Tuple[ResolvedInputBinding, ...]
) -> TypedId:
    """``resolved_inputs_digest = SHA256(JCS(["aigear.resolved-inputs.v2",
    resolved_input_bindings]))`` (spec 8.4).

    Requires the array to already be sorted by ``binding_name`` (UTF-8 bytes)
    with unique names, matching the sealed, immutable shape the spec
    describes -- this function does not sort for the caller.
    """
    if not all(isinstance(binding, ResolvedInputBinding) for binding in resolved_input_bindings):
        raise InvalidOccurrenceRecordError(
            "resolved_input_bindings must be a sequence of ResolvedInputBinding"
        )
    names = [binding.binding_name for binding in resolved_input_bindings]
    if names != sorted(names):
        raise InvalidOccurrenceRecordError("resolved_input_bindings must be sorted by binding_name")
    if len(set(names)) != len(names):
        raise InvalidOccurrenceRecordError("resolved_input_bindings must be unique by binding_name")

    payload = [
        "aigear.resolved-inputs.v2",
        [binding.to_manifest_dict() for binding in resolved_input_bindings],
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def compute_metrics_digest(metrics: Dict) -> TypedId:
    """``metrics_digest = SHA256(JCS(["aigear.occurrence-metrics.v2", metrics]))`` (spec 8.4)."""
    if not isinstance(metrics, dict):
        raise InvalidOccurrenceRecordError(f"metrics must be a dict, got {type(metrics)!r}")
    payload = ["aigear.occurrence-metrics.v2", metrics]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


@dataclass(frozen=True)
class AttachmentRef:
    """``{attachment_kind, logical_name, blob_id, media_type}`` (spec 8.4)."""

    attachment_kind: str
    logical_name: str
    blob_id: TypedId
    media_type: str

    def __post_init__(self) -> None:
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


@dataclass(frozen=True)
class OccurrenceRecord:
    """The Occurrence document (spec 8.4).

    Construction eagerly recomputes ``occurrence_id``, ``committed_output_key``,
    ``resolved_inputs_digest`` and ``metrics_digest`` from the record's own
    fields and rejects any mismatch, following the same self-verification
    pattern as :class:`~aigear.management.v2.records.asset_version.AssetVersionRecord`.

    ``asset_version_id``/``asset_type``/``asset_name``/``label_id``/
    ``display_version``/``finalization_attestation_ref`` are only populated
    once ``status`` leaves ``provisional``/``aborted`` (spec 10.3: a
    provisional Occurrence is created with ``asset_version_id=null``).
    """

    schema_version: str
    environment_fingerprint: TypedId
    occurrence_id: TypedId
    run_id: str
    step_name: str
    attempt_no: int
    fencing_token: int
    output_name: str
    committed_output_key: TypedId
    resolved_input_bindings: Tuple[ResolvedInputBinding, ...]
    resolved_inputs_digest: TypedId
    metrics: Dict
    metrics_digest: TypedId
    status: OccurrenceStatus
    operation_id: str
    attachment_refs: Tuple[AttachmentRef, ...] = ()
    asset_version_id: Optional[TypedId] = None
    asset_type: Optional[str] = None
    asset_name: Optional[str] = None
    label_id: Optional[TypedId] = None
    display_version: Optional[str] = None
    finalization_attestation_ref: Optional[TypedId] = None
    projection_source_revision: int = 1
    projection_repair_epoch: int = 0
    readable_occurrence: Optional[ReadableManifestProjection] = None
    reference_epoch: int = 0
    committed_at: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.resolved_input_bindings, list):
            object.__setattr__(
                self, "resolved_input_bindings", tuple(self.resolved_input_bindings)
            )
        if isinstance(self.attachment_refs, list):
            object.__setattr__(self, "attachment_refs", tuple(self.attachment_refs))

        parse_schema_version(self.schema_version)
        _require_typed_id("environment_fingerprint", self.environment_fingerprint)
        _require_typed_id("occurrence_id", self.occurrence_id)
        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        object.__setattr__(
            self, "step_name", validate_segment(self.step_name, field_name="step_name")
        )
        _require_positive_int("attempt_no", self.attempt_no)
        _require_non_negative_int("fencing_token", self.fencing_token)
        object.__setattr__(
            self, "output_name", validate_segment(self.output_name, field_name="output_name")
        )
        _require_typed_id("committed_output_key", self.committed_output_key)

        recomputed_occurrence_id = compute_occurrence_id(
            self.run_id, self.step_name, self.attempt_no, self.output_name
        )
        if recomputed_occurrence_id != self.occurrence_id:
            raise InvalidOccurrenceRecordError(
                "occurrence_id does not match compute_occurrence_id(run_id, step_name, "
                f"attempt_no, output_name): expected {recomputed_occurrence_id.typed!r}, "
                f"got {self.occurrence_id.typed!r}"
            )
        recomputed_committed_output_key = compute_committed_output_key(
            self.run_id, self.step_name, self.output_name
        )
        if recomputed_committed_output_key != self.committed_output_key:
            raise InvalidOccurrenceRecordError(
                "committed_output_key does not match compute_committed_output_key(run_id, "
                f"step_name, output_name): expected {recomputed_committed_output_key.typed!r}, "
                f"got {self.committed_output_key.typed!r}"
            )

        _require_typed_id("resolved_inputs_digest", self.resolved_inputs_digest)
        recomputed_inputs_digest = compute_resolved_inputs_digest(self.resolved_input_bindings)
        if recomputed_inputs_digest != self.resolved_inputs_digest:
            raise InvalidOccurrenceRecordError(
                "resolved_inputs_digest does not match compute_resolved_inputs_digest("
                f"resolved_input_bindings): expected {recomputed_inputs_digest.typed!r}, got "
                f"{self.resolved_inputs_digest.typed!r}"
            )

        _require_typed_id("metrics_digest", self.metrics_digest)
        recomputed_metrics_digest = compute_metrics_digest(self.metrics)
        if recomputed_metrics_digest != self.metrics_digest:
            raise InvalidOccurrenceRecordError(
                "metrics_digest does not match compute_metrics_digest(metrics): expected "
                f"{recomputed_metrics_digest.typed!r}, got {self.metrics_digest.typed!r}"
            )

        if not all(isinstance(ref, AttachmentRef) for ref in self.attachment_refs):
            raise InvalidOccurrenceRecordError("attachment_refs must be a sequence of AttachmentRef")
        attachment_sort_keys = [(ref.attachment_kind, ref.logical_name) for ref in self.attachment_refs]
        if attachment_sort_keys != sorted(attachment_sort_keys):
            raise InvalidOccurrenceRecordError(
                "attachment_refs must be sorted by (attachment_kind, logical_name)"
            )
        if len(set(attachment_sort_keys)) != len(attachment_sort_keys):
            raise InvalidOccurrenceRecordError(
                "attachment_refs must be unique by (attachment_kind, logical_name)"
            )

        if not isinstance(self.status, OccurrenceStatus):
            raise InvalidOccurrenceRecordError(f"status must be an OccurrenceStatus, got {self.status!r}")
        _require_non_empty_str("operation_id", self.operation_id)
        _require_non_negative_int("projection_source_revision", self.projection_source_revision)
        _require_non_negative_int("projection_repair_epoch", self.projection_repair_epoch)
        _require_non_negative_int("reference_epoch", self.reference_epoch)
        if self.readable_occurrence is not None and not isinstance(
            self.readable_occurrence, ReadableManifestProjection
        ):
            raise InvalidOccurrenceRecordError(
                "readable_occurrence must be a ReadableManifestProjection or None"
            )

        is_bound_to_asset = self.status not in (
            OccurrenceStatus.PROVISIONAL,
            OccurrenceStatus.ABORTED,
        )
        if is_bound_to_asset:
            _require_typed_id("asset_version_id", self.asset_version_id)
            _require_non_empty_str("asset_type", self.asset_type)
            _require_non_empty_str("asset_name", self.asset_name)
            _require_typed_id("finalization_attestation_ref", self.finalization_attestation_ref)
        else:
            for field_name, value in (
                ("asset_version_id", self.asset_version_id),
                ("finalization_attestation_ref", self.finalization_attestation_ref),
            ):
                if value is not None:
                    raise InvalidOccurrenceRecordError(
                        f"{field_name} must be None while status is {self.status.value!r}"
                    )

        if self.asset_version_id is not None:
            object.__setattr__(self, "asset_type", validate_segment(self.asset_type, field_name="asset_type"))
            object.__setattr__(self, "asset_name", validate_segment(self.asset_name, field_name="asset_name"))
        elif self.asset_type is not None or self.asset_name is not None:
            raise InvalidOccurrenceRecordError(
                "asset_type/asset_name require asset_version_id to also be set"
            )

        if (self.label_id is None) != (self.display_version is None):
            raise InvalidOccurrenceRecordError(
                "label_id and display_version must both be set or both be None"
            )
        if self.label_id is not None:
            if self.asset_version_id is None:
                raise InvalidOccurrenceRecordError(
                    "label_id/display_version require asset_version_id to also be set"
                )
            _require_typed_id("label_id", self.label_id)
            object.__setattr__(
                self,
                "display_version",
                validate_segment(self.display_version, field_name="display_version"),
            )

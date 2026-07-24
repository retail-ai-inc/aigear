"""Firestore V2 namespace path builder (spec section 7).

V1 and V2 use fully isolated Firestore namespaces. All V2 collections live
under a single per-(project, pipeline) control document:

    aigear_projects/{project_name}/pipelines/{pipeline_version}/registries/v2

This module is the single place that assembles those paths; callers should
never string-concatenate a V2 Firestore path by hand. Document ID segments
that are computed as full SHA-256 identities (blob_id, asset_version_id,
label_id, occurrence_id, ...) accept a :class:`TypedId` (or the equivalent
``sha256:``-prefixed/bare string) and are stored using the bare hex form,
matching :mod:`aigear.management.v2.identifiers`. Human-assigned identifiers
(run_id, step_name, service_name, ...) go through the same segment validator
used for GCS paths (:mod:`aigear.management.v2.naming`), since a bare hex
digest is also a valid segment under that same ASCII rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from aigear.management.v2.identifiers import SHA256_TYPED_PREFIX, TypedId
from aigear.management.v2.naming import validate_segment

__all__ = ["InvalidFirestorePathError", "FirestorePathsV2"]


class InvalidFirestorePathError(ValueError):
    """Raised for malformed dynamic segments in a V2 Firestore path."""


def _coerce_typed_id(value: "TypedId | str") -> TypedId:
    if isinstance(value, TypedId):
        return value
    if isinstance(value, str) and value.startswith(SHA256_TYPED_PREFIX):
        return TypedId.from_typed(value)
    return TypedId.from_bare(value)


def _validate_positive_int(field_name: str, value: int) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidFirestorePathError(
            f"{field_name} must be a positive int, got {value!r}"
        )
    return str(value)


@dataclass(frozen=True)
class FirestorePathsV2:
    """Path builder rooted at ``aigear_projects/{project}/pipelines/{pipeline}/registries/v2``."""

    project_name: str
    pipeline_version: str

    def __post_init__(self) -> None:
        validate_segment(self.project_name, field_name="project_name")
        validate_segment(self.pipeline_version, field_name="pipeline_version")

    # ── root / control document ─────────────────────────────────────────

    @property
    def control_document(self) -> str:
        """The ``registries/v2`` control document path (spec section 7)."""
        return (
            f"aigear_projects/{self.project_name}/pipelines/{self.pipeline_version}/"
            "registries/v2"
        )

    def _under_root(self, *parts: str) -> str:
        return "/".join((self.control_document, *parts))

    # ── blobs ────────────────────────────────────────────────────────────

    def blobs_collection(self) -> str:
        return self._under_root("blobs")

    def blob_document(self, blob_id: "TypedId | str") -> str:
        return self._under_root("blobs", _coerce_typed_id(blob_id).bare)

    def blob_location_revisions_collection(self, blob_id: "TypedId | str") -> str:
        return self._under_root("blobs", _coerce_typed_id(blob_id).bare, "location_revisions")

    def blob_location_revision_document(self, blob_id: "TypedId | str", revision: int) -> str:
        return self._under_root(
            "blobs",
            _coerce_typed_id(blob_id).bare,
            "location_revisions",
            _validate_positive_int("revision", revision),
        )

    def blob_claim_document(self, blob_id: "TypedId | str") -> str:
        return self._under_root("blob_claims", _coerce_typed_id(blob_id).bare)

    # ── asset versions / attestations / policy ──────────────────────────

    def asset_version_document(self, asset_version_id: "TypedId | str") -> str:
        return self._under_root("asset_versions", _coerce_typed_id(asset_version_id).bare)

    def attestation_document(self, attestation_id: "TypedId | str") -> str:
        return self._under_root("attestations", _coerce_typed_id(attestation_id).bare)

    def policy_decision_head_document(self, asset_version_id: "TypedId | str") -> str:
        return self._under_root(
            "policy_decision_heads", _coerce_typed_id(asset_version_id).bare
        )

    def policy_decision_epoch_document(self, subject_epoch_key: str) -> str:
        return self._under_root(
            "policy_decision_epochs",
            validate_segment(subject_epoch_key, field_name="subject_epoch_key"),
        )

    # ── labels ───────────────────────────────────────────────────────────

    def label_document(self, label_id: "TypedId | str") -> str:
        return self._under_root("labels", _coerce_typed_id(label_id).bare)

    # ── runs / steps / attempts / committed outputs ─────────────────────

    def run_document(self, run_id: str) -> str:
        return self._under_root("runs", validate_segment(run_id, field_name="run_id"))

    def run_input_binding_document(self, run_id: str, input_name: str) -> str:
        return self._under_root(
            "runs",
            validate_segment(run_id, field_name="run_id"),
            "input_bindings",
            validate_segment(input_name, field_name="input_name"),
        )

    def run_step_instance_document(self, run_id: str, step_key: str) -> str:
        return self._under_root(
            "runs",
            validate_segment(run_id, field_name="run_id"),
            "step_instances",
            validate_segment(step_key, field_name="step_key"),
        )

    def run_attempt_document(self, run_id: str, step_key: str, attempt_no: int) -> str:
        return self._under_root(
            "runs",
            validate_segment(run_id, field_name="run_id"),
            "step_instances",
            validate_segment(step_key, field_name="step_key"),
            "attempts",
            _validate_positive_int("attempt_no", attempt_no),
        )

    def run_committed_output_document(self, run_id: str, committed_output_key: str) -> str:
        return self._under_root(
            "runs",
            validate_segment(run_id, field_name="run_id"),
            "committed_outputs",
            validate_segment(committed_output_key, field_name="committed_output_key"),
        )

    # ── occurrences / lineage / component / attachment edges ────────────

    def occurrence_document(self, occurrence_id: "TypedId | str") -> str:
        return self._under_root("occurrences", _coerce_typed_id(occurrence_id).bare)

    def lineage_edge_document(self, edge_id: str) -> str:
        return self._under_root("lineage_edges", validate_segment(edge_id, field_name="edge_id"))

    def component_edge_document(self, component_edge_id: str) -> str:
        return self._under_root(
            "component_edges",
            validate_segment(component_edge_id, field_name="component_edge_id"),
        )

    def attachment_edge_document(self, attachment_edge_id: str) -> str:
        return self._under_root(
            "attachment_edges",
            validate_segment(attachment_edge_id, field_name="attachment_edge_id"),
        )

    # ── aliases / releases / services ────────────────────────────────────

    def alias_document(self, alias_id: str) -> str:
        return self._under_root("aliases", validate_segment(alias_id, field_name="alias_id"))

    def release_document(self, release_id: str) -> str:
        return self._under_root(
            "releases", validate_segment(release_id, field_name="release_id")
        )

    def service_release_state_document(self, service_name: str) -> str:
        return self._under_root(
            "services",
            validate_segment(service_name, field_name="service_name"),
            "release_state",
            "current",
        )

    def release_operation_document(self, operation_id: str) -> str:
        return self._under_root(
            "release_operations",
            validate_segment(operation_id, field_name="operation_id"),
        )

    def restore_operation_document(self, operation_id: str) -> str:
        return self._under_root(
            "restore_operations",
            validate_segment(operation_id, field_name="operation_id"),
        )

    def export_operation_document(self, export_id: str) -> str:
        return self._under_root(
            "export_operations", validate_segment(export_id, field_name="export_id")
        )

    def pin_document(self, pin_id: str) -> str:
        return self._under_root("pins", validate_segment(pin_id, field_name="pin_id"))

    # ── operations / outbox / events / tombstones / migration / reconcile ─

    def operation_document(self, idempotency_key_hash: str) -> str:
        return self._under_root(
            "operations",
            validate_segment(idempotency_key_hash, field_name="idempotency_key_hash"),
        )

    def outbox_document(self, event_id: str) -> str:
        return self._under_root("outbox", validate_segment(event_id, field_name="event_id"))

    def event_document(self, event_id: str) -> str:
        return self._under_root("events", validate_segment(event_id, field_name="event_id"))

    def tombstone_document(self, identity_hash: str) -> str:
        return self._under_root(
            "tombstones", validate_segment(identity_hash, field_name="identity_hash")
        )

    def migration_map_document(self, legacy_doc_id_hash: str) -> str:
        return self._under_root(
            "migration_map",
            validate_segment(legacy_doc_id_hash, field_name="legacy_doc_id_hash"),
        )

    def reconcile_checkpoint_document(self, job_name: str) -> str:
        return self._under_root(
            "reconcile_checkpoints", validate_segment(job_name, field_name="job_name")
        )

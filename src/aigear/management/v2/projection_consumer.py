"""Readable projection consumer (spec sections 6.3/6.5 step 8).

Implements the minimal outbox-consumer Saga step spec 6.3 describes, against
:class:`~aigear.management.v2.fake_registry.FakeRegistryV2` and
:class:`~aigear.management.v2.fake_gcs.FakeGcsClient`: render a
:class:`ProjectionEvent` for one subject (a Label's ``manifest.json``, or a
committed Occurrence's ``occurrence.json``) into RFC 8785 canonical bytes,
CAS/create-only write it to GCS, and update that subject's
``ReadableManifestProjection`` delivery state to ``ready``.

Deliberately out of scope (this is a Saga *step*, not the full controller):

- The separate mutation-lease/``projection-writer-sa`` role split spec 6.3
  describes (a dedicated principal that can write GCS but not Firestore).
  This module runs as one caller with both capabilities, which is exactly
  what T22's ``finalize_step_outputs``/T23's ``cancel_run`` already do for
  their own writes -- consistent with this package's fake-registry-only
  scope for Phase B.
- ``projection_mutation_fence`` concurrency control across *parallel*
  consumers for the same subject: this module is a single synchronous call,
  so there is nothing to fence against; the fence counter is still
  incremented on every successful apply so a real implementation slotting
  in a real lease later has a consistent value to build on.
- The full ``pending -> applying -> ready`` / ``stale`` / ``conflict``
  delivery lattice; this module only ever transitions straight to
  ``ready`` on success. Reconcile-driven repair (``projection_repair_epoch``
  bumps for a projection that was deleted or corrupted out of band) is
  future work.

Ordering/idempotency (spec 6.3's "consumer 先读取 subject 的 desired/applied
revision" rule, and this task's "同一 projection_source_revision 不重复触发
投递" requirement) is implemented by comparing ``event.projection_source_
revision`` against the *subject record's own* ``projection_source_revision``
field (the authoritative "desired" revision) and against the delivery
state's ``applied_source_revision``/``applied_repair_epoch`` (what has
already been rendered): an event behind desired is a no-op, one ahead of
desired is an ordering error, and one that has already been fully applied at
its exact revision/repair-epoch is a no-op (the self-loop guard).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from aigear.management.v2.canonical import canonicalize_json, digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.fake_gcs import FakeGcsClient, GcsObjectSnapshot
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.label import ReadableManifestProjection
from aigear.management.v2.records.occurrence import OccurrenceStatus

__all__ = [
    "ProjectionConsumerError",
    "ProjectionKind",
    "compute_projection_event_id",
    "ProjectionEvent",
    "consume_projection_event",
]


class ProjectionConsumerError(ValueError):
    """Raised when a projection event cannot be rendered/applied as requested."""


class ProjectionKind(str, Enum):
    """Closed enum per spec 6.3: ``subject_id`` is a ``label_id`` for
    ``asset_manifest``, a ``committed_output_key`` for ``committed_run_output``."""

    ASSET_MANIFEST = "asset_manifest"
    COMMITTED_RUN_OUTPUT = "committed_run_output"


def compute_projection_event_id(
    kind: ProjectionKind,
    subject_id: TypedId,
    projection_schema_version: str,
    projection_source_revision: int,
    projection_repair_epoch: int,
) -> TypedId:
    """``event_id`` per spec 6.3's projection-event JCS payload."""
    payload = [
        "aigear.projection-event.v2",
        kind.value,
        subject_id.typed,
        projection_schema_version,
        projection_source_revision,
        projection_repair_epoch,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def _require_non_negative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectionConsumerError(f"{field_name} must be a non-negative int, got {value!r}")


@dataclass(frozen=True)
class ProjectionEvent:
    """One outbox event asking the consumer to render ``subject_id`` at
    ``projection_source_revision``/``projection_repair_epoch`` (spec 6.3)."""

    kind: ProjectionKind
    subject_id: TypedId
    projection_schema_version: str
    projection_source_revision: int
    projection_repair_epoch: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProjectionKind):
            raise ProjectionConsumerError(f"kind must be a ProjectionKind, got {self.kind!r}")
        if not isinstance(self.subject_id, TypedId):
            raise ProjectionConsumerError(f"subject_id must be a TypedId, got {type(self.subject_id)!r}")
        parse_schema_version(self.projection_schema_version)
        _require_non_negative_int("projection_source_revision", self.projection_source_revision)
        _require_non_negative_int("projection_repair_epoch", self.projection_repair_epoch)

    @property
    def event_id(self) -> TypedId:
        return compute_projection_event_id(
            self.kind, self.subject_id, self.projection_schema_version,
            self.projection_source_revision, self.projection_repair_epoch,
        )


def _classify(desired_revision: int, projection: ReadableManifestProjection, event: ProjectionEvent) -> str:
    """Returns ``"skip"`` or ``"apply"`` per spec 6.3's ordering rule."""
    if event.projection_source_revision > desired_revision:
        raise ProjectionConsumerError(
            f"event projection_source_revision {event.projection_source_revision} is ahead of "
            f"the subject's own desired revision {desired_revision}; schema/order error"
        )
    if event.projection_source_revision < desired_revision:
        return "skip"
    if (
        projection.status == "ready"
        and projection.applied_source_revision == event.projection_source_revision
        and projection.applied_repair_epoch == event.projection_repair_epoch
    ):
        return "skip"
    return "apply"


def _finalize_projection_bytes(payload: dict) -> bytes:
    """Spec 6.3: final bytes = JCS(payload + its own payload_digest)."""
    payload_digest = digest_sha256_of_jcs(payload)
    return canonicalize_json({**payload, "payload_digest": payload_digest})


def _write_projection(gcs: FakeGcsClient, object_name: str, final_bytes: bytes, *, current_generation: int) -> GcsObjectSnapshot:
    """CAS-write (create-only when ``current_generation == 0``).

    A retry that finds identical bytes already live -- at any generation --
    is treated as already-applied rather than an error (spec 6.3's
    GCS-write/Firestore-acknowledgement retry rule: "内容相同则复用...内容
    不同则冲突告警"). A create-only write (``current_generation == 0``) that
    finds *different* bytes already live is a hard identity conflict, raised
    directly instead of relying on the less specific generation-precondition
    error. A CAS-update with different live bytes is left to
    :meth:`FakeGcsClient.put_object`'s own precondition check, which raises
    if the caller's expected generation is stale.
    """
    live = gcs.get_live_object(object_name)
    if live is not None:
        if live.data == final_bytes:
            return live
        if current_generation == 0:
            raise ProjectionConsumerError(
                f"projection object {object_name!r} already exists with different content; "
                "consistency conflict"
            )
    return gcs.put_object(object_name, final_bytes, if_generation_match=current_generation)


def _envelope(event: ProjectionEvent) -> dict:
    return {
        "projection_kind": event.kind.value,
        "subject_id": event.subject_id.typed,
        "projection_schema_version": event.projection_schema_version,
        "projection_source_revision": event.projection_source_revision,
        "projection_repair_epoch": event.projection_repair_epoch,
    }


def _mark_ready(
    projection: ReadableManifestProjection, snapshot: GcsObjectSnapshot, event: ProjectionEvent
) -> ReadableManifestProjection:
    return replace(
        projection,
        status="ready",
        projection_mutation_fence=projection.projection_mutation_fence + 1,
        observed_generation=snapshot.generation,
        observed_content_sha256=snapshot.sha256,
        observed_source_revision=event.projection_source_revision,
        observed_repair_epoch=event.projection_repair_epoch,
        applied_source_revision=event.projection_source_revision,
        applied_repair_epoch=event.projection_repair_epoch,
    )


def _consume_asset_manifest_event(
    registry: FakeRegistryV2, gcs: FakeGcsClient, layout: GcsLayoutV2, event: ProjectionEvent
) -> ReadableManifestProjection:
    label = registry.get_label(event.subject_id)
    if label is None:
        raise ProjectionConsumerError(f"no Label found for label_id {event.subject_id.typed!r}")

    projection = label.readable_manifest
    if _classify(label.projection_source_revision, projection, event) == "skip":
        return projection

    asset_version = registry.get_asset_version(label.asset_version_id)
    if asset_version is None:
        raise ProjectionConsumerError(
            f"Label {label.label_id.typed!r} points to missing AssetVersion "
            f"{label.asset_version_id.typed!r}"
        )

    components = []
    for component in asset_version.components:
        blob = registry.get_blob(component.blob_id)
        if blob is None:
            raise ProjectionConsumerError(f"no Blob found for blob_id {component.blob_id.typed!r}")
        components.append(
            {
                "role": component.role,
                "logical_name": component.logical_name,
                "blob_id": component.blob_id.typed,
                "object_uri": layout.to_uri(blob.object_name),
                "generation": blob.generation,
                "location_revision": blob.current_location_revision,
                "sha256": blob.sha256,
                "size_bytes": blob.size_bytes,
            }
        )

    payload = {
        **_envelope(event),
        "label_id": label.label_id.typed,
        "display_version": label.display_version,
        "asset_version_id": asset_version.asset_version_id.typed,
        "manifest_digest": asset_version.manifest_digest.typed,
        "components": components,
        "schema_contract_digest": asset_version.schema_contract_digest.typed,
        "runtime_contract_digest": asset_version.runtime_contract_digest.typed,
        "manifest_integrity_attestation_ref": asset_version.manifest_integrity_attestation_ref.typed,
    }
    final_bytes = _finalize_projection_bytes(payload)

    object_name = layout.asset_projection(label.asset_type, label.asset_name, label.display_version)
    current_generation = int(projection.observed_generation) if projection.observed_generation else 0
    snapshot = _write_projection(gcs, object_name, final_bytes, current_generation=current_generation)

    updated = _mark_ready(projection, snapshot, event)
    registry.put_label(replace(label, readable_manifest=updated))
    return updated


def _consume_committed_run_output_event(
    registry: FakeRegistryV2, gcs: FakeGcsClient, layout: GcsLayoutV2, event: ProjectionEvent
) -> ReadableManifestProjection:
    occurrence = registry.get_committed_occurrence_by_output_key(event.subject_id)
    if occurrence is None or occurrence.status != OccurrenceStatus.COMMITTED:
        raise ProjectionConsumerError(
            f"no committed Occurrence found for committed_output_key {event.subject_id.typed!r}"
        )

    object_name = layout.run_projection(occurrence.run_id, occurrence.step_name, occurrence.output_name)
    projection = occurrence.readable_occurrence or ReadableManifestProjection.initial(
        uri=layout.to_uri(object_name)
    )
    if _classify(occurrence.projection_source_revision, projection, event) == "skip":
        return projection

    asset_manifest_uri = layout.to_uri(
        layout.asset_projection(occurrence.asset_type, occurrence.asset_name, occurrence.display_version)
    )
    input_occurrence_ids = sorted(
        binding.occurrence_id.typed
        for binding in occurrence.resolved_input_bindings
        if binding.occurrence_id is not None
    )
    payload = {
        **_envelope(event),
        "occurrence_id": occurrence.occurrence_id.typed,
        "run_id": occurrence.run_id,
        "step_name": occurrence.step_name,
        "attempt_no": occurrence.attempt_no,
        "fencing_token": occurrence.fencing_token,
        "output_name": occurrence.output_name,
        "asset_type": occurrence.asset_type,
        "asset_name": occurrence.asset_name,
        "label_id": occurrence.label_id.typed if occurrence.label_id is not None else None,
        "display_version": occurrence.display_version,
        "asset_version_id": occurrence.asset_version_id.typed,
        "asset_manifest_uri": asset_manifest_uri,
        "input_occurrence_ids": input_occurrence_ids,
        "committed_at": occurrence.committed_at,
    }
    final_bytes = _finalize_projection_bytes(payload)

    # Spec 6.3: committed run-output projection content is immutable and
    # must always be create-only, unlike the asset manifest's CAS updates.
    snapshot = _write_projection(gcs, object_name, final_bytes, current_generation=0)

    updated = _mark_ready(projection, snapshot, event)
    registry.put_occurrence(replace(occurrence, readable_occurrence=updated))
    return updated


def consume_projection_event(
    registry: FakeRegistryV2, gcs: FakeGcsClient, layout: GcsLayoutV2, event: ProjectionEvent
) -> ReadableManifestProjection:
    """Render and deliver one projection event (spec 6.3/6.5 step 8)."""
    if event.kind == ProjectionKind.ASSET_MANIFEST:
        return _consume_asset_manifest_event(registry, gcs, layout, event)
    return _consume_committed_run_output_event(registry, gcs, layout, event)

"""Lease/fence-driven readable projection Saga (spec sections 6.3/6.5).

Implements the minimal outbox-consumer Saga step spec 6.3 describes, against
:class:`~aigear.management.v2.fake_registry.FakeRegistryV2` and
:class:`~aigear.management.v2.fake_gcs.FakeGcsClient`: render a
:class:`ProjectionEvent` for one subject (a Label's ``manifest.json``, or a
committed Occurrence's ``occurrence.json``) into RFC 8785 canonical bytes,
CAS/create-only write it to GCS, and update that subject's
``ReadableManifestProjection`` delivery state to ``ready``.

The bullets below describe the original T26 prototype limitations; Phase B
production hardening now implements them through transactional outbox records,
dual leases/fences, exact-generation completion verification, repair epochs,
and the bounded worker. Continuous inventory/SLO monitoring remains Phase D.

Historical prototype scope (retained to explain the evolution):

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
from datetime import datetime, timedelta, timezone
from typing import Optional

from aigear.management.v2.canonical import canonicalize_json, digest_sha256_of_jcs
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.fake_gcs import GcsObjectSnapshot
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.label import ReadableManifestProjection
from aigear.management.v2.records.occurrence import OccurrenceStatus
from aigear.management.v2.records.outbox import (
    OutboxEventRecord,
    OutboxStatus,
    ProjectionKind,
    compute_projection_event_id,
)

__all__ = [
    "ProjectionConsumerError",
    "ProjectionKind",
    "compute_projection_event_id",
    "ProjectionEvent",
    "ProjectionMutationTask",
    "ProjectionCompletionEvidence",
    "acquire_projection_task",
    "render_projection_task",
    "verify_projection_completion",
    "acknowledge_projection_task",
    "request_projection_repair",
    "consume_projection_event",
]


class ProjectionConsumerError(ValueError):
    """Raised when a projection event cannot be rendered/applied as requested."""


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


@dataclass(frozen=True)
class ProjectionMutationTask:
    event: ProjectionEvent
    worker_principal: str
    delivery_fencing_token: int
    projection_mutation_fence: int
    expected_observed_generation: str
    lease_expires_at: str


@dataclass(frozen=True)
class ProjectionCompletionEvidence:
    event_id: TypedId
    worker_principal: str
    delivery_fencing_token: int
    projection_mutation_fence: int
    object_name: str
    generation: str
    content_sha256: str


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


def _write_projection(
    gcs: GcsClientV2,
    object_name: str,
    final_bytes: bytes,
    *,
    current_generation: int,
    immutable_path: bool,
) -> GcsObjectSnapshot:
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
        if immutable_path or current_generation == 0:
            raise ProjectionConsumerError(
                f"projection object {object_name!r} already exists with different content; "
                "consistency conflict"
            )
    # A known projection may have been deleted out of band.  Re-creation must
    # use create-only, never its now-stale observed generation.
    expected_generation = current_generation if live is not None else 0
    return gcs.put_object(
        object_name, final_bytes, if_generation_match=expected_generation
    )


def _envelope(event: ProjectionEvent) -> dict:
    return {
        "projection_kind": event.kind.value,
        "subject_id": event.subject_id.typed,
        "projection_schema_version": event.projection_schema_version,
        "projection_source_revision": event.projection_source_revision,
        "projection_repair_epoch": event.projection_repair_epoch,
    }


def _subject_state(registry, layout: GcsLayoutV2, event: ProjectionEvent):
    if event.kind == ProjectionKind.ASSET_MANIFEST:
        label = registry.get_label(event.subject_id)
        if label is None:
            raise ProjectionConsumerError(
                f"no Label found for label_id {event.subject_id.typed!r}"
            )
        return (
            label,
            label.projection_source_revision,
            label.projection_repair_epoch,
            label.readable_manifest,
        )
    occurrence = registry.get_committed_occurrence_by_output_key(event.subject_id)
    if occurrence is None or occurrence.status != OccurrenceStatus.COMMITTED:
        raise ProjectionConsumerError(
            f"no committed Occurrence found for committed_output_key {event.subject_id.typed!r}"
        )
    projection = occurrence.readable_occurrence or ReadableManifestProjection.initial(
        uri=layout.to_uri(
            layout.run_projection(
                occurrence.run_id, occurrence.step_name, occurrence.output_name
            )
        )
    )
    return (
        occurrence,
        occurrence.projection_source_revision,
        occurrence.projection_repair_epoch,
        projection,
    )


def _put_subject_projection(registry, event, subject, projection) -> None:
    if event.kind == ProjectionKind.ASSET_MANIFEST:
        registry.put_label(replace(subject, readable_manifest=projection))
    else:
        registry.put_occurrence(replace(subject, readable_occurrence=projection))


def acquire_projection_task(
    registry,
    layout: GcsLayoutV2,
    event: ProjectionEvent,
    *,
    worker_principal: str,
    now: datetime,
    lease_ttl: timedelta = timedelta(minutes=2),
) -> Optional[ProjectionMutationTask]:
    """Atomically lease one outbox event and its projection subject."""
    if now.tzinfo is None or now.utcoffset() is None or lease_ttl <= timedelta(0):
        raise ProjectionConsumerError("now must be aware and lease_ttl must be positive")
    if not worker_principal:
        raise ProjectionConsumerError("worker_principal must be non-empty")
    subject, desired_revision, desired_repair_epoch, projection = _subject_state(
        registry, layout, event
    )
    classification = _classify(desired_revision, projection, event)
    outbox = registry.get_outbox_event(event.event_id)
    if outbox is None:
        raise ProjectionConsumerError("projection event is not present in the transactional outbox")
    if (
        outbox.kind != event.kind
        or outbox.subject_id != event.subject_id
        or outbox.projection_source_revision != event.projection_source_revision
        or outbox.projection_repair_epoch != event.projection_repair_epoch
    ):
        raise ProjectionConsumerError("outbox event identity does not match the delivery request")
    if outbox.status == OutboxStatus.DELIVERED or classification == "skip":
        if outbox.status != OutboxStatus.DELIVERED:
            registry.put_outbox_event(
                replace(
                    outbox,
                    status=OutboxStatus.DELIVERED,
                    delivered_at=now.isoformat(),
                    next_attempt_at=None,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
        return None

    if outbox.status == OutboxStatus.DELIVERING:
        expires_at = datetime.fromisoformat(outbox.lease_expires_at)
        if now < expires_at:
            if outbox.lease_owner != worker_principal:
                raise ProjectionConsumerError("projection outbox event has an active delivery lease")
            if (
                projection.status != "applying"
                or projection.mutation_owner_principal != worker_principal
                or projection.mutation_lease_expires_at != outbox.lease_expires_at
            ):
                raise ProjectionConsumerError(
                    "outbox and projection mutation leases are inconsistent"
                )
            return ProjectionMutationTask(
                event=event,
                worker_principal=worker_principal,
                delivery_fencing_token=outbox.delivery_fencing_token,
                projection_mutation_fence=projection.projection_mutation_fence,
                expected_observed_generation=projection.observed_generation or "0",
                lease_expires_at=outbox.lease_expires_at,
            )

    if projection.status == "applying" and projection.mutation_lease_expires_at:
        projection_expiry = datetime.fromisoformat(projection.mutation_lease_expires_at)
        if now < projection_expiry and projection.mutation_owner_principal != worker_principal:
            raise ProjectionConsumerError("projection subject has an active mutation lease")

    lease_expires_at = (now + lease_ttl).isoformat()
    delivery_fence = outbox.delivery_fencing_token + 1
    mutation_fence = projection.projection_mutation_fence + 1
    registry.put_outbox_event(
        replace(
            outbox,
            status=OutboxStatus.DELIVERING,
            delivery_attempts=outbox.delivery_attempts + 1,
            delivery_fencing_token=delivery_fence,
            lease_owner=worker_principal,
            lease_expires_at=lease_expires_at,
            next_attempt_at=None,
            last_error=None,
        )
    )
    applying = replace(
        projection,
        status="applying",
        projection_mutation_fence=mutation_fence,
        mutation_owner_principal=worker_principal,
        mutation_lease_expires_at=lease_expires_at,
        last_error=None,
    )
    _put_subject_projection(registry, event, subject, applying)
    return ProjectionMutationTask(
        event=event,
        worker_principal=worker_principal,
        delivery_fencing_token=delivery_fence,
        projection_mutation_fence=mutation_fence,
        expected_observed_generation=projection.observed_generation or "0",
        lease_expires_at=lease_expires_at,
    )


def render_projection_task(
    registry, gcs: GcsClientV2, layout: GcsLayoutV2, task: ProjectionMutationTask
) -> ProjectionCompletionEvidence:
    """Render and CAS-write GCS; this function never writes Firestore."""
    event = task.event
    if event.kind == ProjectionKind.ASSET_MANIFEST:
        label = registry.get_label(event.subject_id)
        if label is None:
            raise ProjectionConsumerError("projection Label disappeared")
        asset_version = registry.get_asset_version(label.asset_version_id)
        if asset_version is None:
            raise ProjectionConsumerError("projection AssetVersion disappeared")
        components = []
        for component in asset_version.components:
            blob = registry.get_blob(component.blob_id)
            if blob is None:
                raise ProjectionConsumerError(
                    f"no Blob found for blob_id {component.blob_id.typed!r}"
                )
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
        object_name = layout.asset_projection(
            label.asset_type, label.asset_name, label.display_version
        )
    else:
        occurrence = registry.get_committed_occurrence_by_output_key(event.subject_id)
        if occurrence is None or occurrence.status != OccurrenceStatus.COMMITTED:
            raise ProjectionConsumerError("committed projection Occurrence disappeared")
        asset_manifest_uri = layout.to_uri(
            layout.asset_projection(
                occurrence.asset_type, occurrence.asset_name, occurrence.display_version
            )
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
            "label_id": occurrence.label_id.typed if occurrence.label_id else None,
            "display_version": occurrence.display_version,
            "asset_version_id": occurrence.asset_version_id.typed,
            "asset_manifest_uri": asset_manifest_uri,
            "input_occurrence_ids": sorted(
                binding.occurrence_id.typed
                for binding in occurrence.resolved_input_bindings
                if binding.occurrence_id is not None
            ),
            "committed_at": occurrence.committed_at,
        }
        object_name = layout.run_projection(
            occurrence.run_id, occurrence.step_name, occurrence.output_name
        )
    final_bytes = _finalize_projection_bytes(payload)
    snapshot = _write_projection(
        gcs,
        object_name,
        final_bytes,
        current_generation=int(task.expected_observed_generation),
        immutable_path=event.kind == ProjectionKind.COMMITTED_RUN_OUTPUT,
    )
    return ProjectionCompletionEvidence(
        event_id=event.event_id,
        worker_principal=task.worker_principal,
        delivery_fencing_token=task.delivery_fencing_token,
        projection_mutation_fence=task.projection_mutation_fence,
        object_name=object_name,
        generation=snapshot.generation,
        content_sha256=snapshot.sha256,
    )


def acknowledge_projection_task(
    registry,
    layout: GcsLayoutV2,
    task: ProjectionMutationTask,
    evidence: ProjectionCompletionEvidence,
    *,
    now: datetime,
) -> ReadableManifestProjection:
    """Fence-check completion evidence and atomically acknowledge delivery."""
    if evidence.event_id != task.event.event_id:
        raise ProjectionConsumerError("projection completion event_id mismatch")
    outbox = registry.get_outbox_event(task.event.event_id)
    if (
        outbox is None
        or outbox.status != OutboxStatus.DELIVERING
        or outbox.delivery_fencing_token != evidence.delivery_fencing_token
        or outbox.lease_owner != evidence.worker_principal
    ):
        raise ProjectionConsumerError("stale or unauthorized projection completion")
    subject, desired_revision, desired_repair_epoch, projection = _subject_state(
        registry, layout, task.event
    )
    if (
        projection.status != "applying"
        or projection.projection_mutation_fence != evidence.projection_mutation_fence
        or projection.mutation_owner_principal != evidence.worker_principal
    ):
        raise ProjectionConsumerError("projection mutation fence no longer owns the subject")
    is_current = (
        desired_revision == task.event.projection_source_revision
        and desired_repair_epoch == task.event.projection_repair_epoch
    )
    updated = replace(
        projection,
        status="ready" if is_current else "stale",
        observed_generation=evidence.generation,
        observed_content_sha256=evidence.content_sha256,
        observed_source_revision=task.event.projection_source_revision,
        observed_repair_epoch=task.event.projection_repair_epoch,
        applied_source_revision=(
            task.event.projection_source_revision
            if is_current
            else projection.applied_source_revision
        ),
        applied_repair_epoch=(
            task.event.projection_repair_epoch
            if is_current
            else projection.applied_repair_epoch
        ),
        mutation_owner_principal=None,
        mutation_lease_expires_at=None,
        last_error=None,
    )
    _put_subject_projection(registry, task.event, subject, updated)
    registry.put_outbox_event(
        replace(
            outbox,
            status=OutboxStatus.DELIVERED,
            lease_owner=None,
            lease_expires_at=None,
            delivered_at=now.isoformat(),
            next_attempt_at=None,
        )
    )
    return updated


def verify_projection_completion(
    registry,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    task: ProjectionMutationTask,
    evidence: ProjectionCompletionEvidence,
) -> None:
    """Verify worker evidence against the exact GCS generation before ACK.

    The projection writer may be a separate principal without Firestore write
    access.  Consequently its completion envelope is never trusted by itself:
    the controller derives the only valid object name from the Registry
    subject and reads the exact reported generation before allowing the
    transactional acknowledgement.
    """
    if (
        evidence.event_id != task.event.event_id
        or evidence.worker_principal != task.worker_principal
        or evidence.delivery_fencing_token != task.delivery_fencing_token
        or evidence.projection_mutation_fence != task.projection_mutation_fence
    ):
        raise ProjectionConsumerError("projection completion does not match the leased task")

    subject, _desired, _repair, _projection = _subject_state(registry, layout, task.event)
    if task.event.kind == ProjectionKind.ASSET_MANIFEST:
        expected_object_name = layout.asset_projection(
            subject.asset_type, subject.asset_name, subject.display_version
        )
    else:
        expected_object_name = layout.run_projection(
            subject.run_id, subject.step_name, subject.output_name
        )
    if evidence.object_name != expected_object_name:
        raise ProjectionConsumerError(
            "projection completion object_name does not match the Registry-derived path"
        )

    snapshot = gcs.get_object(evidence.object_name, generation=evidence.generation)
    if snapshot.sha256 != evidence.content_sha256:
        raise ProjectionConsumerError(
            "projection completion digest does not match the exact GCS generation"
        )


def _mark_projection_failure(registry, layout, task, error: BaseException, now: datetime) -> None:
    outbox = registry.get_outbox_event(task.event.event_id)
    subject, _desired, _repair, projection = _subject_state(registry, layout, task.event)
    conflict = isinstance(error, ProjectionConsumerError) and "consistency conflict" in str(error)
    if outbox is not None and outbox.delivery_fencing_token == task.delivery_fencing_token:
        terminal = conflict or outbox.delivery_attempts >= 10
        registry.put_outbox_event(
            replace(
                outbox,
                status=OutboxStatus.DEAD_LETTER if terminal else OutboxStatus.FAILED,
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=None if terminal else (now + timedelta(seconds=30)).isoformat(),
                last_error=str(error),
            )
        )
    if projection.projection_mutation_fence == task.projection_mutation_fence:
        _put_subject_projection(
            registry,
            task.event,
            subject,
            replace(
                projection,
                status="conflict" if conflict else "failed",
                mutation_owner_principal=None,
                mutation_lease_expires_at=None,
                last_error=str(error),
            ),
        )


def request_projection_repair(
    registry,
    layout: GcsLayoutV2,
    *,
    kind: ProjectionKind,
    subject_id: TypedId,
    expected_source_revision: int,
    expected_repair_epoch: int,
    requested_by: str,
    reason: str,
    now: datetime,
) -> ProjectionEvent:
    """Atomically fence and enqueue a same-source-revision projection repair."""
    if not requested_by or not reason:
        raise ProjectionConsumerError("projection repair requires requested_by and reason")
    current_event = ProjectionEvent(
        kind=kind,
        subject_id=subject_id,
        projection_schema_version="1.0",
        projection_source_revision=expected_source_revision,
        projection_repair_epoch=expected_repair_epoch,
    )
    subject, desired_revision, desired_repair_epoch, projection = _subject_state(
        registry, layout, current_event
    )
    if (
        desired_revision != expected_source_revision
        or desired_repair_epoch != expected_repair_epoch
    ):
        raise ProjectionConsumerError("projection repair fence is stale")
    if projection.status == "applying":
        raise ProjectionConsumerError("cannot request repair while projection mutation is applying")

    repair_epoch = desired_repair_epoch + 1
    event = ProjectionEvent(
        kind=kind,
        subject_id=subject_id,
        projection_schema_version=projection.projection_schema_version,
        projection_source_revision=desired_revision,
        projection_repair_epoch=repair_epoch,
    )
    pending_projection = replace(
        projection,
        status="pending",
        mutation_owner_principal=None,
        mutation_lease_expires_at=None,
        last_error=f"repair requested by {requested_by}: {reason}",
    )
    if kind == ProjectionKind.ASSET_MANIFEST:
        registry.put_label(
            replace(
                subject,
                projection_repair_epoch=repair_epoch,
                readable_manifest=pending_projection,
            )
        )
    else:
        registry.put_occurrence(
            replace(
                subject,
                projection_repair_epoch=repair_epoch,
                readable_occurrence=pending_projection,
            )
        )
    registry.put_outbox_event(
        OutboxEventRecord.pending(
            schema_version=subject.schema_version,
            kind=kind,
            subject_id=subject_id,
            projection_schema_version=event.projection_schema_version,
            projection_source_revision=desired_revision,
            projection_repair_epoch=repair_epoch,
            created_at=now.isoformat(),
        )
    )
    return event


def consume_projection_event(
    registry: FakeRegistryV2,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    event: ProjectionEvent,
    *,
    worker_principal: str = "projection-writer@test",
    now: Optional[datetime] = None,
) -> ReadableManifestProjection:
    """Compatibility wrapper around lease -> GCS write -> acknowledgement."""
    now = now or datetime.now(timezone.utc)
    runner = getattr(registry, "run_atomic", None)
    if runner is None:
        raise ProjectionConsumerError("Registry has no transaction boundary")
    task = runner(
        lambda tx: acquire_projection_task(
            tx,
            layout,
            event,
            worker_principal=worker_principal,
            now=now,
        )
    )
    if task is None:
        return _subject_state(registry, layout, event)[3]
    try:
        evidence = render_projection_task(registry, gcs, layout, task)
        verify_projection_completion(registry, gcs, layout, task, evidence)
    except BaseException as exc:
        runner(lambda tx: _mark_projection_failure(tx, layout, task, exc, now))
        raise
    return runner(
        lambda tx: acknowledge_projection_task(tx, layout, task, evidence, now=now)
    )

"""In-memory Fake V2 Registry for tests and later tasks (no real Firestore/GCS).

Mirrors the identity/uniqueness invariants the real Firestore-backed registry
must enforce, at the granularity spec sections 7-10 actually require:

- Blob: registering the same ``blob_id`` twice with a different physical
  identity (``sha256``/``size_bytes``/``crc32c``) is an :class:`IntegrityConflict`
  (spec 6.3/8.1); mutable fields (``availability_state``, current location
  pointer, ``reference_epoch``, ``legal_hold``, ``retention_class``) may
  freely change across calls for the same ``blob_id``.
- AssetVersion: registering the same ``asset_version_id`` with a different
  ``canonical_manifest()`` is an :class:`IdentityConflict` (spec 8.2);
  ``lifecycle_state``/``trust_state``/``record_revision``/``reference_epoch``/
  ``policy_decision_head_ref`` may change freely.
- Label: create-once — the same ``label_id`` can never be rebound to a
  different ``asset_version_id`` (:class:`LabelRebindConflict`, spec 8.3);
  the mutable ``readable_manifest`` projection state may still be updated.
- Occurrence: a ``committed`` Occurrence can never be overwritten with
  different content (:class:`IdempotencyConflict`, spec 8.4), and at most one
  Occurrence may hold a given ``committed_output_key`` at a time
  (:class:`OutputAlreadyCommitted`, spec 8.4/9.1's committed-output binding);
  the mutable ``readable_occurrence`` projection state and
  ``projection_source_revision``/``projection_repair_epoch``/``reference_epoch``
  counters may still be updated after commit (spec 6.3's projection consumer,
  spec 7's reference counting).
- Run: created once per ``run_id``; status only moves along the T9 state
  machine (:class:`~aigear.management.v2.records.run.
  InvalidRunStatusTransitionError` propagates unchanged).
- Step/Attempt: created once per ``(run_id, step_name)``/
  ``(run_id, step_name, attempt_no)``; status only moves along their T9 state
  machines.
- Operation: same ``idempotency_key_hash`` with a different
  ``request_fingerprint`` is an :class:`OperationConflict` (spec 10.1/10.2);
  ``phase`` only moves along the T9 state machine.
- BlobClaim: keyed by ``blob_id``; ``state`` only moves along the T18 state
  machine, which already enforces the ``adopting``/``delete_intent`` mutual
  exclusion described in spec section 7.
- Lineage/Component/Attachment edges: create-once by their own ID, ignoring
  ``created_at`` (the only field not folded into the ID) so idempotent
  replays of an identical edge do not spuriously conflict.
- Read-only list helpers (``iter_asset_versions``/``iter_occurrences_by_run``):
  unfiltered, unsorted iterators for ``query.py`` (T27) to build bounded,
  paginated list queries on top of; they never create or mutate a record.

This is a test/dev double, not a Firestore client. ``run_atomic`` serializes
callbacks and rolls all in-memory collections back on failure, but there is
no cross-process isolation or persistence across process restarts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from threading import RLock
from typing import Callable, Dict, Iterator, Optional, Tuple, TypeVar

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.attestation import AttestationRecord, same_attestation_identity
from aigear.management.v2.records.asset_version import AssetVersionRecord
from aigear.management.v2.records.blob import BlobLocationRevision, BlobRecord
from aigear.management.v2.records.blob_claim import BlobClaim, validate_claim_transition
from aigear.management.v2.records.label import LabelRecord
from aigear.management.v2.records.lineage import AttachmentEdge, ComponentEdge, LineageEdge
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    validate_occurrence_status_transition,
)
from aigear.management.v2.records.operation import OperationRecord, validate_operation_phase_transition
from aigear.management.v2.records.import_operation import ImportOperationRecord
from aigear.management.v2.records.outbox import OutboxEventRecord, OutboxStatus
from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
    validate_attempt_status_transition,
    validate_run_status_transition,
    validate_step_status_transition,
)
from aigear.management.v2.records.run_spec import RunSpec, compute_run_spec_digest

__all__ = [
    "FakeRegistryConflictError",
    "IntegrityConflict",
    "IdentityConflict",
    "LabelRebindConflict",
    "IdempotencyConflict",
    "OutputAlreadyCommitted",
    "OperationConflict",
    "FakeRegistryV2",
]


class FakeRegistryConflictError(ValueError):
    """Common base for every conflict this fake registry can raise."""


class IntegrityConflict(FakeRegistryConflictError):
    """Same ``blob_id`` but a different physical byte identity (spec 6.3/8.1)."""


class IdentityConflict(FakeRegistryConflictError):
    """Same identity but different content (spec 8.2 AssetVersion; also used here for
    lineage/component/attachment edges, spec 11)."""


class LabelRebindConflict(FakeRegistryConflictError):
    """Same ``label_id`` already bound to a different ``asset_version_id`` (spec 8.3)."""


class IdempotencyConflict(FakeRegistryConflictError):
    """Attempted to overwrite an already-committed Occurrence with different content (spec 8.4)."""


class OutputAlreadyCommitted(FakeRegistryConflictError):
    """Another Occurrence already holds this ``committed_output_key`` (spec 8.4/9.1)."""


class OperationConflict(FakeRegistryConflictError):
    """Same ``idempotency_key_hash`` but a different ``request_fingerprint`` (spec 10.1/10.2)."""


_T = TypeVar("_T")


def _blob_physical_identity(record: BlobRecord) -> tuple:
    return (record.sha256, record.size_bytes, record.crc32c)


def _equal_ignoring_created_at(a, b) -> bool:
    """Edge equality that ignores ``created_at`` (the only field spec 11's edge IDs do
    not fold in), so an idempotent replay with a fresh timestamp is not a conflict."""
    return replace(a, created_at=None) == replace(b, created_at=None)


class FakeRegistryV2:
    """In-memory stand-in for the V2 Firestore registry."""

    def __init__(self) -> None:
        self._transaction_lock = RLock()
        self._blobs: Dict[TypedId, BlobRecord] = {}
        self._blob_location_revisions: Dict[Tuple[TypedId, int], BlobLocationRevision] = {}
        self._asset_versions: Dict[TypedId, AssetVersionRecord] = {}
        self._attestations: Dict[TypedId, AttestationRecord] = {}
        self._labels: Dict[TypedId, LabelRecord] = {}
        self._occurrences: Dict[TypedId, OccurrenceRecord] = {}
        self._committed_output_index: Dict[TypedId, TypedId] = {}
        self._runs: Dict[str, RunRecord] = {}
        self._run_specs: Dict[str, RunSpec] = {}
        self._steps: Dict[Tuple[str, str], StepRecord] = {}
        self._attempts: Dict[Tuple[str, str, int], AttemptRecord] = {}
        self._operations: Dict[str, OperationRecord] = {}
        self._import_operations: Dict[str, ImportOperationRecord] = {}
        self._blob_claims: Dict[TypedId, BlobClaim] = {}
        self._lineage_edges: Dict[TypedId, LineageEdge] = {}
        self._component_edges: Dict[TypedId, ComponentEdge] = {}
        self._attachment_edges: Dict[TypedId, AttachmentEdge] = {}
        self._outbox_events: Dict[TypedId, OutboxEventRecord] = {}

    # ── transaction boundary ───────────────────────────────────────────

    def run_atomic(self, work: Callable[["FakeRegistryV2"], _T]) -> _T:
        """Execute ``work`` with serializable in-memory semantics.

        The fake used to mutate a sequence of dictionaries directly, so an
        exception half way through finalize/cancel/lease acquisition exposed
        partial state even though the production contract requires one
        Firestore transaction.  A re-entrant lock plus snapshot rollback makes
        the reference backend exercise the same all-or-nothing boundary.  GCS
        side effects remain external Saga effects and are intentionally not
        rolled back.
        """
        with self._transaction_lock:
            state_fields = tuple(
                name
                for name, value in self.__dict__.items()
                if name != "_transaction_lock" and isinstance(value, dict)
            )
            snapshot = {name: deepcopy(getattr(self, name)) for name in state_fields}
            try:
                return work(self)
            except BaseException:
                for name, value in snapshot.items():
                    setattr(self, name, value)
                raise

    # ── Blob ─────────────────────────────────────────────────────────────

    def put_blob(self, record: BlobRecord) -> BlobRecord:
        existing = self._blobs.get(record.blob_id)
        if existing is not None and _blob_physical_identity(existing) != _blob_physical_identity(
            record
        ):
            raise IntegrityConflict(
                f"blob_id {record.blob_id.typed!r} already registered with a different "
                f"physical identity: {_blob_physical_identity(existing)!r} != "
                f"{_blob_physical_identity(record)!r}"
            )
        self._blobs[record.blob_id] = record
        return record

    def get_blob(self, blob_id: TypedId) -> Optional[BlobRecord]:
        return self._blobs.get(blob_id)

    def put_blob_location_revision(self, record: BlobLocationRevision) -> BlobLocationRevision:
        """Append-only per spec 8.1: the same ``(blob_id, location_revision)``
        may only ever be written once with the same content."""
        key = (record.blob_id, record.location_revision)
        existing = self._blob_location_revisions.get(key)
        if existing is not None and existing != record:
            raise IdentityConflict(
                f"location_revision {record.location_revision} for blob_id "
                f"{record.blob_id.typed!r} already exists with different content"
            )
        self._blob_location_revisions[key] = record
        return record

    def get_blob_location_revision(
        self, blob_id: TypedId, location_revision: int
    ) -> Optional[BlobLocationRevision]:
        return self._blob_location_revisions.get((blob_id, location_revision))

    # ── AssetVersion ─────────────────────────────────────────────────────

    def put_asset_version(self, record: AssetVersionRecord) -> AssetVersionRecord:
        existing = self._asset_versions.get(record.asset_version_id)
        if existing is not None and existing.canonical_manifest() != record.canonical_manifest():
            raise IdentityConflict(
                f"asset_version_id {record.asset_version_id.typed!r} already registered "
                "with a different canonical manifest"
            )
        self._asset_versions[record.asset_version_id] = record
        return record

    def get_asset_version(self, asset_version_id: TypedId) -> Optional[AssetVersionRecord]:
        return self._asset_versions.get(asset_version_id)

    # ── Attestation ─────────────────────────────────────────────────────

    def put_attestation(self, record: AttestationRecord) -> AttestationRecord:
        existing = self._attestations.get(record.attestation_id)
        if existing is not None:
            if not same_attestation_identity(existing, record):
                raise IdentityConflict(
                    f"attestation_id {record.attestation_id.typed!r} already exists with different content"
                )
            return existing
        self._attestations[record.attestation_id] = record
        return record

    def get_attestation(self, attestation_id: TypedId) -> Optional[AttestationRecord]:
        return self._attestations.get(attestation_id)

    # ── Label ────────────────────────────────────────────────────────────

    def put_label(self, record: LabelRecord) -> LabelRecord:
        existing = self._labels.get(record.label_id)
        if existing is not None and existing.asset_version_id != record.asset_version_id:
            raise LabelRebindConflict(
                f"label_id {record.label_id.typed!r} is already bound to AssetVersion "
                f"{existing.asset_version_id.typed!r}; cannot rebind to "
                f"{record.asset_version_id.typed!r}"
            )
        self._labels[record.label_id] = record
        return record

    def get_label(self, label_id: TypedId) -> Optional[LabelRecord]:
        return self._labels.get(label_id)

    # ── Occurrence ───────────────────────────────────────────────────────

    def put_occurrence(self, record: OccurrenceRecord) -> OccurrenceRecord:
        existing = self._occurrences.get(record.occurrence_id)
        if existing is not None and existing != record:
            if existing.status == OccurrenceStatus.COMMITTED:
                # Everything except the projection-delivery-state fields is
                # frozen once committed; adopt those from `record` before
                # comparing so the projection consumer (T26) and reference
                # counting (spec 7) can still update them post-commit.
                existing_with_mutables_adopted = replace(
                    existing,
                    readable_occurrence=record.readable_occurrence,
                    projection_source_revision=record.projection_source_revision,
                    projection_repair_epoch=record.projection_repair_epoch,
                    reference_epoch=record.reference_epoch,
                )
                if existing_with_mutables_adopted != record:
                    raise IdempotencyConflict(
                        f"occurrence_id {record.occurrence_id.typed!r} is already committed "
                        "and cannot be overwritten with different content"
                    )
            elif record.status != existing.status:
                validate_occurrence_status_transition(existing.status, record.status)

        if record.status == OccurrenceStatus.COMMITTED:
            winner_id = self._committed_output_index.get(record.committed_output_key)
            if winner_id is not None and winner_id != record.occurrence_id:
                raise OutputAlreadyCommitted(
                    f"committed_output_key {record.committed_output_key.typed!r} is already "
                    f"held by occurrence_id {winner_id.typed!r}; cannot commit "
                    f"{record.occurrence_id.typed!r} for the same (run, step, output) slot"
                )
            self._committed_output_index[record.committed_output_key] = record.occurrence_id

        self._occurrences[record.occurrence_id] = record
        return record

    def get_occurrence(self, occurrence_id: TypedId) -> Optional[OccurrenceRecord]:
        return self._occurrences.get(occurrence_id)

    def get_committed_occurrence_by_output_key(
        self, committed_output_key: TypedId
    ) -> Optional[OccurrenceRecord]:
        occurrence_id = self._committed_output_index.get(committed_output_key)
        if occurrence_id is None:
            return None
        return self._occurrences.get(occurrence_id)

    # ── Run ──────────────────────────────────────────────────────────────

    def create_run(self, record: RunRecord) -> RunRecord:
        if record.run_id in self._runs:
            raise FakeRegistryConflictError(
                f"run_id {record.run_id!r} already exists; use update_run_status to "
                "change its status"
            )
        self._runs[record.run_id] = record
        return record

    def put_run_spec(self, run_id: str, run_spec: RunSpec) -> RunSpec:
        """Persist the immutable RunSpec instead of relying on process memory."""
        existing = self._run_specs.get(run_id)
        if existing is not None and compute_run_spec_digest(existing) != compute_run_spec_digest(run_spec):
            raise IdentityConflict(f"run_id {run_id!r} already has a different immutable RunSpec")
        self._run_specs[run_id] = run_spec
        return run_spec

    def get_run_spec(self, run_id: str) -> Optional[RunSpec]:
        return self._run_specs.get(run_id)

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        return self._runs.get(run_id)

    def update_run_status(self, run_id: str, target_status: RunStatus, **field_updates) -> RunRecord:
        existing = self._runs.get(run_id)
        if existing is None:
            raise KeyError(f"no Run registered for run_id {run_id!r}")
        if target_status != existing.status:
            validate_run_status_transition(existing.status, target_status)
        updated = replace(existing, status=target_status, **field_updates)
        self._runs[run_id] = updated
        return updated

    # ── Step ─────────────────────────────────────────────────────────────

    def create_step(self, record: StepRecord) -> StepRecord:
        key = (record.run_id, record.step_name)
        if key in self._steps:
            raise FakeRegistryConflictError(
                f"step {key!r} already exists; use update_step_status to change its status"
            )
        self._steps[key] = record
        return record

    def get_step(self, run_id: str, step_name: str) -> Optional[StepRecord]:
        return self._steps.get((run_id, step_name))

    def update_step_status(
        self, run_id: str, step_name: str, target_status: StepStatus, **field_updates
    ) -> StepRecord:
        key = (run_id, step_name)
        existing = self._steps.get(key)
        if existing is None:
            raise KeyError(f"no Step registered for {key!r}")
        if target_status != existing.status:
            validate_step_status_transition(existing.status, target_status)
        updated = replace(existing, status=target_status, **field_updates)
        self._steps[key] = updated
        return updated

    # ── Attempt ──────────────────────────────────────────────────────────

    def create_attempt(self, record: AttemptRecord) -> AttemptRecord:
        key = (record.run_id, record.step_name, record.attempt_no)
        if key in self._attempts:
            raise FakeRegistryConflictError(
                f"attempt {key!r} already exists; use update_attempt_status to change its status"
            )
        self._attempts[key] = record
        return record

    def get_attempt(self, run_id: str, step_name: str, attempt_no: int) -> Optional[AttemptRecord]:
        return self._attempts.get((run_id, step_name, attempt_no))

    def update_attempt_status(
        self, run_id: str, step_name: str, attempt_no: int, target_status: AttemptStatus, **field_updates
    ) -> AttemptRecord:
        key = (run_id, step_name, attempt_no)
        existing = self._attempts.get(key)
        if existing is None:
            raise KeyError(f"no Attempt registered for {key!r}")
        if target_status != existing.status:
            validate_attempt_status_transition(existing.status, target_status)
        updated = replace(existing, status=target_status, **field_updates)
        self._attempts[key] = updated
        return updated

    # ── Operation (also satisfies run_trigger.OperationStore) ──────────────

    def put_operation(self, record: OperationRecord) -> OperationRecord:
        existing = self._operations.get(record.idempotency_key_hash)
        if existing is not None:
            if existing.request_fingerprint != record.request_fingerprint:
                raise OperationConflict(
                    f"idempotency_key_hash {record.idempotency_key_hash!r} is already reserved "
                    "with a different request_fingerprint"
                )
            if existing.phase != record.phase:
                validate_operation_phase_transition(existing.phase, record.phase)
        self._operations[record.idempotency_key_hash] = record
        return record

    def get_operation(self, idempotency_key_hash: str) -> Optional[OperationRecord]:
        return self._operations.get(idempotency_key_hash)

    def put_import_operation(self, record: ImportOperationRecord) -> ImportOperationRecord:
        self._import_operations[record.idempotency_key_hash] = record
        return record

    def get_import_operation(
        self, idempotency_key_hash: str
    ) -> Optional[ImportOperationRecord]:
        return self._import_operations.get(idempotency_key_hash)

    # ── BlobClaim ────────────────────────────────────────────────────────

    def put_blob_claim(self, record: BlobClaim) -> BlobClaim:
        existing = self._blob_claims.get(record.blob_id)
        if existing is not None and existing.state != record.state:
            validate_claim_transition(existing.state, record.state)
        self._blob_claims[record.blob_id] = record
        return record

    def get_blob_claim(self, blob_id: TypedId) -> Optional[BlobClaim]:
        return self._blob_claims.get(blob_id)

    # ── Lineage / Component / Attachment edges ──────────────────────────

    def put_lineage_edge(self, record: LineageEdge) -> LineageEdge:
        existing = self._lineage_edges.get(record.edge_id)
        if existing is not None:
            if not _equal_ignoring_created_at(existing, record):
                raise IdentityConflict(
                    f"lineage edge_id {record.edge_id.typed!r} already exists with different content"
                )
            return existing
        self._lineage_edges[record.edge_id] = record
        return record

    def get_lineage_edge(self, edge_id: TypedId) -> Optional[LineageEdge]:
        return self._lineage_edges.get(edge_id)

    def put_component_edge(self, record: ComponentEdge) -> ComponentEdge:
        existing = self._component_edges.get(record.component_edge_id)
        if existing is not None:
            if not _equal_ignoring_created_at(existing, record):
                raise IdentityConflict(
                    f"component_edge_id {record.component_edge_id.typed!r} already exists with "
                    "different content"
                )
            return existing
        self._component_edges[record.component_edge_id] = record
        return record

    def get_component_edge(self, component_edge_id: TypedId) -> Optional[ComponentEdge]:
        return self._component_edges.get(component_edge_id)

    def put_attachment_edge(self, record: AttachmentEdge) -> AttachmentEdge:
        existing = self._attachment_edges.get(record.attachment_edge_id)
        if existing is not None:
            if not _equal_ignoring_created_at(existing, record):
                raise IdentityConflict(
                    f"attachment_edge_id {record.attachment_edge_id.typed!r} already exists with "
                    "different content"
                )
            return existing
        self._attachment_edges[record.attachment_edge_id] = record
        return record

    def get_attachment_edge(self, attachment_edge_id: TypedId) -> Optional[AttachmentEdge]:
        return self._attachment_edges.get(attachment_edge_id)

    # ── transactional outbox ───────────────────────────────────────────

    def put_outbox_event(self, record: OutboxEventRecord) -> OutboxEventRecord:
        existing = self._outbox_events.get(record.event_id)
        if existing is not None and existing.immutable_identity != record.immutable_identity:
            raise IdentityConflict(
                f"outbox event_id {record.event_id.typed!r} has conflicting immutable content"
            )
        if existing is not None and record.status.value == "pending":
            return existing
        self._outbox_events[record.event_id] = record
        return record

    def get_outbox_event(self, event_id: TypedId) -> Optional[OutboxEventRecord]:
        return self._outbox_events.get(event_id)

    def iter_outbox_events(self) -> Iterator[OutboxEventRecord]:
        return iter(self._outbox_events.values())

    def query_due_outbox_events(self, *, now: str, limit: int):
        """Bounded fake equivalent of the production outbox work query."""
        now_value = datetime.fromisoformat(now)

        def due(record: OutboxEventRecord) -> bool:
            if record.status in (OutboxStatus.PENDING, OutboxStatus.FAILED):
                return record.next_attempt_at is None or datetime.fromisoformat(
                    record.next_attempt_at
                ) <= now_value
            if record.status == OutboxStatus.DELIVERING:
                return record.lease_expires_at is not None and datetime.fromisoformat(
                    record.lease_expires_at
                ) <= now_value
            return False

        records = sorted(
            (record for record in self._outbox_events.values() if due(record)),
            key=lambda record: (
                record.next_attempt_at or record.lease_expires_at or record.created_at or "",
                record.event_id.typed,
            ),
        )
        return tuple(records[:limit])

    # ── read-only list helpers (T27: bounded query) ─────────────────────
    #
    # Unlike every method above, these never create/mutate a record; they
    # only hand ``query.py`` an iterator to filter, sort and paginate over,
    # standing in for a real Firestore composite-index query.

    def iter_asset_versions(self) -> Iterator[AssetVersionRecord]:
        return iter(self._asset_versions.values())

    def iter_occurrences_by_run(self, run_id: str) -> Iterator[OccurrenceRecord]:
        return (record for record in self._occurrences.values() if record.run_id == run_id)

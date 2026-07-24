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
  (:class:`OutputAlreadyCommitted`, spec 8.4/9.1's committed-output binding).
- Run: created once per ``run_id``; status only moves along the T9 state
  machine (:class:`~aigear.management.v2.records.run.
  InvalidRunStatusTransitionError` propagates unchanged).

This is a test/dev double, not a Firestore client: there is no transaction
isolation, no CAS/optimistic-concurrency semantics beyond what is described
above, and no persistence across process restarts.
"""

from __future__ import annotations

from typing import Dict, Optional

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import AssetVersionRecord
from aigear.management.v2.records.blob import BlobRecord
from aigear.management.v2.records.label import LabelRecord
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    validate_occurrence_status_transition,
)
from aigear.management.v2.records.run import RunRecord, RunStatus, validate_run_status_transition

__all__ = [
    "FakeRegistryConflictError",
    "IntegrityConflict",
    "IdentityConflict",
    "LabelRebindConflict",
    "IdempotencyConflict",
    "OutputAlreadyCommitted",
    "FakeRegistryV2",
]


class FakeRegistryConflictError(ValueError):
    """Common base for every conflict this fake registry can raise."""


class IntegrityConflict(FakeRegistryConflictError):
    """Same ``blob_id`` but a different physical byte identity (spec 6.3/8.1)."""


class IdentityConflict(FakeRegistryConflictError):
    """Same ``asset_version_id`` but a different canonical manifest (spec 8.2)."""


class LabelRebindConflict(FakeRegistryConflictError):
    """Same ``label_id`` already bound to a different ``asset_version_id`` (spec 8.3)."""


class IdempotencyConflict(FakeRegistryConflictError):
    """Attempted to overwrite an already-committed Occurrence with different content (spec 8.4)."""


class OutputAlreadyCommitted(FakeRegistryConflictError):
    """Another Occurrence already holds this ``committed_output_key`` (spec 8.4/9.1)."""


def _blob_physical_identity(record: BlobRecord) -> tuple:
    return (record.sha256, record.size_bytes, record.crc32c)


class FakeRegistryV2:
    """In-memory stand-in for the V2 Firestore registry."""

    def __init__(self) -> None:
        self._blobs: Dict[TypedId, BlobRecord] = {}
        self._asset_versions: Dict[TypedId, AssetVersionRecord] = {}
        self._labels: Dict[TypedId, LabelRecord] = {}
        self._occurrences: Dict[TypedId, OccurrenceRecord] = {}
        self._committed_output_index: Dict[TypedId, TypedId] = {}
        self._runs: Dict[str, RunRecord] = {}

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
                raise IdempotencyConflict(
                    f"occurrence_id {record.occurrence_id.typed!r} is already committed "
                    "and cannot be overwritten with different content"
                )
            if record.status != existing.status:
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

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        return self._runs.get(run_id)

    def update_run_status(self, run_id: str, target_status: RunStatus) -> RunRecord:
        existing = self._runs.get(run_id)
        if existing is None:
            raise KeyError(f"no Run registered for run_id {run_id!r}")
        validate_run_status_transition(existing.status, target_status)
        updated = RunRecord(run_id=existing.run_id, status=target_status)
        self._runs[run_id] = updated
        return updated

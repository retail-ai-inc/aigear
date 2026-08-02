"""Firestore-backed Pipeline V2 Registry with retryable atomic callbacks.

The transaction view buffers writes until the callback has finished all of
its reads.  This preserves Firestore's mandatory read-before-write ordering
even when domain code uses a natural read/validate/stage sequence.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar

from aigear.management.v2.attestation import AttestationRecord, same_attestation_identity
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.fake_registry import (
    FakeRegistryConflictError,
    IdempotencyConflict,
    IdentityConflict,
    IntegrityConflict,
    LabelRebindConflict,
    OperationConflict,
    OutputAlreadyCommitted,
)
from aigear.management.v2.firestore_paths import FirestorePathsV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.record_codec import decode_record, encode_record
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
from aigear.management.v2.records.import_operation import (
    ImportOperationRecord,
    ImportProvenanceIndexRecord,
)
from aigear.management.v2.import_recovery import ImportCleanupIntent
from aigear.management.v2.records.outbox import OutboxEventRecord, OutboxStatus
from aigear.management.v2.records.policy import (
    PolicyDecisionEpochBinding,
    PolicyDecisionHead,
    PolicyDecisionOperationRecord,
    PolicyDecisionReservationLock,
    validate_policy_decision_operation_transition,
)
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

__all__ = ["FirestoreRegistryV2"]

_T = TypeVar("_T")


class FirestoreRegistryV2:
    def __init__(
        self,
        project_name: str,
        pipeline_version: str,
        *,
        database_id: str = "(default)",
        client: Any | None = None,
        transaction: Any | None = None,
        transaction_runner: Optional[Callable[[Any, Callable[[Any], _T]], _T]] = None,
        read_time: Optional[datetime] = None,
    ) -> None:
        if client is None:
            try:
                from google.cloud import firestore
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("FirestoreRegistryV2 requires google-cloud-firestore") from exc
            client = firestore.Client(database=database_id)
        self.client = client
        self.database_id = database_id
        self.paths = FirestorePathsV2(project_name, pipeline_version)
        self._transaction = transaction
        self._transaction_runner = transaction_runner
        if transaction is not None and read_time is not None:
            raise ValueError("transaction and read_time are mutually exclusive")
        if read_time is not None and (
            not isinstance(read_time, datetime)
            or read_time.tzinfo is None
            or read_time.utcoffset() is None
        ):
            raise ValueError("read_time must be timezone-aware")
        self.read_time = read_time
        self._last_server_read_time: Optional[datetime] = None
        self._cache: Dict[str, Optional[dict]] = {}
        self._writes: Dict[str, tuple[dict, bool]] = {}

    # ── atomic execution / raw IO ───────────────────────────────────────

    def run_atomic(self, work: Callable[["FirestoreRegistryV2"], _T]) -> _T:
        if self.read_time is not None:
            raise ValueError("fixed read-time Registry views are read-only")
        if self._transaction is not None:
            return work(self)
        transaction = self.client.transaction(max_attempts=5)

        def callback(tx):
            view = FirestoreRegistryV2(
                self.paths.project_name,
                self.paths.pipeline_version,
                database_id=self.database_id,
                client=self.client,
                transaction=tx,
                transaction_runner=self._transaction_runner,
            )
            result = work(view)
            view._flush()
            return result

        if self._transaction_runner is not None:
            return self._transaction_runner(transaction, callback)
        try:
            from google.cloud import firestore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("FirestoreRegistryV2 requires google-cloud-firestore") from exc
        return firestore.transactional(callback)(transaction)

    def at_read_time(self, read_time: datetime) -> "FirestoreRegistryV2":
        """Return a read-only view whose point reads and queries share one timestamp."""

        return FirestoreRegistryV2(
            self.paths.project_name,
            self.paths.pipeline_version,
            database_id=self.database_id,
            client=self.client,
            read_time=read_time,
        )

    def _doc(self, path: str):
        return self.client.document(path)

    def _read_data(self, path: str) -> Optional[dict]:
        if path in self._writes:
            return dict(self._writes[path][0])
        if path in self._cache:
            cached = self._cache[path]
            return None if cached is None else dict(cached)
        ref = self._doc(path)
        if self._transaction is not None:
            snapshot = self._transaction.get(ref)
        elif self.read_time is None:
            snapshot = ref.get()
        else:
            snapshot = ref.get(read_time=self.read_time)
        if isinstance(snapshot, (list, tuple)):
            snapshot = snapshot[0]
        data = snapshot.to_dict() if getattr(snapshot, "exists", False) else None
        snapshot_read_time = getattr(snapshot, "read_time", None)
        if (
            isinstance(snapshot_read_time, datetime)
            and snapshot_read_time.tzinfo is not None
            and snapshot_read_time.utcoffset() is not None
        ):
            self._last_server_read_time = snapshot_read_time
        self._cache[path] = None if data is None else dict(data)
        return None if data is None else dict(data)

    def get_server_read_time(self) -> datetime:
        if self._last_server_read_time is None:
            raise RuntimeError(
                "server read time is unavailable before a Firestore document read"
            )
        return self._last_server_read_time

    def _write_data(self, path: str, data: dict, *, create_only: bool = False) -> None:
        if self.read_time is not None:
            raise ValueError("fixed read-time Registry views are read-only")
        if self._transaction is not None:
            previous = self._writes.get(path)
            self._writes[path] = (
                dict(data),
                create_only or (previous[1] if previous is not None else False),
            )
            return
        ref = self._doc(path)
        if create_only:
            ref.create(data)
        else:
            ref.set(data)
        self._cache[path] = dict(data)

    def _flush(self) -> None:
        for path, (data, create_only) in self._writes.items():
            ref = self._doc(path)
            if create_only:
                self._transaction.create(ref, data)
            else:
                self._transaction.set(ref, data)

    def _get(self, path: str, record_type: type):
        data = self._read_data(path)
        return None if data is None else decode_record(record_type, data)

    def _put(self, path: str, record: Any, *, create_only: bool = False):
        self._write_data(path, encode_record(record), create_only=create_only)
        return record

    # ── control / immutable RunSpec ─────────────────────────────────────

    def get_control_document(self) -> Optional[ControlDocument]:
        return self._get(self.paths.control_document, ControlDocument)

    def put_run_spec(self, run_id: str, run_spec: RunSpec) -> RunSpec:
        path = self.paths.run_spec_document(run_id)
        existing = self._get(path, RunSpec)
        if existing is not None:
            if compute_run_spec_digest(existing) != compute_run_spec_digest(run_spec):
                raise IdentityConflict(f"run_id {run_id!r} already has a different RunSpec")
            return existing
        return self._put(path, run_spec, create_only=True)

    def get_run_spec(self, run_id: str) -> Optional[RunSpec]:
        return self._get(self.paths.run_spec_document(run_id), RunSpec)

    # ── blobs / attestations / asset / label ────────────────────────────

    def get_blob(self, blob_id: TypedId) -> Optional[BlobRecord]:
        return self._get(self.paths.blob_document(blob_id), BlobRecord)

    def put_blob(self, record: BlobRecord) -> BlobRecord:
        existing = self.get_blob(record.blob_id)
        if existing is not None and (existing.sha256, existing.size_bytes, existing.crc32c) != (
            record.sha256,
            record.size_bytes,
            record.crc32c,
        ):
            raise IntegrityConflict(f"blob_id {record.blob_id.typed!r} physical identity conflict")
        return self._put(self.paths.blob_document(record.blob_id), record)

    def get_blob_location_revision(self, blob_id: TypedId, revision: int) -> Optional[BlobLocationRevision]:
        return self._get(self.paths.blob_location_revision_document(blob_id, revision), BlobLocationRevision)

    def put_blob_location_revision(self, record: BlobLocationRevision) -> BlobLocationRevision:
        path = self.paths.blob_location_revision_document(record.blob_id, record.location_revision)
        existing = self._get(path, BlobLocationRevision)
        if existing is not None:
            if existing != record:
                raise IdentityConflict("Blob location revision is create-only")
            return existing
        return self._put(path, record, create_only=True)

    def put_attestation(self, record: AttestationRecord) -> AttestationRecord:
        path = self.paths.attestation_document(record.attestation_id)
        existing = self._get(path, AttestationRecord)
        if existing is not None:
            if not same_attestation_identity(existing, record):
                raise IdentityConflict("attestation_id content conflict")
            return existing
        return self._put(path, record, create_only=True)

    def get_attestation(self, attestation_id: TypedId) -> Optional[AttestationRecord]:
        return self._get(self.paths.attestation_document(attestation_id), AttestationRecord)

    def get_policy_decision_head(
        self, subject_asset_version_id: TypedId
    ) -> Optional[PolicyDecisionHead]:
        return self._get(
            self.paths.policy_decision_head_document(subject_asset_version_id),
            PolicyDecisionHead,
        )

    def get_policy_decision_epoch(
        self, subject_epoch_key: TypedId
    ) -> Optional[PolicyDecisionEpochBinding]:
        return self._get(
            self.paths.policy_decision_epoch_document(subject_epoch_key),
            PolicyDecisionEpochBinding,
        )

    def put_policy_decision_epoch(
        self, record: PolicyDecisionEpochBinding
    ) -> PolicyDecisionEpochBinding:
        path = self.paths.policy_decision_epoch_document(
            record.subject_epoch_key
        )
        existing = self._get(path, PolicyDecisionEpochBinding)
        if existing is not None:
            existing.assert_same_identity(record)
            return existing
        return self._put(path, record, create_only=True)

    def put_policy_decision_head(
        self, record: PolicyDecisionHead
    ) -> PolicyDecisionHead:
        path = self.paths.policy_decision_head_document(
            record.subject_asset_version_id
        )
        existing = self._get(path, PolicyDecisionHead)
        if existing is not None and existing != record:
            if record.revision != existing.revision + 1:
                raise IdentityConflict("policy decision head revision conflict")
            if record.current_epoch < existing.current_epoch:
                raise IdentityConflict("policy decision head epoch moved backwards")
        return self._put(path, record, create_only=existing is None)

    def get_policy_decision_operation(
        self, idempotency_key_hash: str
    ) -> Optional[PolicyDecisionOperationRecord]:
        return self._get(
            self.paths.policy_decision_operation_document(idempotency_key_hash),
            PolicyDecisionOperationRecord,
        )

    def put_policy_decision_operation(
        self, record: PolicyDecisionOperationRecord
    ) -> PolicyDecisionOperationRecord:
        path = self.paths.policy_decision_operation_document(
            record.idempotency_key_hash
        )
        existing = self._get(path, PolicyDecisionOperationRecord)
        if existing is not None:
            if existing.request_fingerprint != record.request_fingerprint:
                raise IdentityConflict(
                    "policy decision idempotency key request conflict"
                )
            if existing.phase != record.phase:
                validate_policy_decision_operation_transition(
                    existing.phase, record.phase
                )
            expected_revision = existing.revision + (0 if existing == record else 1)
            if record.revision != expected_revision:
                raise IdentityConflict("policy decision operation revision conflict")
        return self._put(path, record, create_only=existing is None)

    def get_policy_decision_reservation(
        self, subject_asset_version_id: TypedId
    ) -> Optional[PolicyDecisionReservationLock]:
        return self._get(
            self.paths.policy_decision_reservation_document(
                subject_asset_version_id
            ),
            PolicyDecisionReservationLock,
        )

    def put_policy_decision_reservation(
        self, record: PolicyDecisionReservationLock
    ) -> PolicyDecisionReservationLock:
        path = self.paths.policy_decision_reservation_document(
            record.subject_asset_version_id
        )
        existing = self._get(path, PolicyDecisionReservationLock)
        if existing is not None and existing != record:
            if record.revision != existing.revision + 1:
                raise IdentityConflict("policy reservation revision conflict")
            if record.fencing_token <= existing.fencing_token:
                raise IdentityConflict("policy reservation fencing token is stale")
        return self._put(path, record, create_only=existing is None)

    def get_asset_version(self, asset_version_id: TypedId) -> Optional[AssetVersionRecord]:
        return self._get(self.paths.asset_version_document(asset_version_id), AssetVersionRecord)

    def put_asset_version(self, record: AssetVersionRecord) -> AssetVersionRecord:
        existing = self.get_asset_version(record.asset_version_id)
        if existing is not None and existing.canonical_manifest() != record.canonical_manifest():
            raise IdentityConflict("asset_version_id canonical manifest conflict")
        return self._put(self.paths.asset_version_document(record.asset_version_id), record)

    def get_label(self, label_id: TypedId) -> Optional[LabelRecord]:
        return self._get(self.paths.label_document(label_id), LabelRecord)

    def put_label(self, record: LabelRecord) -> LabelRecord:
        existing = self.get_label(record.label_id)
        if existing is not None and existing.asset_version_id != record.asset_version_id:
            raise LabelRebindConflict("immutable label cannot be rebound")
        return self._put(self.paths.label_document(record.label_id), record)

    # ── run / step / attempt ────────────────────────────────────────────

    def create_run(self, record: RunRecord) -> RunRecord:
        path = self.paths.run_document(record.run_id)
        if self._read_data(path) is not None:
            raise FakeRegistryConflictError(f"run_id {record.run_id!r} already exists")
        return self._put(path, record, create_only=True)

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        return self._get(self.paths.run_document(run_id), RunRecord)

    def update_run_status(self, run_id: str, target_status: RunStatus, **updates) -> RunRecord:
        existing = self.get_run(run_id)
        if existing is None:
            raise KeyError(f"no Run {run_id!r}")
        if target_status != existing.status:
            validate_run_status_transition(existing.status, target_status)
        return self._put(self.paths.run_document(run_id), replace(existing, status=target_status, **updates))

    def create_step(self, record: StepRecord) -> StepRecord:
        path = self.paths.run_step_instance_document(record.run_id, record.step_name)
        if self._read_data(path) is not None:
            raise FakeRegistryConflictError("Step already exists")
        return self._put(path, record, create_only=True)

    def get_step(self, run_id: str, step_name: str) -> Optional[StepRecord]:
        return self._get(self.paths.run_step_instance_document(run_id, step_name), StepRecord)

    def update_step_status(self, run_id: str, step_name: str, target_status: StepStatus, **updates) -> StepRecord:
        existing = self.get_step(run_id, step_name)
        if existing is None:
            raise KeyError("Step does not exist")
        if target_status != existing.status:
            validate_step_status_transition(existing.status, target_status)
        return self._put(
            self.paths.run_step_instance_document(run_id, step_name),
            replace(existing, status=target_status, **updates),
        )

    def create_attempt(self, record: AttemptRecord) -> AttemptRecord:
        path = self.paths.run_attempt_document(record.run_id, record.step_name, record.attempt_no)
        if self._read_data(path) is not None:
            raise FakeRegistryConflictError("Attempt already exists")
        return self._put(path, record, create_only=True)

    def get_attempt(self, run_id: str, step_name: str, attempt_no: int) -> Optional[AttemptRecord]:
        return self._get(self.paths.run_attempt_document(run_id, step_name, attempt_no), AttemptRecord)

    def update_attempt_status(
        self, run_id: str, step_name: str, attempt_no: int, target_status: AttemptStatus, **updates
    ) -> AttemptRecord:
        existing = self.get_attempt(run_id, step_name, attempt_no)
        if existing is None:
            raise KeyError("Attempt does not exist")
        if target_status != existing.status:
            validate_attempt_status_transition(existing.status, target_status)
        return self._put(
            self.paths.run_attempt_document(run_id, step_name, attempt_no),
            replace(existing, status=target_status, **updates),
        )

    # ── occurrences / committed outputs ─────────────────────────────────

    def get_occurrence(self, occurrence_id: TypedId) -> Optional[OccurrenceRecord]:
        return self._get(self.paths.occurrence_document(occurrence_id), OccurrenceRecord)

    def query_occurrences_by_asset(
        self, *, asset_version_id: TypedId, cursor: Optional[str], limit: int
    ):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        query = (
            self.client.collection(self.paths._under_root("occurrences"))
            .where("asset_version_id", "==", asset_version_id.typed)
            .where("status", "==", OccurrenceStatus.COMMITTED.value)
            .order_by("occurrence_id")
        )
        if cursor is not None:
            query = query.start_after({"occurrence_id": cursor})
        query = query.limit(limit)
        snapshots = (
            query.stream()
            if self.read_time is None
            else query.stream(read_time=self.read_time)
        )
        return tuple(
            decode_record(OccurrenceRecord, snapshot.to_dict())
            for snapshot in snapshots
        )

    def get_committed_occurrence_by_output_key(self, key: TypedId) -> Optional[OccurrenceRecord]:
        # The run_id is not derivable from the digest, so exact callers should
        # use a query.  A collection-group equality query remains bounded to 1.
        collection = self.client.collection(self.paths._under_root("occurrences"))
        query = collection.where("committed_output_key", "==", key.typed).limit(1)
        snapshots = self._transaction.get(query) if self._transaction is not None else query.stream()
        for snapshot in snapshots:
            return decode_record(OccurrenceRecord, snapshot.to_dict())
        return None

    def put_occurrence(self, record: OccurrenceRecord) -> OccurrenceRecord:
        path = self.paths.occurrence_document(record.occurrence_id)
        existing = self._get(path, OccurrenceRecord)
        if existing is not None and existing != record:
            if existing.status == OccurrenceStatus.COMMITTED:
                mutable = replace(
                    existing,
                    readable_occurrence=record.readable_occurrence,
                    projection_source_revision=record.projection_source_revision,
                    projection_repair_epoch=record.projection_repair_epoch,
                    reference_epoch=record.reference_epoch,
                )
                if mutable != record:
                    raise IdempotencyConflict("committed Occurrence is immutable")
            elif record.status != existing.status:
                validate_occurrence_status_transition(existing.status, record.status)
        if record.status == OccurrenceStatus.COMMITTED:
            binding_path = self.paths.run_committed_output_document(
                record.run_id, record.committed_output_key.bare
            )
            binding = self._read_data(binding_path)
            expected = {"occurrence_id": record.occurrence_id.typed}
            if binding is not None and binding.get("occurrence_id") != record.occurrence_id.typed:
                raise OutputAlreadyCommitted("committed output slot already has a winner")
            if binding is None:
                self._write_data(binding_path, expected, create_only=True)
        return self._put(path, record, create_only=existing is None)

    # ── operation / claim ───────────────────────────────────────────────

    def get_operation(self, key: str) -> Optional[OperationRecord]:
        return self._get(self.paths.operation_document(key), OperationRecord)

    def put_operation(self, record: OperationRecord) -> OperationRecord:
        path = self.paths.operation_document(record.idempotency_key_hash)
        existing = self._get(path, OperationRecord)
        if existing is not None:
            if existing.request_fingerprint != record.request_fingerprint:
                raise OperationConflict("idempotency key request fingerprint conflict")
            if existing.phase != record.phase:
                validate_operation_phase_transition(existing.phase, record.phase)
        return self._put(path, record, create_only=existing is None)

    def get_import_operation(self, key: str) -> Optional[ImportOperationRecord]:
        return self._get(self.paths.import_operation_document(key), ImportOperationRecord)

    def put_import_operation(
        self, record: ImportOperationRecord
    ) -> ImportOperationRecord:
        path = self.paths.import_operation_document(record.idempotency_key_hash)
        existing = self._get(path, ImportOperationRecord)
        if existing is not None and existing.request_fingerprint != record.request_fingerprint:
            raise OperationConflict("import idempotency key request fingerprint conflict")
        return self._put(path, record, create_only=existing is None)

    def put_import_provenance(
        self, record: ImportProvenanceIndexRecord
    ) -> ImportProvenanceIndexRecord:
        path = self.paths.import_provenance_document(
            record.asset_version_id, record.source_provenance_attestation_ref
        )
        existing = self._get(path, ImportProvenanceIndexRecord)
        if existing is not None:
            if existing != record:
                raise IdentityConflict("import provenance index conflict")
            return existing
        return self._put(path, record, create_only=True)

    def get_import_provenance(
        self, asset_version_id: TypedId, attestation_id: TypedId
    ) -> Optional[ImportProvenanceIndexRecord]:
        return self._get(
            self.paths.import_provenance_document(asset_version_id, attestation_id),
            ImportProvenanceIndexRecord,
        )

    def query_import_provenance_by_asset(
        self, *, asset_version_id: TypedId, cursor: Optional[str], limit: int
    ):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        query = self.client.collection(
            self.paths._under_root(
                "asset_import_provenance",
                asset_version_id.bare,
                "entries",
            )
        ).order_by("source_provenance_attestation_ref")
        if cursor is not None:
            query = query.start_after(
                {"source_provenance_attestation_ref": cursor}
            )
        query = query.limit(limit)
        snapshots = (
            query.stream()
            if self.read_time is None
            else query.stream(read_time=self.read_time)
        )
        return tuple(
            decode_record(ImportProvenanceIndexRecord, snapshot.to_dict())
            for snapshot in snapshots
        )

    def put_import_cleanup_intent(
        self, record: ImportCleanupIntent
    ) -> ImportCleanupIntent:
        path = self.paths.import_cleanup_intent_document(record.intent_id)
        existing = self._get(path, ImportCleanupIntent)
        if existing is not None:
            if existing != record:
                raise IdentityConflict("import cleanup intent conflict")
            return existing
        return self._put(path, record, create_only=True)

    def get_import_cleanup_intent(
        self, intent_id: TypedId
    ) -> Optional[ImportCleanupIntent]:
        return self._get(
            self.paths.import_cleanup_intent_document(intent_id),
            ImportCleanupIntent,
        )

    def get_blob_claim(self, blob_id: TypedId) -> Optional[BlobClaim]:
        return self._get(self.paths.blob_claim_document(blob_id), BlobClaim)

    def put_blob_claim(self, record: BlobClaim) -> BlobClaim:
        path = self.paths.blob_claim_document(record.blob_id)
        existing = self._get(path, BlobClaim)
        if existing is not None and existing.state != record.state:
            validate_claim_transition(existing.state, record.state)
        return self._put(path, record, create_only=existing is None)

    # ── edges ────────────────────────────────────────────────────────────

    @staticmethod
    def _equal_edge(a, b) -> bool:
        return replace(a, created_at=None) == replace(b, created_at=None)

    def _put_edge(self, path: str, record: Any, record_type: type):
        existing = self._get(path, record_type)
        if existing is not None:
            if not self._equal_edge(existing, record):
                raise IdentityConflict("edge identity conflict")
            return existing
        return self._put(path, record, create_only=True)

    def put_lineage_edge(self, record: LineageEdge) -> LineageEdge:
        return self._put_edge(self.paths.lineage_edge_document(record.edge_id.bare), record, LineageEdge)

    def get_lineage_edge(self, edge_id: TypedId) -> Optional[LineageEdge]:
        return self._get(self.paths.lineage_edge_document(edge_id.bare), LineageEdge)

    def put_component_edge(self, record: ComponentEdge) -> ComponentEdge:
        return self._put_edge(
            self.paths.component_edge_document(record.component_edge_id.bare), record, ComponentEdge
        )

    def get_component_edge(self, edge_id: TypedId) -> Optional[ComponentEdge]:
        return self._get(self.paths.component_edge_document(edge_id.bare), ComponentEdge)

    def put_attachment_edge(self, record: AttachmentEdge) -> AttachmentEdge:
        return self._put_edge(
            self.paths.attachment_edge_document(record.attachment_edge_id.bare), record, AttachmentEdge
        )

    def get_attachment_edge(self, edge_id: TypedId) -> Optional[AttachmentEdge]:
        return self._get(self.paths.attachment_edge_document(edge_id.bare), AttachmentEdge)

    # ── transactional outbox ───────────────────────────────────────────

    def put_outbox_event(self, record: OutboxEventRecord) -> OutboxEventRecord:
        path = self.paths.outbox_document(record.event_id.bare)
        existing = self._get(path, OutboxEventRecord)
        if existing is not None and existing.immutable_identity != record.immutable_identity:
            raise IdentityConflict("outbox event identity conflict")
        if existing is not None and record.status.value == "pending":
            return existing
        return self._put(path, record, create_only=existing is None)

    def get_outbox_event(self, event_id: TypedId) -> Optional[OutboxEventRecord]:
        return self._get(self.paths.outbox_document(event_id.bare), OutboxEventRecord)

    def query_due_outbox_events(self, *, now: str, limit: int):
        """Return a bounded batch of retryable or lease-expired projection work."""
        collection = self.client.collection(self.paths._under_root("outbox"))
        retryable = (
            collection.where(
                "status",
                "in",
                [OutboxStatus.PENDING.value, OutboxStatus.FAILED.value],
            )
            .where("next_attempt_at", "<=", now)
            .order_by("next_attempt_at")
            .order_by("created_at")
            .order_by("event_id")
            .limit(limit)
        )
        expired = (
            collection.where("status", "==", OutboxStatus.DELIVERING.value)
            .where("lease_expires_at", "<=", now)
            .order_by("lease_expires_at")
            .order_by("created_at")
            .order_by("event_id")
            .limit(limit)
        )
        records = [
            decode_record(OutboxEventRecord, snapshot.to_dict())
            for query in (retryable, expired)
            for snapshot in query.stream()
        ]
        records.sort(
            key=lambda record: (
                record.next_attempt_at or record.lease_expires_at or record.created_at or "",
                record.event_id.typed,
            )
        )
        return tuple(records[:limit])

    # ── bounded query helpers ────────────────────────────────────────────

    def query_asset_versions(
        self,
        *,
        asset_type: str,
        name: str,
        lifecycle_state=None,
        trust_state=None,
        page_cutoff: str,
        cursor,
        limit: int,
    ):
        query = self.client.collection(self.paths._under_root("asset_versions"))
        query = query.where("asset_type", "==", asset_type).where("name", "==", name)
        if lifecycle_state is not None:
            query = query.where("lifecycle_state", "==", lifecycle_state.value)
        if trust_state is not None:
            query = query.where("trust_state", "==", trust_state.value)
        query = (
            query.where("created_at", "<=", page_cutoff)
            .order_by("created_at")
            .order_by("asset_version_id")
        )
        if cursor is not None:
            query = query.start_after(
                {"created_at": cursor[0], "asset_version_id": cursor[1]}
            )
        for snapshot in query.limit(limit).stream():
            yield decode_record(AssetVersionRecord, snapshot.to_dict())

    def query_run_outputs(
        self,
        *,
        run_id: str,
        step_name: Optional[str],
        page_cutoff: str,
        cursor,
        limit: int,
    ):
        query = self.client.collection(self.paths._under_root("occurrences"))
        query = query.where("run_id", "==", run_id).where(
            "status", "==", OccurrenceStatus.COMMITTED.value
        )
        if step_name is not None:
            query = query.where("step_name", "==", step_name)
        query = (
            query.where("committed_at", "<=", page_cutoff)
            .order_by("committed_at")
            .order_by("occurrence_id")
        )
        if cursor is not None:
            query = query.start_after(
                {"committed_at": cursor[0], "occurrence_id": cursor[1]}
            )
        for snapshot in query.limit(limit).stream():
            yield decode_record(OccurrenceRecord, snapshot.to_dict())

    def iter_asset_versions(self) -> Iterator[AssetVersionRecord]:
        for snapshot in self.client.collection(self.paths._under_root("asset_versions")).stream():
            yield decode_record(AssetVersionRecord, snapshot.to_dict())

    def iter_occurrences_by_run(self, run_id: str) -> Iterator[OccurrenceRecord]:
        query = self.client.collection(self.paths._under_root("occurrences")).where("run_id", "==", run_id)
        for snapshot in query.stream():
            yield decode_record(OccurrenceRecord, snapshot.to_dict())

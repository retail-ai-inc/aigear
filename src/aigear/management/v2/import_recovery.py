"""Deterministic recovery and generation-bound cleanup intents for imports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple

from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_adoption import AdoptedImportBlobs
from aigear.management.v2.import_executor import (
    ImportExecutorCompletion,
    QuarantineObjectDescriptor,
)
from aigear.management.v2.records.import_operation import (
    ImportOperationRecord,
    ImportPhase,
)
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.naming import validate_segment

__all__ = [
    "ImportRecoveryError",
    "StaleImportFenceError",
    "CleanupObjectScope",
    "CleanupObjectRef",
    "ImportCleanupIntent",
    "RecoveredImportResult",
    "ImportRecoveryAction",
    "ImportRecoveryOutcome",
    "build_import_cleanup_intent",
    "fail_import_with_cleanup",
    "reconcile_import_once",
]


class ImportRecoveryError(ValueError):
    pass


class StaleImportFenceError(ImportRecoveryError):
    pass


class CleanupObjectScope(str, Enum):
    QUARANTINE = "quarantine"
    CANONICAL_CANDIDATE = "canonical_candidate"


class ImportRecoveryAction(str, Enum):
    RESUME = "resume"
    REBUILT_SUCCEEDED = "rebuilt_succeeded"
    TERMINAL = "terminal"
    RECONCILING = "reconciling"


def _aware(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImportRecoveryError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportRecoveryError(f"{field_name} must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class CleanupObjectRef:
    scope: CleanupObjectScope
    bucket: str
    object_name: str
    generation: str
    sha256: str
    size_bytes: int
    blob_id: Optional[TypedId] = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, CleanupObjectScope):
            raise ImportRecoveryError("cleanup scope must be CleanupObjectScope")
        for field_name in ("bucket", "object_name", "generation", "sha256"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ImportRecoveryError(f"{field_name} must be non-empty")
        if (
            len(self.sha256) != 64
            or self.sha256 != self.sha256.lower()
            or any(value not in "0123456789abcdef" for value in self.sha256)
        ):
            raise ImportRecoveryError("sha256 must be lowercase SHA-256 hex")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ImportRecoveryError("size_bytes must be non-negative")
        if self.scope is CleanupObjectScope.CANONICAL_CANDIDATE:
            if not isinstance(self.blob_id, TypedId) or self.blob_id.bare != self.sha256:
                raise ImportRecoveryError(
                    "canonical cleanup candidates require matching blob_id"
                )
        elif self.blob_id is not None:
            raise ImportRecoveryError("quarantine cleanup refs must not set blob_id")

    def canonical_dict(self) -> dict:
        return {
            "scope": self.scope.value,
            "bucket": self.bucket,
            "object_name": self.object_name,
            "generation": self.generation,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "blob_id": None if self.blob_id is None else self.blob_id.typed,
        }


@dataclass(frozen=True)
class ImportCleanupIntent:
    schema_version: str
    intent_id: TypedId
    environment_fingerprint: TypedId
    operation_id: str
    fencing_token: int
    objects: Tuple[CleanupObjectRef, ...]
    created_at: str
    eligible_after: str

    def __post_init__(self) -> None:
        parse_schema_version(self.schema_version)
        if not isinstance(self.environment_fingerprint, TypedId):
            raise ImportRecoveryError("environment_fingerprint must be a TypedId")
        object.__setattr__(
            self,
            "operation_id",
            validate_segment(self.operation_id, field_name="operation_id"),
        )
        if (
            isinstance(self.fencing_token, bool)
            or not isinstance(self.fencing_token, int)
            or self.fencing_token < 0
        ):
            raise ImportRecoveryError("fencing_token must be non-negative")
        created = _aware("created_at", self.created_at)
        eligible = _aware("eligible_after", self.eligible_after)
        if eligible < created:
            raise ImportRecoveryError("eligible_after cannot precede created_at")
        if not self.objects:
            raise ImportRecoveryError("cleanup intent requires exact object refs")
        identities = [
            (value.bucket, value.object_name, value.generation) for value in self.objects
        ]
        if identities != sorted(identities) or len(set(identities)) != len(identities):
            raise ImportRecoveryError(
                "cleanup objects must be unique and sorted by exact identity"
            )
        if self.intent_id != _cleanup_intent_id(
            self.environment_fingerprint,
            self.operation_id,
            self.fencing_token,
            self.objects,
        ):
            raise ImportRecoveryError("cleanup intent_id does not match object refs")


@dataclass(frozen=True)
class RecoveredImportResult:
    operation: ImportOperationRecord
    asset_version: object
    label: object
    source_provenance: object
    provenance_index: object


@dataclass(frozen=True)
class ImportRecoveryOutcome:
    action: ImportRecoveryAction
    operation: ImportOperationRecord
    result: Optional[RecoveredImportResult] = None


def _cleanup_intent_id(
    environment_fingerprint: TypedId,
    operation_id: str,
    fencing_token: int,
    objects: Tuple[CleanupObjectRef, ...],
) -> TypedId:
    value = {
        "domain": "aigear.import-cleanup-intent.v2",
        "environment_fingerprint": environment_fingerprint.typed,
        "operation_id": operation_id,
        "fencing_token": fencing_token,
        "objects": [item.canonical_dict() for item in objects],
    }
    return TypedId.from_bare(hashlib.sha256(canonicalize_json(value)).hexdigest())


def _quarantine_ref(
    bucket: str, descriptor: QuarantineObjectDescriptor
) -> CleanupObjectRef:
    return CleanupObjectRef(
        scope=CleanupObjectScope.QUARANTINE,
        bucket=bucket,
        object_name=descriptor.object_name,
        generation=descriptor.generation,
        sha256=descriptor.sha256,
        size_bytes=descriptor.size_bytes,
    )


def build_import_cleanup_intent(
    operation: ImportOperationRecord,
    *,
    quarantine_bucket: str,
    completion: Optional[ImportExecutorCompletion] = None,
    partial_quarantine: Tuple[QuarantineObjectDescriptor, ...] = (),
    adopted: Optional[AdoptedImportBlobs] = None,
    created_at: str,
    eligible_after: str,
) -> ImportCleanupIntent:
    """Build cleanup work only from fenced, immutable external evidence."""

    _aware("created_at", created_at)
    _aware("eligible_after", eligible_after)
    descriptors = list(partial_quarantine)
    if completion is not None:
        if (
            operation.completion != completion.to_record()
            or completion.operation_id != operation.operation_id
            or completion.fencing_token != operation.fencing_token
        ):
            raise ImportRecoveryError("completion is not bound to cleanup operation")
        descriptors.extend((completion.source_manifest, *completion.payloads))
    refs = [_quarantine_ref(quarantine_bucket, value) for value in descriptors]
    if any(
        not value.object_name.startswith(operation.target_quarantine_prefix)
        for value in refs
    ):
        raise ImportRecoveryError("cleanup ref escapes operation quarantine prefix")
    if adopted is not None:
        if (
            adopted.operation_id != operation.operation_id
            or adopted.fencing_token != operation.fencing_token
        ):
            raise ImportRecoveryError("adoption evidence is stale for cleanup")
        for material in adopted.materials:
            if material.reused_existing:
                continue
            blob = material.blob
            refs.append(
                CleanupObjectRef(
                    scope=CleanupObjectScope.CANONICAL_CANDIDATE,
                    bucket=blob.bucket,
                    object_name=blob.object_name,
                    generation=blob.generation,
                    sha256=blob.sha256,
                    size_bytes=blob.size_bytes,
                    blob_id=blob.blob_id,
                )
            )
    unique = {}
    for value in refs:
        key = (value.bucket, value.object_name, value.generation)
        existing = unique.get(key)
        if existing is not None and existing != value:
            raise ImportRecoveryError("cleanup evidence conflicts for exact object")
        unique[key] = value
    objects = tuple(unique[key] for key in sorted(unique))
    if not objects:
        raise ImportRecoveryError("no immutable cleanup evidence is available")
    intent_id = _cleanup_intent_id(
        operation.control_snapshot.environment_fingerprint,
        operation.operation_id,
        operation.fencing_token,
        objects,
    )
    return ImportCleanupIntent(
        schema_version=operation.schema_version,
        intent_id=intent_id,
        environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        operation_id=operation.operation_id,
        fencing_token=operation.fencing_token,
        objects=objects,
        created_at=created_at,
        eligible_after=eligible_after,
    )


def fail_import_with_cleanup(
    registry,
    operation: ImportOperationRecord,
    cleanup_intent: ImportCleanupIntent,
    *,
    error_class: str,
    error_summary: str,
    failed_at: str,
) -> ImportOperationRecord:
    _aware("failed_at", failed_at)
    if (
        cleanup_intent.operation_id != operation.operation_id
        or cleanup_intent.fencing_token != operation.fencing_token
        or cleanup_intent.environment_fingerprint
        != operation.control_snapshot.environment_fingerprint
    ):
        raise ImportRecoveryError("cleanup intent is not bound to operation")
    if not error_class or not error_summary:
        raise ImportRecoveryError("error_class and error_summary must be non-empty")
    if operation.phase in (
        ImportPhase.SUCCEEDED,
        ImportPhase.FAILED,
        ImportPhase.CANCELLED,
    ):
        if operation.phase is ImportPhase.FAILED:
            if registry.get_import_cleanup_intent(cleanup_intent.intent_id) != cleanup_intent:
                raise ImportRecoveryError("failed import cleanup intent is missing")
            return operation
        raise ImportRecoveryError("cannot fail an incompatible terminal import")

    def fail(tx):
        current = tx.get_import_operation(operation.idempotency_key_hash)
        if current != operation:
            raise ImportRecoveryError("operation changed before failure commit")
        tx.put_import_cleanup_intent(cleanup_intent)
        compensating = replace(
            operation,
            phase=ImportPhase.COMPENSATING,
            revision=operation.revision + 1,
            lease_expires_at=None,
            updated_at=failed_at,
        )
        tx.put_import_operation(compensating)
        failed = replace(
            compensating,
            phase=ImportPhase.FAILED,
            revision=compensating.revision + 1,
            error_class=error_class,
            error_summary=error_summary,
            updated_at=failed_at,
            finished_at=failed_at,
        )
        return tx.put_import_operation(failed)

    return registry.run_atomic(fail)


def _rebuild_result(registry, operation: ImportOperationRecord) -> RecoveredImportResult:
    asset = registry.get_asset_version(operation.result_asset_version_id)
    label = registry.get_label(operation.result_label_id)
    provenance = registry.get_attestation(
        operation.result_source_provenance_attestation_ref
    )
    index = registry.get_import_provenance(
        operation.result_asset_version_id,
        operation.result_source_provenance_attestation_ref,
    )
    if (
        asset is None
        or label is None
        or provenance is None
        or index is None
        or label.asset_version_id != operation.result_asset_version_id
        or index.operation_id != operation.operation_id
        or index.identity_reservation_entry_id
        != operation.identity_reservation_entry_id
        or provenance.attestation_kind != "source_provenance"
        or provenance.unsigned_envelope["subject"].get("asset_version_id")
        != operation.result_asset_version_id.typed
        or provenance.unsigned_envelope["subject"].get("operation_id")
        != operation.operation_id
    ):
        raise ImportRecoveryError("succeeded import has incomplete immutable result refs")
    return RecoveredImportResult(operation, asset, label, provenance, index)


def reconcile_import_once(
    registry,
    *,
    idempotency_key_hash: str,
    expected_request_fingerprint: TypedId,
    expected_fencing_token: int,
    now: str,
) -> ImportRecoveryOutcome:
    """Run one bounded recovery decision; Phase D schedules repeated calls."""

    _aware("now", now)
    operation = registry.get_import_operation(idempotency_key_hash)
    if operation is None or operation.request_fingerprint != expected_request_fingerprint:
        raise ImportRecoveryError("import operation identity does not match recovery request")
    if operation.fencing_token != expected_fencing_token:
        raise StaleImportFenceError("import recovery fencing token is stale")
    if operation.phase is ImportPhase.SUCCEEDED:
        return ImportRecoveryOutcome(
            ImportRecoveryAction.REBUILT_SUCCEEDED,
            operation,
            _rebuild_result(registry, operation),
        )
    if operation.phase in (ImportPhase.FAILED, ImportPhase.CANCELLED):
        return ImportRecoveryOutcome(ImportRecoveryAction.TERMINAL, operation)
    if operation.phase in (ImportPhase.RESERVED, ImportPhase.TICKETED):
        return ImportRecoveryOutcome(ImportRecoveryAction.RESUME, operation)
    if operation.phase is ImportPhase.RECONCILING:
        return ImportRecoveryOutcome(ImportRecoveryAction.RECONCILING, operation)

    def mark(tx):
        current = tx.get_import_operation(idempotency_key_hash)
        if current != operation:
            raise ImportRecoveryError("import changed during reconciliation")
        reconciling = replace(
            operation,
            phase=ImportPhase.RECONCILING,
            revision=operation.revision + 1,
            lease_expires_at=None,
            updated_at=now,
        )
        return tx.put_import_operation(reconciling)

    reconciling = registry.run_atomic(mark)
    return ImportRecoveryOutcome(ImportRecoveryAction.RECONCILING, reconciling)

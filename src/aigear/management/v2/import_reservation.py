"""Atomic reservation for one exact external import."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportControlSnapshot,
    ImportOperationRecord,
    ImportPhase,
)

__all__ = [
    "ImportReservationError",
    "ImportReservationConflict",
    "compute_import_idempotency_key_hash",
    "compute_import_request_fingerprint",
    "reserve_import_operation",
]


class ImportReservationError(ValueError):
    pass


class ImportReservationConflict(ImportReservationError):
    pass


def compute_import_idempotency_key_hash(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ImportReservationError("idempotency_key must be a non-empty str")
    return digest_sha256_of_jcs(["aigear.import-idempotency.v2", idempotency_key])


def compute_import_request_fingerprint(
    *,
    source: ExactImportSource,
    target_quarantine_prefix: str,
    write_budget: int,
) -> TypedId:
    if not isinstance(source, ExactImportSource):
        raise ImportReservationError("source must be an ExactImportSource")
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            [
                "aigear.import-request.v2",
                {
                    "environment_id": source.environment_id,
                    "project_id": source.project_id,
                    "bucket": source.bucket,
                    "object_name": source.object_name,
                    "generation": source.generation,
                    "region": source.region,
                    "size_bytes": source.size_bytes,
                },
                target_quarantine_prefix,
                write_budget,
            ]
        )
    )


def reserve_import_operation(
    registry,
    *,
    control: ControlDocument,
    source: ExactImportSource,
    idempotency_key: str,
    operation_id: str,
    owner_principal: str,
    target_quarantine_prefix: str,
    write_budget: int,
    now: str,
    lease_ttl_seconds: int = 300,
) -> ImportOperationRecord:
    if not isinstance(control, ControlDocument):
        raise ImportReservationError("control must be a ControlDocument")
    if control.authority != "v2" or control.phase not in {
        "v2_authoritative",
        "compatibility_window",
        "complete",
    }:
        raise ImportReservationError("V2 Registry is not writable")
    if source.environment_id != control.environment_id:
        raise ImportReservationError("source environment does not match Registry control")
    try:
        instant = datetime.fromisoformat(now)
    except (TypeError, ValueError) as exc:
        raise ImportReservationError("now must be an ISO timestamp") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ImportReservationError("now must be timezone-aware")
    if (
        isinstance(lease_ttl_seconds, bool)
        or not isinstance(lease_ttl_seconds, int)
        or lease_ttl_seconds < 1
    ):
        raise ImportReservationError("lease_ttl_seconds must be positive")
    key_hash = compute_import_idempotency_key_hash(idempotency_key)
    fingerprint = compute_import_request_fingerprint(
        source=source,
        target_quarantine_prefix=target_quarantine_prefix,
        write_budget=write_budget,
    )
    lease_expires_at = (instant + timedelta(seconds=lease_ttl_seconds)).isoformat()
    control_snapshot = ImportControlSnapshot(
        environment_id=control.environment_id,
        environment_fingerprint=control.environment_fingerprint,
        firestore_database_id=control.registry_binding.firestore_database_id,
        registry_binding_id=control.registry_binding.registry_binding_id,
        registry_binding_epoch=control.registry_binding.registry_binding_epoch,
        write_epoch=control.write_epoch,
    )

    def reserve(tx):
        current_control = tx.get_control_document()
        if current_control != control:
            raise ImportReservationConflict(
                "Registry control/binding/write epoch changed during reservation"
            )
        existing = tx.get_import_operation(key_hash)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ImportReservationConflict(
                    "idempotency key is already bound to a different import request"
                )
            existing_expiry = (
                None
                if existing.lease_expires_at is None
                else datetime.fromisoformat(existing.lease_expires_at)
            )
            if (
                existing.phase == ImportPhase.RESERVED
                and existing_expiry is not None
                and existing_expiry <= instant
                and existing.owner_principal != owner_principal
            ):
                takeover = replace(
                    existing,
                    owner_principal=owner_principal,
                    fencing_token=existing.fencing_token + 1,
                    revision=existing.revision + 1,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                return tx.put_import_operation(takeover)
            return existing
        record = ImportOperationRecord(
            schema_version="2.0",
            operation_id=operation_id,
            idempotency_key_hash=key_hash,
            request_fingerprint=fingerprint,
            source=source,
            target_environment_id=control.environment_id,
            target_quarantine_prefix=target_quarantine_prefix,
            control_snapshot=control_snapshot,
            owner_principal=owner_principal,
            write_budget=write_budget,
            fencing_token=1,
            phase=ImportPhase.RESERVED,
            revision=1,
            lease_expires_at=lease_expires_at,
            created_at=now,
            updated_at=now,
        )
        return tx.put_import_operation(record)

    return registry.run_atomic(reserve)

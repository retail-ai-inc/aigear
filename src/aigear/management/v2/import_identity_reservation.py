"""Journal-first reservation for imported AssetVersion/Label identities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_prepare import PreparedExternalImport
from aigear.management.v2.records.label import compute_label_id
from aigear.management.v2.security_journal import (
    JournalEntryPhase,
    SecurityJournal,
    SecurityJournalEntry,
)

__all__ = [
    "ImportIdentityReservationError",
    "ImportIdentityReservation",
    "import_identity_journal_prefix",
    "reserve_import_identity",
    "verify_import_identity_reservation",
]


class ImportIdentityReservationError(ValueError):
    pass


def import_identity_journal_prefix(base_prefix: str, label_id: TypedId) -> str:
    if (
        not isinstance(base_prefix, str)
        or not base_prefix
        or base_prefix.startswith("/")
        or base_prefix.endswith("/")
    ):
        raise ImportIdentityReservationError(
            "base_prefix must be a relative prefix without trailing slash"
        )
    if not isinstance(label_id, TypedId):
        raise ImportIdentityReservationError("label_id must be a TypedId")
    return f"{base_prefix}/import-identities/{label_id.bare}"


def _evidence_core(prepared: PreparedExternalImport, label_id: TypedId) -> dict:
    declaration = prepared.declaration
    return {
        "domain": "aigear.import-identity-reservation.v2",
        "schema_version": "2.0",
        "environment_fingerprint": prepared.environment_fingerprint.typed,
        "operation_id": prepared.operation_id,
        "fencing_token": prepared.fencing_token,
        "prepared_digest": prepared.prepared_digest.typed,
        "manifest_digest": prepared.asset_version_id.typed,
        "asset_version_id": prepared.asset_version_id.typed,
        "label_id": label_id.typed,
        "label": {
            "asset_type": declaration.asset_type,
            "asset_name": declaration.name,
            "display_version": declaration.display_version,
        },
    }


def _evidence_digest(prepared: PreparedExternalImport, label_id: TypedId) -> TypedId:
    return TypedId.from_bare(
        hashlib.sha256(canonicalize_json(_evidence_core(prepared, label_id))).hexdigest()
    )


@dataclass(frozen=True)
class ImportIdentityReservation:
    operation_id: str
    fencing_token: int
    environment_fingerprint: TypedId
    asset_version_id: TypedId
    label_id: TypedId
    prepared_digest: TypedId
    evidence_digest: TypedId
    journal_sequence: int
    journal_entry_id: TypedId
    journal_entry_object_name: str
    journal_entry_generation: str


def reserve_import_identity(
    prepared: PreparedExternalImport,
    *,
    journal: SecurityJournal,
    issued_at: str,
) -> ImportIdentityReservation:
    if not isinstance(prepared, PreparedExternalImport):
        raise ImportIdentityReservationError(
            "prepared must be a PreparedExternalImport"
        )
    try:
        issued = datetime.fromisoformat(issued_at)
    except (TypeError, ValueError) as exc:
        raise ImportIdentityReservationError(
            "issued_at must be an ISO timestamp"
        ) from exc
    if issued.tzinfo is None or issued.utcoffset() is None:
        raise ImportIdentityReservationError("issued_at must be timezone-aware")
    label_id = compute_label_id(
        prepared.declaration.asset_type,
        prepared.declaration.name,
        prepared.declaration.display_version,
    )
    expected_prefix = import_identity_journal_prefix(
        journal.object_prefix.rsplit("/import-identities/", 1)[0],
        label_id,
    )
    if journal.object_prefix != expected_prefix:
        raise ImportIdentityReservationError(
            "identity journal prefix does not match label_id"
        )
    evidence_digest = _evidence_digest(prepared, label_id)
    entry = journal.append(
        operation_id=prepared.operation_id,
        event_kind="import-identity",
        phase=JournalEntryPhase.PREPARED,
        subject_id=label_id.typed,
        evidence_digest=evidence_digest,
        issued_at=issued_at,
        expected_previous_sequence=0,
        expected_previous_entry_id=None,
    )
    reservation = ImportIdentityReservation(
        operation_id=prepared.operation_id,
        fencing_token=prepared.fencing_token,
        environment_fingerprint=prepared.environment_fingerprint,
        asset_version_id=prepared.asset_version_id,
        label_id=label_id,
        prepared_digest=prepared.prepared_digest,
        evidence_digest=evidence_digest,
        journal_sequence=entry.sequence,
        journal_entry_id=entry.entry_id,
        journal_entry_object_name=entry.object_name,
        journal_entry_generation=entry.generation,
    )
    verify_import_identity_reservation(
        prepared, reservation=reservation, journal_entry=entry
    )
    return reservation


def verify_import_identity_reservation(
    prepared: PreparedExternalImport,
    *,
    reservation: ImportIdentityReservation,
    journal_entry: SecurityJournalEntry,
) -> None:
    expected_label_id = compute_label_id(
        prepared.declaration.asset_type,
        prepared.declaration.name,
        prepared.declaration.display_version,
    )
    expected_digest = _evidence_digest(prepared, expected_label_id)
    if (
        not isinstance(reservation, ImportIdentityReservation)
        or journal_entry.sequence != 1
        or journal_entry.unsigned.get("event_kind") != "import-identity"
        or journal_entry.unsigned.get("phase") != JournalEntryPhase.PREPARED.value
        or journal_entry.unsigned.get("subject_id") != expected_label_id.typed
        or journal_entry.unsigned.get("evidence_digest") != expected_digest.typed
        or reservation.operation_id != prepared.operation_id
        or reservation.fencing_token != prepared.fencing_token
        or reservation.environment_fingerprint != prepared.environment_fingerprint
        or reservation.asset_version_id != prepared.asset_version_id
        or reservation.label_id != expected_label_id
        or reservation.prepared_digest != prepared.prepared_digest
        or reservation.evidence_digest != expected_digest
        or reservation.journal_sequence != journal_entry.sequence
        or reservation.journal_entry_id != journal_entry.entry_id
        or reservation.journal_entry_object_name != journal_entry.object_name
        or reservation.journal_entry_generation != journal_entry.generation
    ):
        raise ImportIdentityReservationError(
            "journal reservation does not match prepared import identity"
        )

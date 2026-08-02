"""Journal-first evidence for production policy decisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_completion import AuthenticatedPolicyCompletion
from aigear.management.v2.records.policy import (
    PolicyDecisionJournalReceipt,
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    PolicyDecisionReservationLock,
    compute_subject_epoch_key,
)
from aigear.management.v2.security_journal import (
    JournalEntryPhase,
    SecurityJournalEntry,
)

__all__ = [
    "PolicyJournalError",
    "PolicyJournalConflict",
    "compute_policy_journal_evidence_digest",
    "prepare_policy_decision_journal",
    "verify_prepared_policy_journal",
    "append_policy_decision_committed",
]


class PolicyJournalError(ValueError):
    pass


class PolicyJournalConflict(PolicyJournalError):
    pass


def _parse_time(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise PolicyJournalError(
            f"{field_name} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyJournalError(f"{field_name} must be timezone-aware")
    return parsed


def _prepared_evidence(operation: PolicyDecisionOperationRecord) -> dict:
    if operation.verified_completion is None:
        raise PolicyJournalError("policy operation lacks verified completion")
    envelope = operation.request.unsigned_envelope
    return {
        "domain": "aigear.policy-decision-journal-prepared.v2",
        "schema_version": operation.schema_version,
        "environment_fingerprint": envelope.environment_fingerprint.typed,
        "operation_id": operation.operation_id,
        "request_fingerprint": operation.request_fingerprint.typed,
        "fencing_token": operation.fencing_token,
        "subject_asset_version_id": envelope.subject_asset_version_id.typed,
        "subject_epoch_key": compute_subject_epoch_key(
            envelope.subject_asset_version_id,
            envelope.decision_epoch,
        ).typed,
        "decision": envelope.decision.value,
        "decision_epoch": envelope.decision_epoch,
        "attestation_id": operation.verified_completion.attestation_id.typed,
        "policy_version": envelope.policy_version,
        "policy_snapshot_digest": envelope.policy_snapshot_digest.typed,
        "expected_head_revision": operation.request.expected_head_revision,
        "completion_verification_digest": (
            operation.verified_completion.verification_digest.typed
        ),
    }


def compute_policy_journal_evidence_digest(
    operation: PolicyDecisionOperationRecord,
) -> TypedId:
    if not isinstance(operation, PolicyDecisionOperationRecord):
        raise PolicyJournalError(
            "operation must be PolicyDecisionOperationRecord"
        )
    return TypedId.from_bare(
        digest_sha256_of_jcs(_prepared_evidence(operation))
    )


def _validate_lock(
    operation: PolicyDecisionOperationRecord,
    lock: PolicyDecisionReservationLock,
) -> None:
    envelope = operation.request.unsigned_envelope
    if (
        not isinstance(lock, PolicyDecisionReservationLock)
        or lock.environment_fingerprint != envelope.environment_fingerprint
        or lock.subject_asset_version_id != envelope.subject_asset_version_id
        or lock.expected_head_revision
        != operation.request.expected_head_revision
        or lock.decision_epoch != envelope.decision_epoch
        or lock.operation_id != operation.operation_id
        or lock.idempotency_key_hash != operation.idempotency_key_hash
        or lock.request_fingerprint != operation.request_fingerprint
        or lock.fencing_token != operation.fencing_token
        or lock.lease_expires_at != operation.lease_expires_at
    ):
        raise PolicyJournalConflict(
            "policy operation does not own the current reservation fence"
        )


def _validate_entry(
    entry: SecurityJournalEntry,
    *,
    operation: PolicyDecisionOperationRecord,
    phase: JournalEntryPhase,
    evidence_digest: TypedId,
    issued_at: str,
) -> None:
    envelope = operation.request.unsigned_envelope
    expected = {
        "operation_id": operation.operation_id,
        "event_kind": "policy-decision",
        "phase": phase.value,
        "subject_id": compute_subject_epoch_key(
            envelope.subject_asset_version_id,
            envelope.decision_epoch,
        ).typed,
        "evidence_digest": evidence_digest.typed,
        "issued_at": issued_at,
    }
    if any(entry.unsigned.get(key) != value for key, value in expected.items()):
        raise PolicyJournalConflict(
            "journal entry does not match policy decision evidence"
        )


def prepare_policy_decision_journal(
    operation: PolicyDecisionOperationRecord,
    authenticated: AuthenticatedPolicyCompletion,
    *,
    current_lock: PolicyDecisionReservationLock,
    journal,
    issued_at: str,
    expected_previous_sequence: int,
    expected_previous_entry_id: TypedId | None,
) -> PolicyDecisionOperationRecord:
    if not isinstance(authenticated, AuthenticatedPolicyCompletion):
        raise PolicyJournalError(
            "authenticated must be AuthenticatedPolicyCompletion"
        )
    if operation.verified_completion != authenticated.verification:
        raise PolicyJournalConflict(
            "authenticated completion does not match policy operation"
        )
    _validate_lock(operation, current_lock)
    if operation.phase is PolicyDecisionOperationPhase.JOURNALING:
        verify_prepared_policy_journal(operation, journal=journal)
        return operation
    if operation.phase is not PolicyDecisionOperationPhase.VERIFIED:
        raise PolicyJournalError(
            "prepared journal requires a verified policy operation"
        )
    instant = _parse_time("issued_at", issued_at)
    if instant < _parse_time(
        "completion.verified_at", operation.verified_completion.verified_at
    ):
        raise PolicyJournalConflict(
            "prepared journal cannot precede completion verification"
        )
    if instant >= _parse_time(
        "reservation.lease_expires_at", current_lock.lease_expires_at
    ):
        raise PolicyJournalConflict("reservation lease expired before journal")
    evidence_digest = compute_policy_journal_evidence_digest(operation)
    envelope = operation.request.unsigned_envelope
    entry = journal.append(
        operation_id=operation.operation_id,
        event_kind="policy-decision",
        phase=JournalEntryPhase.PREPARED,
        subject_id=compute_subject_epoch_key(
            envelope.subject_asset_version_id,
            envelope.decision_epoch,
        ).typed,
        evidence_digest=evidence_digest,
        issued_at=issued_at,
        expected_previous_sequence=expected_previous_sequence,
        expected_previous_entry_id=expected_previous_entry_id,
    )
    _validate_entry(
        entry,
        operation=operation,
        phase=JournalEntryPhase.PREPARED,
        evidence_digest=evidence_digest,
        issued_at=issued_at,
    )
    receipt = PolicyDecisionJournalReceipt(
        schema_version=operation.schema_version,
        entry_id=entry.entry_id,
        sequence=entry.sequence,
        object_name=entry.object_name,
        generation=entry.generation,
        evidence_digest=evidence_digest,
        issued_at=issued_at,
    )
    return replace(
        operation,
        phase=PolicyDecisionOperationPhase.JOURNALING,
        revision=operation.revision + 1,
        updated_at=issued_at,
        prepared_journal=receipt,
    )


def verify_prepared_policy_journal(
    operation: PolicyDecisionOperationRecord,
    *,
    journal,
) -> SecurityJournalEntry:
    receipt = operation.prepared_journal
    if receipt is None:
        raise PolicyJournalError("policy operation lacks prepared journal")
    expected_digest = compute_policy_journal_evidence_digest(operation)
    if receipt.evidence_digest != expected_digest:
        raise PolicyJournalConflict(
            "prepared journal receipt is bound to different evidence"
        )
    entry = journal.read_entry(receipt.object_name, receipt.generation)
    if entry.entry_id != receipt.entry_id or entry.sequence != receipt.sequence:
        raise PolicyJournalConflict(
            "prepared journal receipt does not match exact entry"
        )
    _validate_entry(
        entry,
        operation=operation,
        phase=JournalEntryPhase.PREPARED,
        evidence_digest=expected_digest,
        issued_at=receipt.issued_at,
    )
    return entry


def append_policy_decision_committed(
    operation: PolicyDecisionOperationRecord,
    *,
    journal,
    registry_commit_digest: TypedId,
    issued_at: str,
    expected_previous_sequence: int,
    expected_previous_entry_id: TypedId,
) -> SecurityJournalEntry:
    if operation.phase is not PolicyDecisionOperationPhase.SUCCEEDED:
        raise PolicyJournalError(
            "committed journal requires a succeeded policy operation"
        )
    prepared = verify_prepared_policy_journal(operation, journal=journal)
    if not isinstance(registry_commit_digest, TypedId):
        raise PolicyJournalError("registry_commit_digest must be TypedId")
    committed_at = _parse_time("issued_at", issued_at)
    if committed_at < _parse_time("operation.finished_at", operation.finished_at):
        raise PolicyJournalConflict(
            "committed journal cannot precede Registry commit"
        )
    committed_digest = TypedId.from_bare(
        digest_sha256_of_jcs(
            {
                "domain": "aigear.policy-decision-journal-committed.v2",
                "prepared_entry_id": prepared.entry_id.typed,
                "prepared_evidence_digest": (
                    operation.prepared_journal.evidence_digest.typed
                ),
                "registry_commit_digest": registry_commit_digest.typed,
            }
        )
    )
    envelope = operation.request.unsigned_envelope
    entry = journal.append(
        operation_id=operation.operation_id,
        event_kind="policy-decision",
        phase=JournalEntryPhase.COMMITTED,
        subject_id=compute_subject_epoch_key(
            envelope.subject_asset_version_id,
            envelope.decision_epoch,
        ).typed,
        evidence_digest=committed_digest,
        issued_at=issued_at,
        expected_previous_sequence=expected_previous_sequence,
        expected_previous_entry_id=expected_previous_entry_id,
    )
    _validate_entry(
        entry,
        operation=operation,
        phase=JournalEntryPhase.COMMITTED,
        evidence_digest=committed_digest,
        issued_at=issued_at,
    )
    return entry

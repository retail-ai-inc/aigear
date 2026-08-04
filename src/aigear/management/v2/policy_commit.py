"""Atomic Registry commit for one journal-prepared policy decision."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_completion import AuthenticatedPolicyCompletion
from aigear.management.v2.policy_journal import (
    compute_policy_committed_journal_digest,
    verify_prepared_policy_journal,
)
from aigear.management.v2.records.asset_version import (
    LifecycleState,
    TrustState,
    validate_trust_transition,
)
from aigear.management.v2.records.outbox import (
    OUTBOX_PRIORITY_EMERGENCY,
    OutboxEventRecord,
    ProjectionKind,
)
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionEpochBinding,
    PolicyDecisionHead,
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    compute_subject_epoch_key,
)

__all__ = [
    "PolicyCommitError",
    "PolicyCommitConflict",
    "PolicyRegistryCommit",
    "commit_policy_decision_registry",
    "finalize_policy_decision_commit",
]


class PolicyCommitError(ValueError):
    pass


class PolicyCommitConflict(PolicyCommitError):
    pass


@dataclass(frozen=True)
class PolicyRegistryCommit:
    operation: PolicyDecisionOperationRecord
    epoch_binding: PolicyDecisionEpochBinding
    head: PolicyDecisionHead
    registry_commit_digest: TypedId


def _parse_time(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise PolicyCommitError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyCommitError(f"{field_name} must be timezone-aware")
    return parsed


def _commit_digest(
    operation: PolicyDecisionOperationRecord,
    binding: PolicyDecisionEpochBinding,
    head: PolicyDecisionHead,
    asset_revision: int,
) -> TypedId:
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            {
                "domain": "aigear.policy-registry-commit.v2",
                "operation_id": operation.operation_id,
                "fencing_token": operation.fencing_token,
                "prepared_entry_id": operation.prepared_journal.entry_id.typed,
                "subject_epoch_key": binding.subject_epoch_key.typed,
                "attestation_id": binding.attestation_id.typed,
                "head_revision": head.revision,
                "asset_record_revision": asset_revision,
            }
        )
    )


def _result_from_committed_state(tx, operation):
    envelope = operation.request.unsigned_envelope
    key = compute_subject_epoch_key(
        envelope.subject_asset_version_id, envelope.decision_epoch
    )
    binding = tx.get_policy_decision_epoch(key)
    head = tx.get_policy_decision_head(envelope.subject_asset_version_id)
    asset = tx.get_asset_version(envelope.subject_asset_version_id)
    if (
        binding is None
        or head is None
        or asset is None
        or binding.attestation_id
        != operation.verified_completion.attestation_id
        or head.current_epoch != envelope.decision_epoch
        or head.attestation_id != binding.attestation_id
        or asset.policy_decision_head_ref != binding.attestation_id
        or binding.environment_fingerprint != envelope.environment_fingerprint
        or binding.subject_asset_version_id != envelope.subject_asset_version_id
        or binding.decision != envelope.decision
        or binding.policy_version != envelope.policy_version
        or binding.not_before != envelope.not_before
        or binding.valid_until != envelope.valid_until
        or head.environment_fingerprint != envelope.environment_fingerprint
        or head.revision != operation.request.expected_head_revision + 1
        or head.decision != envelope.decision
        or head.policy_version != envelope.policy_version
        or head.not_before != envelope.not_before
        or head.valid_until != envelope.valid_until
        or asset.trust_state
        != (
            TrustState.APPROVED
            if envelope.decision is PolicyDecision.APPROVED
            else TrustState.REVOKED
        )
    ):
        raise PolicyCommitConflict("partial or conflicting policy commit state")
    return PolicyRegistryCommit(
        operation=operation,
        epoch_binding=binding,
        head=head,
        registry_commit_digest=_commit_digest(
            operation, binding, head, asset.record_revision
        ),
    )


def commit_policy_decision_registry(
    registry,
    operation: PolicyDecisionOperationRecord,
    authenticated: AuthenticatedPolicyCompletion,
    *,
    journal,
    committed_at: str,
) -> PolicyRegistryCommit:
    """Commit policy facts atomically after all signature/journal I/O."""

    if operation.phase is not PolicyDecisionOperationPhase.JOURNALING:
        raise PolicyCommitError("policy commit requires a journaling operation")
    if (
        not isinstance(authenticated, AuthenticatedPolicyCompletion)
        or authenticated.verification != operation.verified_completion
        or authenticated.attestation.attestation_id
        != operation.request.unsigned_envelope_digest
    ):
        raise PolicyCommitConflict("verified attestation does not match operation")
    verify_prepared_policy_journal(operation, journal=journal)
    instant = _parse_time("committed_at", committed_at)
    if instant >= _parse_time("lease_expires_at", operation.lease_expires_at):
        raise PolicyCommitConflict("policy reservation lease expired before commit")

    envelope = operation.request.unsigned_envelope
    subject_epoch_key = compute_subject_epoch_key(
        envelope.subject_asset_version_id, envelope.decision_epoch
    )
    binding = PolicyDecisionEpochBinding(
        schema_version=operation.schema_version,
        environment_fingerprint=envelope.environment_fingerprint,
        subject_epoch_key=subject_epoch_key,
        subject_asset_version_id=envelope.subject_asset_version_id,
        decision_epoch=envelope.decision_epoch,
        attestation_id=authenticated.attestation.attestation_id,
        decision=envelope.decision,
        policy_version=envelope.policy_version,
        not_before=envelope.not_before,
        valid_until=envelope.valid_until,
        created_at=committed_at,
    )

    def commit(tx):
        current = tx.get_policy_decision_operation(operation.idempotency_key_hash)
        if current is not None and current.phase is PolicyDecisionOperationPhase.COMMITTING:
            if (
                current.request_fingerprint != operation.request_fingerprint
                or current.fencing_token != operation.fencing_token
                or current.prepared_journal != operation.prepared_journal
            ):
                raise PolicyCommitConflict("policy operation commit identity changed")
            return _result_from_committed_state(tx, current)
        if current != operation:
            raise PolicyCommitConflict("policy operation changed before commit")
        lock = tx.get_policy_decision_reservation(envelope.subject_asset_version_id)
        if (
            lock is None
            or lock.operation_id != operation.operation_id
            or lock.fencing_token != operation.fencing_token
            or lock.request_fingerprint != operation.request_fingerprint
            or lock.expected_head_revision
            != operation.request.expected_head_revision
            or lock.decision_epoch != envelope.decision_epoch
        ):
            raise PolicyCommitConflict("policy reservation fence was lost")
        head = tx.get_policy_decision_head(envelope.subject_asset_version_id)
        actual_revision = 0 if head is None else head.revision
        actual_epoch = 0 if head is None else head.current_epoch
        if (
            actual_revision != operation.request.expected_head_revision
            or envelope.decision_epoch != actual_epoch + 1
        ):
            raise PolicyCommitConflict("policy head revision or epoch changed")
        asset = tx.get_asset_version(envelope.subject_asset_version_id)
        if (
            asset is None
            or asset.environment_id != envelope.environment_id
            or asset.environment_fingerprint != envelope.environment_fingerprint
            or asset.lifecycle_state is not LifecycleState.ACTIVE
        ):
            raise PolicyCommitConflict("policy subject asset is not active")
        target_trust = (
            TrustState.APPROVED
            if envelope.decision is PolicyDecision.APPROVED
            else TrustState.REVOKED
        )
        if asset.trust_state is not target_trust:
            try:
                validate_trust_transition(asset.trust_state, target_trust)
            except Exception as exc:
                raise PolicyCommitConflict("illegal policy trust transition") from exc
        new_head = PolicyDecisionHead(
            schema_version=operation.schema_version,
            environment_fingerprint=envelope.environment_fingerprint,
            subject_asset_version_id=envelope.subject_asset_version_id,
            current_epoch=envelope.decision_epoch,
            revision=actual_revision + 1,
            attestation_id=binding.attestation_id,
            decision=envelope.decision,
            policy_version=envelope.policy_version,
            not_before=envelope.not_before,
            valid_until=envelope.valid_until,
            updated_at=committed_at,
        )
        updated_asset = replace(
            asset,
            trust_state=target_trust,
            policy_decision_head_ref=binding.attestation_id,
            record_revision=asset.record_revision + 1,
        )
        committing = replace(
            operation,
            phase=PolicyDecisionOperationPhase.COMMITTING,
            revision=operation.revision + 1,
            updated_at=committed_at,
        )
        tx.put_attestation(authenticated.attestation)
        tx.put_policy_decision_epoch(binding)
        tx.put_policy_decision_head(new_head)
        tx.put_asset_version(updated_asset)
        tx.put_outbox_event(
            OutboxEventRecord.pending(
                schema_version=operation.schema_version,
                kind=ProjectionKind.POLICY_DECISION_AUDIT,
                subject_id=envelope.subject_asset_version_id,
                projection_schema_version="1.0",
                projection_source_revision=updated_asset.record_revision,
                created_at=committed_at,
            )
        )
        if envelope.decision is PolicyDecision.REVOKED:
            tx.put_outbox_event(
                OutboxEventRecord.pending(
                    schema_version=operation.schema_version,
                    kind=ProjectionKind.POLICY_REVOCATION_EMERGENCY,
                    subject_id=envelope.subject_asset_version_id,
                    projection_schema_version="1.0",
                    projection_source_revision=updated_asset.record_revision,
                    created_at=committed_at,
                    priority=OUTBOX_PRIORITY_EMERGENCY,
                )
            )
        tx.put_policy_decision_operation(committing)
        return PolicyRegistryCommit(
            operation=committing,
            epoch_binding=binding,
            head=new_head,
            registry_commit_digest=_commit_digest(
                committing, binding, new_head, updated_asset.record_revision
            ),
        )

    return registry.run_atomic(commit)


def finalize_policy_decision_commit(
    registry,
    result: PolicyRegistryCommit,
    *,
    journal,
    committed_journal_entry,
    finished_at: str,
) -> PolicyDecisionOperationRecord:
    operation = result.operation
    if operation.phase is not PolicyDecisionOperationPhase.COMMITTING:
        raise PolicyCommitError("finalize requires a committing operation")
    exact_entry = journal.read_entry(
        committed_journal_entry.object_name,
        committed_journal_entry.generation,
    )
    expected_evidence = compute_policy_committed_journal_digest(
        operation, result.registry_commit_digest
    )
    if (
        exact_entry != committed_journal_entry
        or committed_journal_entry.unsigned.get("phase") != "committed"
        or committed_journal_entry.unsigned.get("operation_id")
        != operation.operation_id
        or committed_journal_entry.unsigned.get("evidence_digest")
        != expected_evidence.typed
    ):
        raise PolicyCommitConflict("committed journal entry does not match operation")
    finish_time = _parse_time("finished_at", finished_at)
    journal_time = _parse_time(
        "committed_journal.issued_at",
        committed_journal_entry.unsigned.get("issued_at"),
    )
    if finish_time < journal_time:
        raise PolicyCommitConflict(
            "operation cannot finish before committed journal"
        )

    def finish(tx):
        current = tx.get_policy_decision_operation(operation.idempotency_key_hash)
        if current is None:
            raise PolicyCommitConflict("policy operation disappeared before finalize")
        if current.phase is PolicyDecisionOperationPhase.SUCCEEDED:
            if (
                current.request_fingerprint != operation.request_fingerprint
                or current.fencing_token != operation.fencing_token
                or current.prepared_journal != operation.prepared_journal
            ):
                raise PolicyCommitConflict("succeeded policy operation identity changed")
            return current
        if current != operation:
            raise PolicyCommitConflict("policy operation changed before finalize")
        _result_from_committed_state(tx, operation)
        succeeded = replace(
            operation,
            phase=PolicyDecisionOperationPhase.SUCCEEDED,
            revision=operation.revision + 1,
            lease_expires_at=None,
            updated_at=finished_at,
            finished_at=finished_at,
        )
        return tx.put_policy_decision_operation(succeeded)

    return registry.run_atomic(finish)

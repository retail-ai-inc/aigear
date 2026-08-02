from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from aigear.management.v2.attestation import (
    AttestationRecord,
    HmacTestSigner,
    HmacTestVerifier,
)
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_completion import AuthenticatedPolicyCompletion
from aigear.management.v2.policy_journal import (
    PolicyJournalConflict,
    append_policy_decision_committed,
    prepare_policy_decision_journal,
    verify_prepared_policy_journal,
)
from aigear.management.v2.record_codec import decode_record, encode_record
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    PolicyDecisionRequest,
    PolicyDecisionReservationLock,
    PolicyDecisionUnsignedEnvelope,
    PolicyDecisionVerificationRecord,
)
from aigear.management.v2.security_journal import SecurityJournal
from aigear.management.v2.security_journal import SecurityJournalConflictError

_FP = TypedId.from_bare("aa" * 32)
_SUBJECT = TypedId.from_bare("bb" * 32)
_FILTER = TypedId.from_bare("01" * 32)
_CLOSURE = TypedId.from_bare("02" * 32)
_POLICY = TypedId.from_bare("03" * 32)
_KEY = "projects/p/locations/l/keyRings/r/cryptoKeys/policy/cryptoKeyVersions/1"
_JOURNAL_KEY = (
    "projects/p/locations/l/keyRings/r/cryptoKeys/journal/cryptoKeyVersions/1"
)


def _journal(gcs=None):
    return SecurityJournal(
        gcs=gcs or FakeGcsClient(),
        object_prefix="security/policy-decisions",
        environment_fingerprint=_FP,
        signer=HmacTestSigner(b"journal", key_version=_JOURNAL_KEY),
        verifier=HmacTestVerifier(b"journal", key_version=_JOURNAL_KEY),
    )


def _setup(*, epoch=1):
    envelope = PolicyDecisionUnsignedEnvelope(
        schema_version="2.0",
        operation_id=f"policy-{epoch}",
        fencing_token=epoch,
        environment_id="production",
        environment_fingerprint=_FP,
        subject_asset_version_id=_SUBJECT,
        decision=PolicyDecision.APPROVED,
        decision_epoch=epoch,
        policy_version="policy-2026-07",
        policy_snapshot_digest=_POLICY,
        evidence_digests=(_FILTER, _CLOSURE),
        evidence_closure_digest=_CLOSURE,
        firestore_read_time="2026-07-28T00:00:00+00:00",
        issued_at="2026-07-28T00:00:01+00:00",
        not_before="2026-07-28T00:00:01+00:00",
        valid_until="2026-07-28T01:00:01+00:00",
        key_version=_KEY,
    )
    request_fingerprint = TypedId.from_bare(f"{10 + epoch:02x}" * 32)
    request = PolicyDecisionRequest(
        schema_version="2.0",
        operation_id=envelope.operation_id,
        request_fingerprint=request_fingerprint,
        expected_head_revision=epoch - 1,
        unsigned_envelope=envelope,
        unsigned_envelope_digest=envelope.digest,
    )
    verification_values = {
        "domain": "aigear.policy-completion-verification.v2",
        "schema_version": "2.0",
        "operation_id": envelope.operation_id,
        "request_fingerprint": request_fingerprint.typed,
        "fencing_token": epoch,
        "attestation_id": envelope.digest.typed,
        "message_id": f"message-{epoch}",
        "publisher_principal": "policy-attestor@example.test",
        "verified_at": "2026-07-28T00:00:03+00:00",
    }
    verification = PolicyDecisionVerificationRecord(
        schema_version="2.0",
        operation_id=envelope.operation_id,
        request_fingerprint=request_fingerprint,
        fencing_token=epoch,
        attestation_id=envelope.digest,
        message_id=f"message-{epoch}",
        publisher_principal="policy-attestor@example.test",
        verified_at="2026-07-28T00:00:03+00:00",
        verification_digest=TypedId.from_bare(
            digest_sha256_of_jcs(verification_values)
        ),
    )
    operation = PolicyDecisionOperationRecord(
        schema_version="2.0",
        operation_id=envelope.operation_id,
        idempotency_key_hash=f"{20 + epoch:02x}" * 32,
        request_fingerprint=request_fingerprint,
        request=request,
        policy_snapshot_digest=_POLICY,
        evidence_filter_digest=_FILTER,
        owner_principal="controller@example.test",
        fencing_token=epoch,
        phase=PolicyDecisionOperationPhase.VERIFIED,
        revision=3,
        lease_expires_at="2026-07-28T00:10:01+00:00",
        created_at="2026-07-28T00:00:01+00:00",
        updated_at=verification.verified_at,
        verified_completion=verification,
    )
    lock = PolicyDecisionReservationLock(
        schema_version="2.0",
        environment_fingerprint=_FP,
        subject_asset_version_id=_SUBJECT,
        expected_head_revision=epoch - 1,
        decision_epoch=epoch,
        operation_id=envelope.operation_id,
        idempotency_key_hash=operation.idempotency_key_hash,
        request_fingerprint=request_fingerprint,
        fencing_token=epoch,
        revision=epoch,
        lease_expires_at=operation.lease_expires_at,
        updated_at="2026-07-28T00:00:01+00:00",
    )
    signer = HmacTestSigner(b"policy", key_version=_KEY)
    signature = signer.sign_sha256_digest(bytes.fromhex(envelope.digest.bare))
    attestation = AttestationRecord(
        schema_version="2.0",
        attestation_kind="policy_decision",
        attestation_id=envelope.digest,
        environment_fingerprint=_FP,
        unsigned_envelope=envelope.to_jcs_dict(),
        key_version=_KEY,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )
    authenticated = AuthenticatedPolicyCompletion(
        verification=verification,
        attestation=attestation,
        reused_existing_attestation=False,
    )
    return operation, lock, authenticated


def _prepare(operation, lock, authenticated, journal):
    return prepare_policy_decision_journal(
        operation,
        authenticated,
        current_lock=lock,
        journal=journal,
        issued_at="2026-07-28T00:00:04+00:00",
        expected_previous_sequence=0,
        expected_previous_entry_id=None,
    )


def test_prepared_journal_binds_decision_and_advances_operation():
    operation, lock, authenticated = _setup()
    journal = _journal()

    journaled = _prepare(operation, lock, authenticated, journal)
    entry = verify_prepared_policy_journal(journaled, journal=journal)

    assert journaled.phase is PolicyDecisionOperationPhase.JOURNALING
    assert journaled.prepared_journal.entry_id == entry.entry_id
    assert journaled.prepared_journal.sequence == 1
    assert entry.unsigned["phase"] == "prepared"
    assert entry.unsigned["evidence_digest"] == (
        journaled.prepared_journal.evidence_digest.typed
    )
    assert decode_record(type(journaled), encode_record(journaled)) == journaled
    registry = FakeRegistryV2()
    registry.put_policy_decision_operation(operation)
    registry.put_policy_decision_operation(journaled)
    assert registry.get_policy_decision_operation(
        operation.idempotency_key_hash
    ) == journaled


def test_prepared_journal_retry_reuses_exact_entry():
    operation, lock, authenticated = _setup()
    journal = _journal()
    first = _prepare(operation, lock, authenticated, journal)

    replay = _prepare(operation, lock, authenticated, journal)

    assert replay.prepared_journal == first.prepared_journal
    assert journal.read_head().sequence == 1

    with pytest.raises(PolicyJournalConflict, match="fence"):
        prepare_policy_decision_journal(
            first,
            authenticated,
            current_lock=replace(
                lock,
                fencing_token=lock.fencing_token + 1,
                revision=lock.revision + 1,
            ),
            journal=journal,
            issued_at="2026-07-28T00:00:04+00:00",
            expected_previous_sequence=0,
            expected_previous_entry_id=None,
        )


def test_ack_loss_retry_does_not_allocate_another_sequence():
    operation, lock, authenticated = _setup()
    delegate = _journal()

    class AckLossJournal:
        def __init__(self):
            self.failed = False

        def append(self, **kwargs):
            entry = delegate.append(**kwargs)
            if not self.failed:
                self.failed = True
                raise RuntimeError("ACK lost")
            return entry

        def read_entry(self, object_name, generation):
            return delegate.read_entry(object_name, generation)

    journal = AckLossJournal()
    with pytest.raises(RuntimeError, match="ACK lost"):
        _prepare(operation, lock, authenticated, journal)

    recovered = _prepare(operation, lock, authenticated, journal)

    assert recovered.prepared_journal.sequence == 1
    assert delegate.read_head().sequence == 1


def test_prepared_receipt_cannot_be_reused_for_another_epoch():
    operation, lock, authenticated = _setup(epoch=1)
    journal = _journal()
    first = _prepare(operation, lock, authenticated, journal)
    other, _, _ = _setup(epoch=2)
    forged = replace(
        other,
        phase=PolicyDecisionOperationPhase.JOURNALING,
        prepared_journal=first.prepared_journal,
    )

    with pytest.raises(PolicyJournalConflict, match="different evidence"):
        verify_prepared_policy_journal(forged, journal=journal)


def test_stale_fence_cannot_prepare_journal():
    operation, lock, authenticated = _setup()
    stale = replace(lock, fencing_token=lock.fencing_token + 1, revision=2)

    with pytest.raises(PolicyJournalConflict, match="fence"):
        _prepare(operation, stale, authenticated, _journal())


def test_committed_journal_binds_registry_commit_and_is_idempotent():
    operation, lock, authenticated = _setup()
    journal = _journal()
    journaled = _prepare(operation, lock, authenticated, journal)
    succeeded = replace(
        journaled,
        phase=PolicyDecisionOperationPhase.SUCCEEDED,
        revision=journaled.revision + 1,
        lease_expires_at=None,
        updated_at="2026-07-28T00:00:06+00:00",
        finished_at="2026-07-28T00:00:06+00:00",
    )
    commit_digest = TypedId.from_bare("ee" * 32)

    first = append_policy_decision_committed(
        succeeded,
        journal=journal,
        registry_commit_digest=commit_digest,
        issued_at="2026-07-28T00:00:07+00:00",
        expected_previous_sequence=journaled.prepared_journal.sequence,
        expected_previous_entry_id=journaled.prepared_journal.entry_id,
    )
    replay = append_policy_decision_committed(
        succeeded,
        journal=journal,
        registry_commit_digest=commit_digest,
        issued_at="2026-07-28T00:00:07+00:00",
        expected_previous_sequence=journaled.prepared_journal.sequence,
        expected_previous_entry_id=journaled.prepared_journal.entry_id,
    )

    assert replay == first
    assert first.sequence == 2
    assert first.unsigned["phase"] == "committed"

    with pytest.raises(
        SecurityJournalConflictError,
        match="different evidence|committed",
    ):
        append_policy_decision_committed(
            succeeded,
            journal=journal,
            registry_commit_digest=TypedId.from_bare("ff" * 32),
            issued_at="2026-07-28T00:00:07+00:00",
            expected_previous_sequence=journaled.prepared_journal.sequence,
            expected_previous_entry_id=journaled.prepared_journal.entry_id,
        )

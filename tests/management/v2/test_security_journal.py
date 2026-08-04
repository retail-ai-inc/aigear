from __future__ import annotations

import json

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.security_journal import (
    JournalEntryPhase,
    SecurityJournal,
    SecurityJournalConflictError,
    SecurityJournalError,
)

_FP = TypedId.from_bare("aa" * 32)
_EVIDENCE = TypedId.from_bare("bb" * 32)


def _journal(gcs=None, *, secret=b"journal-secret"):
    gcs = gcs or FakeGcsClient()
    key = "projects/p/locations/l/keyRings/r/cryptoKeys/journal/cryptoKeyVersions/1"
    return (
        SecurityJournal(
            gcs=gcs,
            object_prefix="security/journal",
            environment_fingerprint=_FP,
            signer=HmacTestSigner(secret, key_version=key),
            verifier=HmacTestVerifier(secret, key_version=key),
        ),
        gcs,
    )


def _append(journal, **overrides):
    values = {
        "operation_id": "policy-op-1",
        "event_kind": "policy-decision",
        "phase": JournalEntryPhase.PREPARED,
        "subject_id": "sha256:" + "cc" * 32,
        "evidence_digest": _EVIDENCE,
        "issued_at": "2026-07-28T00:00:00+00:00",
        "expected_previous_sequence": 0,
        "expected_previous_entry_id": None,
    }
    values.update(overrides)
    return journal.append(**values)


def test_first_append_creates_signed_entry_and_head():
    journal, _ = _journal()
    entry = _append(journal)
    head = journal.read_head()
    assert entry.sequence == 1
    assert head.sequence == 1
    assert head.entry_id == entry.entry_id


def test_ack_loss_retry_returns_same_entry():
    journal, _ = _journal()
    first = _append(journal)
    retry = _append(journal)
    assert retry == first


def test_same_sequence_different_evidence_conflicts():
    journal, _ = _journal()
    _append(journal)
    with pytest.raises(SecurityJournalConflictError, match="different evidence"):
        _append(journal, evidence_digest=TypedId.from_bare("dd" * 32))


def test_second_append_requires_exact_previous_head():
    journal, _ = _journal()
    first = _append(journal)
    second = _append(
        journal,
        operation_id="policy-op-2",
        phase=JournalEntryPhase.COMMITTED,
        expected_previous_sequence=1,
        expected_previous_entry_id=first.entry_id,
    )
    assert second.sequence == 2
    assert second.previous_entry_id == first.entry_id


def test_stale_previous_head_is_rejected():
    journal, _ = _journal()
    first = _append(journal)
    _append(
        journal,
        operation_id="policy-op-2",
        phase=JournalEntryPhase.COMMITTED,
        expected_previous_sequence=1,
        expected_previous_entry_id=first.entry_id,
    )
    with pytest.raises(SecurityJournalConflictError, match="committed|head changed"):
        _append(
            journal,
            operation_id="policy-op-3",
            expected_previous_sequence=1,
            expected_previous_entry_id=first.entry_id,
        )


def test_tampered_head_is_rejected():
    journal, gcs = _journal()
    _append(journal)
    snapshot = gcs.get_live_object(journal.head_object_name)
    document = json.loads(snapshot.data)
    document["unsigned"]["sequence"] = 99
    gcs.put_object(
        journal.head_object_name,
        json.dumps(document).encode(),
        if_generation_match=int(snapshot.generation),
    )
    with pytest.raises(SecurityJournalError, match="head_id"):
        journal.read_head()


def test_wrong_verifier_key_rejects_existing_journal():
    journal, gcs = _journal()
    _append(journal)
    wrong, _ = _journal(gcs, secret=b"wrong-secret")
    with pytest.raises(SecurityJournalError, match="verification failed"):
        wrong.read_head()


def test_invalid_initial_previous_identity_is_rejected():
    journal, _ = _journal()
    with pytest.raises(SecurityJournalError, match="sequence zero"):
        _append(
            journal,
            expected_previous_entry_id=TypedId.from_bare("ee" * 32),
        )

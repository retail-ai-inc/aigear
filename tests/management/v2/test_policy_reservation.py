from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_evidence import (
    EvidenceNodeKind,
    PolicyEvidenceClosure,
    PolicyEvidenceNode,
    _closure_digest,
)
from aigear.management.v2.policy_reservation import (
    PolicyReservationBusy,
    PolicyReservationConflict,
    PolicyReservationError,
    reserve_policy_decision,
)
from aigear.management.v2.record_codec import decode_record, encode_record
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionHead,
    PolicyDecisionOperationPhase,
)

_READ_TIME = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
_SERVER_TIME = _READ_TIME + timedelta(seconds=1)
_FP = TypedId.from_bare("aa" * 32)
_SUBJECT = TypedId.from_bare("bb" * 32)
_POLICY_SNAPSHOT = TypedId.from_bare("cc" * 32)
_ATTESTATION = TypedId.from_bare("dd" * 32)
_KEY_VERSION = (
    "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1"
)


def _closure(
    *,
    read_time: datetime = _READ_TIME,
    approvable: bool = True,
) -> PolicyEvidenceClosure:
    evidence = {"asset_version_id": _SUBJECT.typed}
    node = PolicyEvidenceNode(
        kind=EvidenceNodeKind.ASSET_VERSION,
        identity=_SUBJECT.typed,
        evidence_digest=TypedId.from_bare(
            digest_sha256_of_jcs(
                [
                    "aigear.policy-evidence-node.v2",
                    EvidenceNodeKind.ASSET_VERSION.value,
                    _SUBJECT.typed,
                    evidence,
                ]
            )
        ),
        evidence=evidence,
    )
    values = {
        "subject_asset_version_id": _SUBJECT,
        "environment_fingerprint": _FP,
        "read_time": read_time.isoformat(),
        "filter_digest": TypedId.from_bare("01" * 32),
        "nodes": (node,),
        "links": (),
        "approvable": approvable,
        "rejection_reasons": () if approvable else ("unknown_producer",),
    }
    provisional = PolicyEvidenceClosure.__new__(PolicyEvidenceClosure)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    return PolicyEvidenceClosure(
        **values,
        closure_digest=_closure_digest(provisional),
    )


def _reserve(
    registry: FakeRegistryV2,
    *,
    decision: PolicyDecision = PolicyDecision.APPROVED,
    closure: PolicyEvidenceClosure | None = None,
    idempotency_key: str = "request-1",
    owner_principal: str = "controller@example.test",
):
    return reserve_policy_decision(
        registry,
        environment_id="production",
        environment_fingerprint=_FP,
        decision=decision,
        closure=closure or _closure(),
        policy_version="policy-2026-07",
        policy_snapshot_digest=_POLICY_SNAPSHOT,
        key_version=_KEY_VERSION,
        idempotency_key=idempotency_key,
        owner_principal=owner_principal,
        validity_seconds=3600,
        max_evidence_age_seconds=300,
        lease_ttl_seconds=60,
    )


def test_reservation_freezes_server_time_epoch_policy_and_evidence():
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)

    operation = _reserve(registry)
    envelope = operation.request.unsigned_envelope

    assert operation.phase is PolicyDecisionOperationPhase.RESERVED
    assert operation.request.expected_head_revision == 0
    assert envelope.decision_epoch == 1
    assert envelope.issued_at == _SERVER_TIME.isoformat()
    assert envelope.not_before == _SERVER_TIME.isoformat()
    assert envelope.valid_until == (
        _SERVER_TIME + timedelta(hours=1)
    ).isoformat()
    assert envelope.policy_snapshot_digest == _POLICY_SNAPSHOT
    assert operation.evidence_filter_digest in envelope.evidence_digests
    assert envelope.evidence_closure_digest in envelope.evidence_digests
    assert tuple(
        sorted(envelope.evidence_digests, key=lambda value: value.typed)
    ) == envelope.evidence_digests
    assert operation.fencing_token == envelope.fencing_token == 1
    assert operation.request.unsigned_envelope_digest == envelope.digest


def test_operation_and_reservation_lock_round_trip_through_registry_codec():
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    operation = _reserve(registry)
    lock = registry.get_policy_decision_reservation(_SUBJECT)

    assert (
        decode_record(type(operation), encode_record(operation))
        == operation
    )
    assert decode_record(type(lock), encode_record(lock)) == lock


def test_idempotent_retry_does_not_refetch_time_or_rebuild_envelope():
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    original = _reserve(registry)
    registry._server_read_time = _SERVER_TIME + timedelta(days=1)

    replay = _reserve(registry)

    assert replay == original
    assert replay.request.unsigned_envelope.issued_at == _SERVER_TIME.isoformat()


@pytest.mark.parametrize(
    "changed",
    [
        {"decision": PolicyDecision.REVOKED},
        {"owner_principal": "other-controller@example.test"},
    ],
)
def test_idempotency_key_is_bound_to_the_semantic_request(changed):
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    _reserve(registry)

    with pytest.raises(PolicyReservationConflict, match="another policy request"):
        _reserve(registry, **changed)

    assert len(registry._policy_decision_operations) == 1


def test_live_approve_and_revoke_reservations_cannot_both_succeed():
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    approved = _reserve(registry, idempotency_key="approve")

    with pytest.raises(PolicyReservationBusy, match="live"):
        _reserve(
            registry,
            decision=PolicyDecision.REVOKED,
            idempotency_key="revoke",
        )

    lock = registry.get_policy_decision_reservation(_SUBJECT)
    assert lock.operation_id == approved.operation_id
    assert len(registry._policy_decision_operations) == 1


def test_expired_early_reservation_is_cancelled_and_fenced():
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    approved = _reserve(registry, idempotency_key="approve")
    registry._server_read_time = _SERVER_TIME + timedelta(seconds=61)

    revoked = _reserve(
        registry,
        decision=PolicyDecision.REVOKED,
        idempotency_key="revoke",
    )

    cancelled = registry.get_policy_decision_operation(
        approved.idempotency_key_hash
    )
    assert cancelled.phase is PolicyDecisionOperationPhase.CANCELLED
    assert cancelled.finished_at == registry._server_read_time.isoformat()
    assert revoked.fencing_token == approved.fencing_token + 1
    assert (
        revoked.request.unsigned_envelope.fencing_token
        == revoked.fencing_token
    )


def test_verified_expired_reservation_requires_reconciliation():
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    operation = _reserve(registry, idempotency_key="approve")
    attesting = replace(
        operation,
        phase=PolicyDecisionOperationPhase.ATTESTING,
        revision=2,
    )
    registry.put_policy_decision_operation(attesting)
    registry.put_policy_decision_operation(
        replace(
            attesting,
            phase=PolicyDecisionOperationPhase.VERIFIED,
            revision=3,
        )
    )
    registry._server_read_time = _SERVER_TIME + timedelta(seconds=61)

    with pytest.raises(PolicyReservationBusy, match="reconciliation"):
        _reserve(
            registry,
            decision=PolicyDecision.REVOKED,
            idempotency_key="revoke",
        )


def test_reservation_uses_current_head_revision_and_next_epoch():
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    registry.put_policy_decision_head(
        PolicyDecisionHead(
            schema_version="2.0",
            environment_fingerprint=_FP,
            subject_asset_version_id=_SUBJECT,
            current_epoch=4,
            revision=7,
            attestation_id=_ATTESTATION,
            decision=PolicyDecision.APPROVED,
            policy_version="policy-previous",
            not_before=(_READ_TIME - timedelta(days=1)).isoformat(),
            valid_until=(_READ_TIME + timedelta(days=1)).isoformat(),
            updated_at=_READ_TIME.isoformat(),
        )
    )

    operation = _reserve(registry)

    assert operation.request.expected_head_revision == 7
    assert operation.request.unsigned_envelope.decision_epoch == 5


@pytest.mark.parametrize(
    "read_time,error",
    [
        (_SERVER_TIME + timedelta(microseconds=1), "later"),
        (_SERVER_TIME - timedelta(seconds=301), "too old"),
    ],
)
def test_future_or_stale_evidence_is_rejected(read_time, error):
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)

    with pytest.raises(PolicyReservationError, match=error):
        _reserve(registry, closure=_closure(read_time=read_time))

    assert not registry._policy_decision_operations
    assert not registry._policy_decision_reservations


def test_nonapprovable_closure_blocks_approval_but_allows_revocation():
    closure = _closure(approvable=False)
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)

    with pytest.raises(PolicyReservationError, match="approvable"):
        _reserve(registry, closure=closure)

    revoked = _reserve(
        registry,
        closure=closure,
        decision=PolicyDecision.REVOKED,
        idempotency_key="revoke",
    )
    assert (
        revoked.request.unsigned_envelope.decision
        is PolicyDecision.REVOKED
    )

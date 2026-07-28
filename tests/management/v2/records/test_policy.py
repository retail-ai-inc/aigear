from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.policy import (
    InvalidPolicyRecordError,
    PolicyDecision,
    PolicyDecisionConflictError,
    PolicyDecisionEpochBinding,
    PolicyDecisionHead,
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    PolicyDecisionRequest,
    PolicyDecisionReservationLock,
    PolicyDecisionUnsignedEnvelope,
    compute_subject_epoch_key,
    require_effective_policy_head,
    validate_policy_decision_operation_transition,
)

_FP = TypedId.from_bare("aa" * 32)
_SUBJECT = TypedId.from_bare("bb" * 32)
_EVIDENCE_A = TypedId.from_bare("01" * 32)
_EVIDENCE_B = TypedId.from_bare("02" * 32)
_CLOSURE = TypedId.from_bare("cc" * 32)
_ATTESTATION = TypedId.from_bare("dd" * 32)


def _envelope(**overrides) -> PolicyDecisionUnsignedEnvelope:
    values = {
        "schema_version": "2.0",
        "operation_id": "policy-1",
        "fencing_token": 1,
        "environment_id": "production",
        "environment_fingerprint": _FP,
        "subject_asset_version_id": _SUBJECT,
        "decision": PolicyDecision.APPROVED,
        "decision_epoch": 1,
        "policy_version": "policy-2026-07",
        "policy_snapshot_digest": TypedId.from_bare("ab" * 32),
        "evidence_digests": (_EVIDENCE_A, _EVIDENCE_B),
        "evidence_closure_digest": _CLOSURE,
        "firestore_read_time": "2026-07-28T00:00:00+00:00",
        "issued_at": "2026-07-28T00:00:01+00:00",
        "not_before": "2026-07-28T00:00:01+00:00",
        "valid_until": "2026-07-29T00:00:01+00:00",
        "key_version": "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1",
    }
    values.update(overrides)
    return PolicyDecisionUnsignedEnvelope(**values)


def _binding(**overrides) -> PolicyDecisionEpochBinding:
    values = {
        "schema_version": "2.0",
        "environment_fingerprint": _FP,
        "subject_epoch_key": compute_subject_epoch_key(_SUBJECT, 1),
        "subject_asset_version_id": _SUBJECT,
        "decision_epoch": 1,
        "attestation_id": _ATTESTATION,
        "decision": PolicyDecision.APPROVED,
        "policy_version": "policy-2026-07",
        "not_before": "2026-07-28T00:00:01+00:00",
        "valid_until": "2026-07-29T00:00:01+00:00",
    }
    values.update(overrides)
    return PolicyDecisionEpochBinding(**values)


def _head(**overrides) -> PolicyDecisionHead:
    values = {
        "schema_version": "2.0",
        "environment_fingerprint": _FP,
        "subject_asset_version_id": _SUBJECT,
        "current_epoch": 1,
        "revision": 2,
        "attestation_id": _ATTESTATION,
        "decision": PolicyDecision.APPROVED,
        "policy_version": "policy-2026-07",
        "not_before": "2026-07-28T00:00:01+00:00",
        "valid_until": "2026-07-29T00:00:01+00:00",
    }
    values.update(overrides)
    return PolicyDecisionHead(**values)


def test_subject_epoch_key_has_stable_cross_language_vector():
    assert compute_subject_epoch_key(_SUBJECT, 1).typed == (
        "sha256:c09ddb0c4793192c1012dc11f3791efb53369b9472809e885e6dead65bda66b7"
    )


def test_policy_envelope_digest_is_deterministic():
    assert _envelope().digest == _envelope().digest


def test_policy_envelope_rejects_unsorted_or_duplicate_evidence():
    with pytest.raises(InvalidPolicyRecordError, match="sorted"):
        _envelope(evidence_digests=(_EVIDENCE_B, _EVIDENCE_A))
    with pytest.raises(InvalidPolicyRecordError, match="duplicates"):
        _envelope(evidence_digests=(_EVIDENCE_A, _EVIDENCE_A))


def test_policy_envelope_rejects_invalid_time_window():
    with pytest.raises(InvalidPolicyRecordError, match="later than not_before"):
        _envelope(valid_until="2026-07-28T00:00:01+00:00")
    with pytest.raises(InvalidPolicyRecordError, match="timezone-aware"):
        _envelope(valid_until="2026-07-29T00:00:01")


def test_policy_request_binds_exact_unsigned_envelope():
    envelope = _envelope()
    request = PolicyDecisionRequest(
        schema_version="2.0",
        operation_id="policy-1",
        request_fingerprint=_EVIDENCE_A,
        expected_head_revision=0,
        unsigned_envelope=envelope,
        unsigned_envelope_digest=envelope.digest,
    )
    assert request.unsigned_envelope_digest == envelope.digest
    with pytest.raises(InvalidPolicyRecordError, match="does not match"):
        replace(request, unsigned_envelope_digest=_EVIDENCE_B)


def test_policy_operation_binds_fence_policy_and_evidence():
    envelope = _envelope()
    request = PolicyDecisionRequest(
        schema_version="2.0",
        operation_id="policy-1",
        request_fingerprint=_EVIDENCE_A,
        expected_head_revision=0,
        unsigned_envelope=envelope,
        unsigned_envelope_digest=envelope.digest,
    )
    operation = PolicyDecisionOperationRecord(
        schema_version="2.0",
        operation_id="policy-1",
        idempotency_key_hash="key-hash",
        request_fingerprint=_EVIDENCE_A,
        request=request,
        policy_snapshot_digest=envelope.policy_snapshot_digest,
        evidence_filter_digest=_EVIDENCE_A,
        owner_principal="controller@example.test",
        fencing_token=1,
        phase=PolicyDecisionOperationPhase.RESERVED,
        revision=1,
        lease_expires_at="2026-07-28T00:05:01+00:00",
        created_at="2026-07-28T00:00:01+00:00",
        updated_at="2026-07-28T00:00:01+00:00",
    )

    with pytest.raises(InvalidPolicyRecordError, match="fencing token"):
        replace(operation, fencing_token=2)
    with pytest.raises(InvalidPolicyRecordError, match="filter digest"):
        replace(operation, evidence_filter_digest=_CLOSURE)


def test_policy_reservation_lock_requires_a_live_lease():
    with pytest.raises(InvalidPolicyRecordError, match="later"):
        PolicyDecisionReservationLock(
            schema_version="2.0",
            environment_fingerprint=_FP,
            subject_asset_version_id=_SUBJECT,
            expected_head_revision=0,
            decision_epoch=1,
            operation_id="policy-1",
            idempotency_key_hash="key-hash",
            request_fingerprint=_EVIDENCE_A,
            fencing_token=1,
            revision=1,
            lease_expires_at="2026-07-28T00:00:01+00:00",
            updated_at="2026-07-28T00:00:01+00:00",
        )


def test_policy_operation_transition_rejects_terminal_rewrite():
    with pytest.raises(PolicyDecisionConflictError, match="illegal"):
        validate_policy_decision_operation_transition(
            PolicyDecisionOperationPhase.SUCCEEDED,
            PolicyDecisionOperationPhase.RECONCILING,
        )


def test_epoch_binding_validates_deterministic_key():
    with pytest.raises(InvalidPolicyRecordError, match="does not match"):
        _binding(subject_epoch_key=_EVIDENCE_A)


def test_same_epoch_same_binding_is_idempotent():
    binding = _binding()
    binding.assert_same_identity(binding)


def test_same_epoch_different_attestation_is_conflict():
    binding = _binding()
    with pytest.raises(PolicyDecisionConflictError, match="different evidence"):
        binding.assert_same_identity(
            replace(binding, attestation_id=TypedId.from_bare("ee" * 32))
        )


def test_epoch_zero_head_has_no_decision():
    head = PolicyDecisionHead(
        schema_version="2.0",
        environment_fingerprint=_FP,
        subject_asset_version_id=_SUBJECT,
        current_epoch=0,
        revision=1,
    )
    assert head.decision is None
    with pytest.raises(InvalidPolicyRecordError, match="epoch zero"):
        replace(head, decision=PolicyDecision.APPROVED)


def test_nonzero_head_requires_complete_binding():
    with pytest.raises(InvalidPolicyRecordError, match="requires all"):
        _head(attestation_id=None)


def test_effective_policy_head_accepts_current_approval():
    require_effective_policy_head(
        _head(),
        at="2026-07-28T12:00:00+00:00",
        required_policy_version="policy-2026-07",
    )


@pytest.mark.parametrize(
    "head,at,error",
    [
        (
            _head(decision=PolicyDecision.REVOKED),
            "2026-07-28T12:00:00+00:00",
            "not approved",
        ),
        (_head(), "2026-07-27T23:59:00+00:00", "not active yet"),
        (_head(), "2026-07-29T00:00:01+00:00", "expired"),
    ],
)
def test_ineffective_policy_head_is_rejected(head, at, error):
    with pytest.raises(InvalidPolicyRecordError, match=error):
        require_effective_policy_head(head, at=at)


def test_policy_version_mismatch_is_rejected():
    with pytest.raises(InvalidPolicyRecordError, match="version"):
        require_effective_policy_head(
            _head(),
            at="2026-07-28T12:00:00+00:00",
            required_policy_version="policy-new",
        )

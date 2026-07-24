from __future__ import annotations

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.blob_claim import (
    BlobClaim,
    ClaimState,
    InvalidBlobClaimError,
    InvalidClaimTransitionError,
    validate_claim_transition,
)

_BLOB_ID = TypedId.from_bare("aa" * 32)
_REQUEST_DIGEST = TypedId.from_bare("bb" * 32)


def _claim(**overrides) -> BlobClaim:
    defaults = dict(
        blob_id=_BLOB_ID,
        claim_epoch=0,
        fencing_token=1,
        operation_id="op-1",
        expected_object_name="registry/v2/_objects/sha256/aa/aaaa...",
        request_digest=_REQUEST_DIGEST,
        state=ClaimState.ADOPTING,
    )
    defaults.update(overrides)
    return BlobClaim(**defaults)


# ── BlobClaim construction ────────────────────────────────────────────────────


def test_blob_claim_accepts_well_formed_fields():
    claim = _claim()
    assert claim.state == ClaimState.ADOPTING
    assert claim.expected_generation is None
    assert claim.lease_expires_at is None


def test_blob_claim_accepts_optional_fields():
    claim = _claim(expected_generation="12345", lease_expires_at="2026-07-24T00:02:00Z")
    assert claim.expected_generation == "12345"
    assert claim.lease_expires_at == "2026-07-24T00:02:00Z"


def test_blob_claim_rejects_non_typed_id_blob_id():
    with pytest.raises(InvalidBlobClaimError):
        _claim(blob_id="not-a-typed-id")


def test_blob_claim_rejects_negative_fencing_token():
    with pytest.raises(InvalidBlobClaimError):
        _claim(fencing_token=-1)


def test_blob_claim_rejects_empty_operation_id():
    with pytest.raises(ValueError):
        _claim(operation_id="")


def test_blob_claim_rejects_non_claim_state():
    with pytest.raises(InvalidBlobClaimError):
        _claim(state="adopting")  # type: ignore[arg-type]


def test_blob_claim_rejects_empty_expected_generation():
    with pytest.raises(InvalidBlobClaimError):
        _claim(expected_generation="")


# ── validate_claim_transition ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,target",
    [
        (ClaimState.ADOPTING, ClaimState.CONSUMED),
        (ClaimState.ADOPTING, ClaimState.RELEASED),
        (ClaimState.ADOPTING, ClaimState.ADOPTING),
        (ClaimState.DELETE_INTENT, ClaimState.DELETED),
    ],
)
def test_valid_claim_transitions_are_accepted(current, target):
    validate_claim_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (ClaimState.ADOPTING, ClaimState.DELETE_INTENT),
        (ClaimState.DELETE_INTENT, ClaimState.CONSUMED),
        (ClaimState.DELETE_INTENT, ClaimState.ADOPTING),
        (ClaimState.CONSUMED, ClaimState.ADOPTING),
        (ClaimState.RELEASED, ClaimState.ADOPTING),
        (ClaimState.DELETED, ClaimState.ADOPTING),
    ],
)
def test_invalid_claim_transitions_are_rejected(current, target):
    with pytest.raises(InvalidClaimTransitionError):
        validate_claim_transition(current, target)


def test_claim_terminal_states_have_no_outgoing_transitions():
    for terminal in (ClaimState.CONSUMED, ClaimState.DELETED, ClaimState.RELEASED):
        for target in ClaimState:
            with pytest.raises(InvalidClaimTransitionError):
                validate_claim_transition(terminal, target)

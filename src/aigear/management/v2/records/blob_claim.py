"""``BlobClaim`` type and state machine (spec section 7).

``blob_claims/{full_sha256}`` is a mutual-exclusion fence over the GCS
canonical object for a Blob ID *before* a ``BlobRecord`` exists for it -- not
a business asset record. A finalizer or importer must hold an ``adopting``
claim before it may create or adopt a canonical generation with no
``BlobRecord`` yet; orphan GC must hold a ``delete_intent`` claim before it
may delete one. The two race on the same document, so only one side can ever
win the final window.

Spec 7's diagram:

```text
absent -> adopting -> consumed          # finalize creates BlobRecord + consumes, same transaction
absent -> delete_intent -> deleted      # orphan GC
adopting(expired) -> released | adopting(new fence)
deleted/released -> absent              # reconcile confirms external state, then cleans up
```

``absent`` is not a stored state -- it is simply "no claim document exists"
-- so it is not a member of :class:`ClaimState`; creating a claim is
constructing a new document, not a transition of an existing one. Likewise
``deleted``/``released -> absent`` is document deletion at the storage layer,
not a state-to-state transition, so it is not in
:data:`_VALID_CLAIM_TRANSITIONS` either.

``adopting(expired) -> adopting(new fence)`` (a takeover of an expired claim
by a higher fencing token) is represented here as the same-state edge
``ADOPTING -> ADOPTING``: whether the *existing* claim has actually expired
is a time-dependent fact this pure value type cannot see, so -- exactly like
every other lease-shaped state machine in this package (see
``AttemptRecord``/step lease handling, spec 10.3) -- that check is the
orchestration layer's job (a later task), not this transition validator's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "InvalidBlobClaimError",
    "InvalidClaimTransitionError",
    "ClaimState",
    "validate_claim_transition",
    "BlobClaim",
]


class InvalidBlobClaimError(ValueError):
    """Raised when a BlobClaim field is malformed."""


class InvalidClaimTransitionError(ValueError):
    """Raised when a BlobClaim ``state`` transition is not allowed (spec 7)."""


class ClaimState(str, Enum):
    ADOPTING = "adopting"
    CONSUMED = "consumed"
    DELETE_INTENT = "delete_intent"
    DELETED = "deleted"
    RELEASED = "released"


_VALID_CLAIM_TRANSITIONS = {
    ClaimState.ADOPTING: frozenset(
        {ClaimState.CONSUMED, ClaimState.RELEASED, ClaimState.ADOPTING}
    ),
    ClaimState.DELETE_INTENT: frozenset({ClaimState.DELETED}),
    ClaimState.CONSUMED: frozenset(),
    ClaimState.DELETED: frozenset(),
    ClaimState.RELEASED: frozenset(),
}


def validate_claim_transition(current: ClaimState, target: ClaimState) -> None:
    allowed = _VALID_CLAIM_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidClaimTransitionError(
            f"illegal BlobClaim state transition: {current.value!r} -> {target.value!r}"
        )


def _require_non_empty_str(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidBlobClaimError(f"{field_name} must be a non-empty str, got {value!r}")


def _require_typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidBlobClaimError(f"{field_name} must be a TypedId, got {type(value)!r}")


def _require_non_negative_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidBlobClaimError(f"{field_name} must be a non-negative int, got {value!r}")


@dataclass(frozen=True)
class BlobClaim:
    """One ``blob_claims/{full_sha256}`` document (spec 7)."""

    blob_id: TypedId
    claim_epoch: int
    fencing_token: int
    operation_id: str
    expected_object_name: str
    request_digest: TypedId
    state: ClaimState
    expected_generation: Optional[str] = None
    lease_expires_at: Optional[str] = None

    def __post_init__(self) -> None:
        _require_typed_id("blob_id", self.blob_id)
        _require_non_negative_int("claim_epoch", self.claim_epoch)
        _require_non_negative_int("fencing_token", self.fencing_token)
        object.__setattr__(
            self, "operation_id", validate_segment(self.operation_id, field_name="operation_id")
        )
        _require_non_empty_str("expected_object_name", self.expected_object_name)
        _require_typed_id("request_digest", self.request_digest)
        if not isinstance(self.state, ClaimState):
            raise InvalidBlobClaimError(f"state must be a ClaimState, got {self.state!r}")
        if self.expected_generation is not None:
            _require_non_empty_str("expected_generation", self.expected_generation)

"""Deterministic, fenced reservation of Pipeline V2 policy decisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_evidence import PolicyEvidenceClosure
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionHead,
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    PolicyDecisionRequest,
    PolicyDecisionReservationLock,
    PolicyDecisionUnsignedEnvelope,
)

__all__ = [
    "PolicyReservationError",
    "PolicyReservationConflict",
    "PolicyReservationBusy",
    "compute_policy_idempotency_key_hash",
    "compute_policy_request_fingerprint",
    "reserve_policy_decision",
]

_MAX_VALIDITY_SECONDS = 31 * 24 * 60 * 60
_MAX_NOT_BEFORE_DELAY_SECONDS = 24 * 60 * 60
_MAX_LEASE_TTL_SECONDS = 60 * 60


class PolicyReservationError(ValueError):
    pass


class PolicyReservationConflict(PolicyReservationError):
    pass


class PolicyReservationBusy(PolicyReservationConflict):
    pass


def _aware_utc(field_name: str, value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PolicyReservationError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _positive_bounded(field_name: str, value: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > maximum
    ):
        raise PolicyReservationError(
            f"{field_name} must be between 1 and {maximum}"
        )


def compute_policy_idempotency_key_hash(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise PolicyReservationError("idempotency_key must be a non-empty str")
    return digest_sha256_of_jcs(
        ["aigear.policy-decision-idempotency.v2", idempotency_key]
    )


def compute_policy_request_fingerprint(
    *,
    environment_id: str,
    environment_fingerprint: TypedId,
    decision: PolicyDecision,
    closure: PolicyEvidenceClosure,
    policy_version: str,
    policy_snapshot_digest: TypedId,
    key_version: str,
    owner_principal: str,
    validity_seconds: int,
    not_before_delay_seconds: int,
    max_evidence_age_seconds: int,
) -> TypedId:
    if not isinstance(environment_id, str) or not environment_id:
        raise PolicyReservationError("environment_id must be non-empty")
    if not isinstance(environment_fingerprint, TypedId):
        raise PolicyReservationError("environment_fingerprint must be TypedId")
    if not isinstance(decision, PolicyDecision):
        raise PolicyReservationError("decision must be PolicyDecision")
    if not isinstance(closure, PolicyEvidenceClosure):
        raise PolicyReservationError("closure must be PolicyEvidenceClosure")
    if not isinstance(policy_version, str) or not policy_version:
        raise PolicyReservationError("policy_version must be non-empty")
    if not isinstance(policy_snapshot_digest, TypedId):
        raise PolicyReservationError("policy_snapshot_digest must be TypedId")
    if not isinstance(key_version, str) or not key_version:
        raise PolicyReservationError("key_version must be non-empty")
    if not isinstance(owner_principal, str) or not owner_principal:
        raise PolicyReservationError("owner_principal must be non-empty")
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            {
                "domain": "aigear.policy-decision-request.v2",
                "environment_id": environment_id,
                "environment_fingerprint": environment_fingerprint.typed,
                "subject_asset_version_id": (
                    closure.subject_asset_version_id.typed
                ),
                "decision": decision.value,
                "policy_version": policy_version,
                "policy_snapshot_digest": policy_snapshot_digest.typed,
                "evidence_filter_digest": closure.filter_digest.typed,
                "evidence_closure_digest": closure.closure_digest.typed,
                "key_version": key_version,
                "owner_principal": owner_principal,
                "validity_seconds": validity_seconds,
                "not_before_delay_seconds": not_before_delay_seconds,
                "max_evidence_age_seconds": max_evidence_age_seconds,
            }
        )
    )


def _validate_inputs(
    *,
    environment_id: str,
    environment_fingerprint: TypedId,
    decision: PolicyDecision,
    closure: PolicyEvidenceClosure,
    policy_version: str,
    policy_snapshot_digest: TypedId,
    key_version: str,
    owner_principal: str,
    validity_seconds: int,
    not_before_delay_seconds: int,
    max_evidence_age_seconds: int,
    lease_ttl_seconds: int,
) -> None:
    if closure.environment_fingerprint != environment_fingerprint:
        raise PolicyReservationError("closure belongs to another environment")
    if decision is PolicyDecision.APPROVED and not closure.approvable:
        raise PolicyReservationError("approval requires an approvable evidence closure")
    _positive_bounded(
        "validity_seconds", validity_seconds, _MAX_VALIDITY_SECONDS
    )
    if (
        isinstance(not_before_delay_seconds, bool)
        or not isinstance(not_before_delay_seconds, int)
        or not 0
        <= not_before_delay_seconds
        <= _MAX_NOT_BEFORE_DELAY_SECONDS
    ):
        raise PolicyReservationError(
            "not_before_delay_seconds is outside the allowed range"
        )
    if (
        decision is PolicyDecision.REVOKED
        and not_before_delay_seconds != 0
    ):
        raise PolicyReservationError("revocation must take effect immediately")
    _positive_bounded(
        "max_evidence_age_seconds",
        max_evidence_age_seconds,
        _MAX_VALIDITY_SECONDS,
    )
    _positive_bounded(
        "lease_ttl_seconds", lease_ttl_seconds, _MAX_LEASE_TTL_SECONDS
    )
    compute_policy_request_fingerprint(
        environment_id=environment_id,
        environment_fingerprint=environment_fingerprint,
        decision=decision,
        closure=closure,
        policy_version=policy_version,
        policy_snapshot_digest=policy_snapshot_digest,
        key_version=key_version,
        owner_principal=owner_principal,
        validity_seconds=validity_seconds,
        not_before_delay_seconds=not_before_delay_seconds,
        max_evidence_age_seconds=max_evidence_age_seconds,
    )


def _evidence_digests(closure: PolicyEvidenceClosure) -> tuple[TypedId, ...]:
    values = {
        closure.filter_digest,
        closure.closure_digest,
        *(node.evidence_digest for node in closure.nodes),
    }
    return tuple(sorted(values, key=lambda value: value.typed.encode("utf-8")))


def _operation_id(
    environment_fingerprint: TypedId, idempotency_key_hash: str
) -> str:
    digest = digest_sha256_of_jcs(
        [
            "aigear.policy-decision-operation.v2",
            environment_fingerprint.typed,
            idempotency_key_hash,
        ]
    )
    return f"policy-{digest}"


def reserve_policy_decision(
    registry,
    *,
    environment_id: str,
    environment_fingerprint: TypedId,
    decision: PolicyDecision,
    closure: PolicyEvidenceClosure,
    policy_version: str,
    policy_snapshot_digest: TypedId,
    key_version: str,
    idempotency_key: str,
    owner_principal: str,
    validity_seconds: int,
    not_before_delay_seconds: int = 0,
    max_evidence_age_seconds: int = 300,
    lease_ttl_seconds: int = 300,
) -> PolicyDecisionOperationRecord:
    """Reserve one head epoch using the Registry's server-issued read time."""

    _validate_inputs(
        environment_id=environment_id,
        environment_fingerprint=environment_fingerprint,
        decision=decision,
        closure=closure,
        policy_version=policy_version,
        policy_snapshot_digest=policy_snapshot_digest,
        key_version=key_version,
        owner_principal=owner_principal,
        validity_seconds=validity_seconds,
        not_before_delay_seconds=not_before_delay_seconds,
        max_evidence_age_seconds=max_evidence_age_seconds,
        lease_ttl_seconds=lease_ttl_seconds,
    )
    key_hash = compute_policy_idempotency_key_hash(idempotency_key)
    request_fingerprint = compute_policy_request_fingerprint(
        environment_id=environment_id,
        environment_fingerprint=environment_fingerprint,
        decision=decision,
        closure=closure,
        policy_version=policy_version,
        policy_snapshot_digest=policy_snapshot_digest,
        key_version=key_version,
        owner_principal=owner_principal,
        validity_seconds=validity_seconds,
        not_before_delay_seconds=not_before_delay_seconds,
        max_evidence_age_seconds=max_evidence_age_seconds,
    )
    operation_id = _operation_id(environment_fingerprint, key_hash)
    try:
        closure_read_time = datetime.fromisoformat(closure.read_time)
    except (TypeError, ValueError) as exc:
        raise PolicyReservationError(
            "closure read_time must be an ISO timestamp"
        ) from exc
    closure_read_time = _aware_utc("closure read_time", closure_read_time)

    def reserve(tx):
        existing_operation = tx.get_policy_decision_operation(key_hash)
        if existing_operation is not None:
            if existing_operation.request_fingerprint != request_fingerprint:
                raise PolicyReservationConflict(
                    "idempotency key is bound to another policy request"
                )
            return existing_operation

        head = tx.get_policy_decision_head(closure.subject_asset_version_id)
        if head is not None and (
            not isinstance(head, PolicyDecisionHead)
            or head.environment_fingerprint != environment_fingerprint
            or head.subject_asset_version_id
            != closure.subject_asset_version_id
        ):
            raise PolicyReservationConflict("policy head identity mismatch")
        server_time = _aware_utc(
            "Registry server read time", tx.get_server_read_time()
        )
        if closure_read_time > server_time:
            raise PolicyReservationError(
                "evidence closure read time is later than Registry server time"
            )
        if (
            server_time - closure_read_time
        ).total_seconds() > max_evidence_age_seconds:
            raise PolicyReservationError("evidence closure is too old")

        expected_head_revision = 0 if head is None else head.revision
        decision_epoch = 1 if head is None else head.current_epoch + 1
        current_lock = tx.get_policy_decision_reservation(
            closure.subject_asset_version_id
        )
        fencing_token = 1
        lock_revision = 1
        if current_lock is not None:
            same_head_slot = (
                current_lock.expected_head_revision == expected_head_revision
                and current_lock.decision_epoch == decision_epoch
            )
            lock_expiry = datetime.fromisoformat(
                current_lock.lease_expires_at
            ).astimezone(timezone.utc)
            if same_head_slot and lock_expiry > server_time:
                raise PolicyReservationBusy(
                    "subject already has a live policy decision reservation"
                )
            previous = tx.get_policy_decision_operation(
                current_lock.idempotency_key_hash
            )
            if previous is not None and previous.phase in {
                PolicyDecisionOperationPhase.VERIFIED,
                PolicyDecisionOperationPhase.JOURNALING,
                PolicyDecisionOperationPhase.COMMITTING,
                PolicyDecisionOperationPhase.RECONCILING,
            }:
                raise PolicyReservationBusy(
                    "existing policy decision requires reconciliation"
                )
            if previous is not None and previous.phase in {
                PolicyDecisionOperationPhase.RESERVED,
                PolicyDecisionOperationPhase.ATTESTING,
            }:
                tx.put_policy_decision_operation(
                    replace(
                        previous,
                        phase=PolicyDecisionOperationPhase.CANCELLED,
                        revision=previous.revision + 1,
                        lease_expires_at=None,
                        updated_at=server_time.isoformat(),
                        finished_at=server_time.isoformat(),
                        error_class="ReservationSuperseded",
                        error_summary=(
                            "expired or stale subject reservation was fenced"
                        ),
                    )
                )
            fencing_token = current_lock.fencing_token + 1
            lock_revision = current_lock.revision + 1

        lease_expires_at = (
            server_time + timedelta(seconds=lease_ttl_seconds)
        ).isoformat()
        not_before = (
            server_time + timedelta(seconds=not_before_delay_seconds)
        )
        valid_until = not_before + timedelta(seconds=validity_seconds)
        envelope = PolicyDecisionUnsignedEnvelope(
            schema_version="2.0",
            operation_id=operation_id,
            fencing_token=fencing_token,
            environment_id=environment_id,
            environment_fingerprint=environment_fingerprint,
            subject_asset_version_id=closure.subject_asset_version_id,
            decision=decision,
            decision_epoch=decision_epoch,
            policy_version=policy_version,
            policy_snapshot_digest=policy_snapshot_digest,
            evidence_digests=_evidence_digests(closure),
            evidence_closure_digest=closure.closure_digest,
            firestore_read_time=closure.read_time,
            issued_at=server_time.isoformat(),
            not_before=not_before.isoformat(),
            valid_until=valid_until.isoformat(),
            key_version=key_version,
        )
        request = PolicyDecisionRequest(
            schema_version="2.0",
            operation_id=operation_id,
            request_fingerprint=request_fingerprint,
            expected_head_revision=expected_head_revision,
            unsigned_envelope=envelope,
            unsigned_envelope_digest=envelope.digest,
        )
        operation = PolicyDecisionOperationRecord(
            schema_version="2.0",
            operation_id=operation_id,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            request=request,
            policy_snapshot_digest=policy_snapshot_digest,
            evidence_filter_digest=closure.filter_digest,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            phase=PolicyDecisionOperationPhase.RESERVED,
            revision=1,
            lease_expires_at=lease_expires_at,
            created_at=server_time.isoformat(),
            updated_at=server_time.isoformat(),
        )
        lock = PolicyDecisionReservationLock(
            schema_version="2.0",
            environment_fingerprint=environment_fingerprint,
            subject_asset_version_id=closure.subject_asset_version_id,
            expected_head_revision=expected_head_revision,
            decision_epoch=decision_epoch,
            operation_id=operation_id,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            fencing_token=fencing_token,
            revision=lock_revision,
            lease_expires_at=lease_expires_at,
            updated_at=server_time.isoformat(),
        )
        tx.put_policy_decision_operation(operation)
        tx.put_policy_decision_reservation(lock)
        return operation

    runner = getattr(registry, "run_atomic", None)
    if not callable(runner):
        raise PolicyReservationError(
            "Registry lacks the required atomic transaction boundary"
        )
    return runner(reserve)

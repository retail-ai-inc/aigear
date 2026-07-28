"""Authenticate and verify policy-attestor completion messages."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable, Optional

from aigear.management.v2.attestation import (
    AttestationRecord,
    same_attestation_identity,
    verify_attestation,
)
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_attestor import (
    PolicyAttestorCompletion,
    PolicySigningTrust,
)
from aigear.management.v2.pubsub_auth import verify_pubsub_oidc_token
from aigear.management.v2.records.policy import (
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    PolicyDecisionReservationLock,
    PolicyDecisionVerificationRecord,
)

__all__ = [
    "PolicyCompletionError",
    "PolicyCompletionConflict",
    "PolicyCompletionEnvelope",
    "AuthenticatedPolicyCompletion",
    "authenticate_policy_completion",
    "mark_policy_completion_verified",
]


class PolicyCompletionError(ValueError):
    pass


class PolicyCompletionConflict(PolicyCompletionError):
    pass


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise PolicyCompletionError(f"{field_name} must be a non-empty str")


def _parse_time(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise PolicyCompletionError(
            f"{field_name} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyCompletionError(
            f"{field_name} must be timezone-aware"
        )
    return parsed


@dataclass(frozen=True)
class PolicyCompletionEnvelope:
    completion: PolicyAttestorCompletion
    message_id: str
    audience: str
    publisher_principal: str
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.completion, PolicyAttestorCompletion):
            raise PolicyCompletionError(
                "completion must be PolicyAttestorCompletion"
            )
        for field_name in (
            "message_id",
            "audience",
            "publisher_principal",
        ):
            _non_empty(field_name, getattr(self, field_name))
        issued = _parse_time("issued_at", self.issued_at)
        expires = _parse_time("expires_at", self.expires_at)
        if expires <= issued:
            raise PolicyCompletionError(
                "expires_at must be later than issued_at"
            )


@dataclass(frozen=True)
class AuthenticatedPolicyCompletion:
    verification: PolicyDecisionVerificationRecord
    attestation: AttestationRecord
    reused_existing_attestation: bool

    def __post_init__(self) -> None:
        if not isinstance(
            self.verification, PolicyDecisionVerificationRecord
        ):
            raise PolicyCompletionError(
                "verification must be PolicyDecisionVerificationRecord"
            )
        if not isinstance(self.attestation, AttestationRecord):
            raise PolicyCompletionError(
                "attestation must be AttestationRecord"
            )
        if self.verification.attestation_id != self.attestation.attestation_id:
            raise PolicyCompletionError(
                "verification and attestation identity mismatch"
            )
        if not isinstance(self.reused_existing_attestation, bool):
            raise PolicyCompletionError(
                "reused_existing_attestation must be bool"
            )


TokenVerifier = Callable[..., str]


def _validate_current_fence(
    operation: PolicyDecisionOperationRecord,
    lock: PolicyDecisionReservationLock,
) -> None:
    if not isinstance(lock, PolicyDecisionReservationLock):
        raise PolicyCompletionError(
            "current_lock must be PolicyDecisionReservationLock"
        )
    envelope = operation.request.unsigned_envelope
    if (
        lock.environment_fingerprint != envelope.environment_fingerprint
        or lock.subject_asset_version_id
        != envelope.subject_asset_version_id
        or lock.expected_head_revision
        != operation.request.expected_head_revision
        or lock.decision_epoch != envelope.decision_epoch
        or lock.operation_id != operation.operation_id
        or lock.idempotency_key_hash != operation.idempotency_key_hash
        or lock.request_fingerprint != operation.request_fingerprint
        or lock.fencing_token != operation.fencing_token
        or operation.lease_expires_at != lock.lease_expires_at
    ):
        raise PolicyCompletionConflict(
            "completion operation no longer owns the current reservation fence"
        )


def _verify_prewarmed_signature(
    attestation: AttestationRecord,
    *,
    algorithm: str,
    verifier,
    signing_trust: PolicySigningTrust,
) -> None:
    if not isinstance(signing_trust, PolicySigningTrust):
        raise PolicyCompletionError(
            "signing_trust must be PolicySigningTrust"
        )
    try:
        signing_trust.authorize(
            key_version=attestation.key_version,
            algorithm=algorithm,
        )
    except Exception as exc:
        raise PolicyCompletionError(
            f"policy signing trust rejected completion: {exc}"
        ) from exc
    if getattr(verifier, "is_warm", False) is not True:
        raise PolicyCompletionError(
            "policy public-key verifier must be prewarmed"
        )
    algorithm_for = getattr(verifier, "algorithm_for", None)
    if not callable(algorithm_for):
        raise PolicyCompletionError(
            "policy verifier cannot report the prewarmed key algorithm"
        )
    try:
        actual_algorithm = algorithm_for(attestation.key_version)
    except Exception as exc:
        raise PolicyCompletionError(
            "prewarmed policy key is unavailable"
        ) from exc
    if actual_algorithm != algorithm:
        raise PolicyCompletionError(
            "completion algorithm does not match prewarmed policy key"
        )
    try:
        verify_attestation(attestation, verifier)
    except Exception as exc:
        raise PolicyCompletionError(
            "policy decision signature verification failed"
        ) from exc


def authenticate_policy_completion(
    envelope: PolicyCompletionEnvelope,
    *,
    operation: PolicyDecisionOperationRecord,
    current_lock: PolicyDecisionReservationLock,
    registry,
    oidc_token: str,
    expected_audience: str,
    allowed_publishers: Iterable[str],
    expected_environment_id: str,
    expected_environment_fingerprint: TypedId,
    signing_trust: PolicySigningTrust,
    verifier,
    at: str,
    oidc_request: Optional[object] = None,
    token_verifier: TokenVerifier = verify_pubsub_oidc_token,
) -> AuthenticatedPolicyCompletion:
    """Authenticate outside transactions and return immutable verified facts."""

    if not isinstance(envelope, PolicyCompletionEnvelope):
        raise PolicyCompletionError(
            "envelope must be PolicyCompletionEnvelope"
        )
    if not isinstance(operation, PolicyDecisionOperationRecord):
        raise PolicyCompletionError(
            "operation must be PolicyDecisionOperationRecord"
        )
    if operation.phase not in {
        PolicyDecisionOperationPhase.ATTESTING,
        PolicyDecisionOperationPhase.VERIFIED,
    }:
        raise PolicyCompletionError(
            "completion requires an attesting or verified policy operation"
        )
    _validate_current_fence(operation, current_lock)
    if envelope.audience != expected_audience:
        raise PolicyCompletionError("completion audience mismatch")
    allowed = tuple(allowed_publishers)
    if not allowed:
        raise PolicyCompletionError(
            "allowed_publishers must not be empty"
        )
    try:
        principal = token_verifier(
            oidc_token,
            audience=expected_audience,
            allowed_service_accounts=allowed,
            request=oidc_request,
        )
    except Exception as exc:
        raise PolicyCompletionError(
            "completion OIDC authentication failed"
        ) from exc
    if (
        principal != envelope.publisher_principal
        or principal not in allowed
    ):
        raise PolicyCompletionError(
            "completion publisher principal mismatch"
        )

    instant = _parse_time("at", at)
    message_issued = _parse_time("issued_at", envelope.issued_at)
    message_expires = _parse_time("expires_at", envelope.expires_at)
    decision = operation.request.unsigned_envelope
    decision_issued = _parse_time("decision.issued_at", decision.issued_at)
    decision_expires = _parse_time(
        "decision.valid_until", decision.valid_until
    )
    lease_expires = _parse_time(
        "reservation.lease_expires_at",
        current_lock.lease_expires_at,
    )
    if not (
        decision_issued <= message_issued < message_expires <= decision_expires
    ):
        raise PolicyCompletionError(
            "completion message is outside the reserved decision window"
        )
    if message_expires > lease_expires or instant >= lease_expires:
        raise PolicyCompletionConflict(
            "completion is outside the current reservation lease"
        )
    if instant < message_issued or instant >= message_expires:
        raise PolicyCompletionError(
            "completion message is not currently valid"
        )
    if instant >= decision_expires:
        raise PolicyCompletionError(
            "reserved policy decision is expired"
        )

    completion = envelope.completion
    signed = completion.signed_decision
    if (
        completion.operation_id != operation.operation_id
        or completion.request_fingerprint != operation.request_fingerprint
        or completion.fencing_token != operation.fencing_token
        or signed.unsigned_envelope != decision
        or signed.unsigned_envelope_digest
        != operation.request.unsigned_envelope_digest
        or signed.attestation_id
        != operation.request.unsigned_envelope_digest
        or signed.key_version != decision.key_version
        or decision.environment_id != expected_environment_id
        or decision.environment_fingerprint
        != expected_environment_fingerprint
    ):
        raise PolicyCompletionConflict(
            "completion is not bound to the current policy operation"
        )

    candidate = signed.to_attestation_record()
    _verify_prewarmed_signature(
        candidate,
        algorithm=signed.algorithm,
        verifier=verifier,
        signing_trust=signing_trust,
    )

    getter = getattr(registry, "get_attestation", None)
    if not callable(getter):
        raise PolicyCompletionError(
            "Registry lacks exact attestation lookup"
        )
    existing = getter(candidate.attestation_id)
    reused = existing is not None
    if existing is not None:
        if (
            not isinstance(existing, AttestationRecord)
            or not same_attestation_identity(existing, candidate)
        ):
            raise PolicyCompletionConflict(
                "attestation ID is already bound to different evidence"
            )
        _verify_prewarmed_signature(
            existing,
            algorithm=signed.algorithm,
            verifier=verifier,
            signing_trust=signing_trust,
        )
        candidate = existing

    verification_values = {
        "domain": "aigear.policy-completion-verification.v2",
        "schema_version": operation.schema_version,
        "operation_id": operation.operation_id,
        "request_fingerprint": operation.request_fingerprint.typed,
        "fencing_token": operation.fencing_token,
        "attestation_id": candidate.attestation_id.typed,
        "message_id": envelope.message_id,
        "publisher_principal": principal,
        "verified_at": instant.isoformat(),
    }
    verification = PolicyDecisionVerificationRecord(
        schema_version=operation.schema_version,
        operation_id=operation.operation_id,
        request_fingerprint=operation.request_fingerprint,
        fencing_token=operation.fencing_token,
        attestation_id=candidate.attestation_id,
        message_id=envelope.message_id,
        publisher_principal=principal,
        verified_at=instant.isoformat(),
        verification_digest=TypedId.from_bare(
            digest_sha256_of_jcs(verification_values)
        ),
    )
    if operation.verified_completion is not None:
        existing_verification = operation.verified_completion
        if (
            existing_verification.operation_id != operation.operation_id
            or existing_verification.request_fingerprint
            != operation.request_fingerprint
            or existing_verification.fencing_token != operation.fencing_token
            or existing_verification.attestation_id
            != candidate.attestation_id
            or existing_verification.message_id != envelope.message_id
            or existing_verification.publisher_principal != principal
        ):
            raise PolicyCompletionConflict(
                "verified operation is bound to another completion"
            )
        verification = existing_verification
    return AuthenticatedPolicyCompletion(
        verification=verification,
        attestation=candidate,
        reused_existing_attestation=reused,
    )


def mark_policy_completion_verified(
    operation: PolicyDecisionOperationRecord,
    authenticated: AuthenticatedPolicyCompletion,
) -> PolicyDecisionOperationRecord:
    if not isinstance(operation, PolicyDecisionOperationRecord):
        raise PolicyCompletionError(
            "operation must be PolicyDecisionOperationRecord"
        )
    if operation.phase is PolicyDecisionOperationPhase.VERIFIED:
        if operation.verified_completion == authenticated.verification:
            return operation
        raise PolicyCompletionConflict(
            "verified operation is bound to another completion"
        )
    if operation.phase is not PolicyDecisionOperationPhase.ATTESTING:
        raise PolicyCompletionError(
            "only an attesting operation can become verified"
        )
    if not isinstance(authenticated, AuthenticatedPolicyCompletion):
        raise PolicyCompletionError(
            "authenticated must be AuthenticatedPolicyCompletion"
        )
    verification = authenticated.verification
    if (
        verification.operation_id != operation.operation_id
        or verification.request_fingerprint != operation.request_fingerprint
        or verification.fencing_token != operation.fencing_token
        or verification.attestation_id
        != operation.request.unsigned_envelope_digest
    ):
        raise PolicyCompletionConflict(
            "verified completion is not bound to operation"
        )
    return replace(
        operation,
        phase=PolicyDecisionOperationPhase.VERIFIED,
        revision=operation.revision + 1,
        updated_at=verification.verified_at,
        verified_completion=verification,
    )

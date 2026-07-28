from __future__ import annotations

import base64
from dataclasses import replace

import pytest

from aigear.management.v2.attestation import (
    HmacTestSigner,
    HmacTestVerifier,
)
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_attestor import (
    PolicyAttestorCompletion,
    PolicySigningTrust,
    SignedPolicyDecision,
)
from aigear.management.v2.policy_completion import (
    PolicyCompletionConflict,
    PolicyCompletionEnvelope,
    PolicyCompletionError,
    authenticate_policy_completion,
    mark_policy_completion_verified,
)
from aigear.management.v2.record_codec import decode_record, encode_record
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    PolicyDecisionRequest,
    PolicyDecisionReservationLock,
    PolicyDecisionUnsignedEnvelope,
)

_FP = TypedId.from_bare("aa" * 32)
_SUBJECT = TypedId.from_bare("bb" * 32)
_REQUEST = TypedId.from_bare("cc" * 32)
_POLICY = TypedId.from_bare("dd" * 32)
_FILTER = TypedId.from_bare("01" * 32)
_CLOSURE = TypedId.from_bare("02" * 32)
_NODE = TypedId.from_bare("03" * 32)
_KEY_RESOURCE = "projects/p/locations/l/keyRings/r/cryptoKeys/policy"
_KEY = f"{_KEY_RESOURCE}/cryptoKeyVersions/1"
_ALGORITHM = "RSA_SIGN_PSS_2048_SHA256"
_PUBLISHER = "policy-attestor@example.test"


class _PrewarmedVerifier:
    is_warm = True

    def __init__(
        self,
        *,
        key_version: str = _KEY,
        algorithm: str = _ALGORITHM,
    ) -> None:
        self.key_version = key_version
        self.algorithm = algorithm
        self.delegate = HmacTestVerifier(
            b"policy-secret",
            key_version=key_version,
        )

    def algorithm_for(self, key_version: str) -> str:
        if key_version != self.key_version:
            raise ValueError("unknown key")
        return self.algorithm

    def verify_sha256_digest(
        self,
        *,
        key_version: str,
        digest: bytes,
        signature: bytes,
    ) -> None:
        self.delegate.verify_sha256_digest(
            key_version=key_version,
            digest=digest,
            signature=signature,
        )


def _trust(
    *,
    key_resource: str = _KEY_RESOURCE,
    key_version: str = _KEY,
    algorithm: str = _ALGORITHM,
) -> PolicySigningTrust:
    return PolicySigningTrust(
        allowed_key_resources=(key_resource,),
        allowed_key_versions=(key_version,),
        allowed_algorithms=(algorithm,),
    )


def _setup(*, key_version: str = _KEY):
    envelope = PolicyDecisionUnsignedEnvelope(
        schema_version="2.0",
        operation_id="policy-1",
        fencing_token=3,
        environment_id="production",
        environment_fingerprint=_FP,
        subject_asset_version_id=_SUBJECT,
        decision=PolicyDecision.APPROVED,
        decision_epoch=4,
        policy_version="policy-2026-07",
        policy_snapshot_digest=_POLICY,
        evidence_digests=(_FILTER, _CLOSURE, _NODE),
        evidence_closure_digest=_CLOSURE,
        firestore_read_time="2026-07-28T00:00:00+00:00",
        issued_at="2026-07-28T00:00:01+00:00",
        not_before="2026-07-28T00:00:01+00:00",
        valid_until="2026-07-28T01:00:01+00:00",
        key_version=key_version,
    )
    request = PolicyDecisionRequest(
        schema_version="2.0",
        operation_id="policy-1",
        request_fingerprint=_REQUEST,
        expected_head_revision=7,
        unsigned_envelope=envelope,
        unsigned_envelope_digest=envelope.digest,
    )
    operation = PolicyDecisionOperationRecord(
        schema_version="2.0",
        operation_id="policy-1",
        idempotency_key_hash="ab" * 32,
        request_fingerprint=_REQUEST,
        request=request,
        policy_snapshot_digest=_POLICY,
        evidence_filter_digest=_FILTER,
        owner_principal="controller@example.test",
        fencing_token=3,
        phase=PolicyDecisionOperationPhase.ATTESTING,
        revision=2,
        lease_expires_at="2026-07-28T00:10:01+00:00",
        created_at="2026-07-28T00:00:01+00:00",
        updated_at="2026-07-28T00:00:02+00:00",
    )
    lock = PolicyDecisionReservationLock(
        schema_version="2.0",
        environment_fingerprint=_FP,
        subject_asset_version_id=_SUBJECT,
        expected_head_revision=7,
        decision_epoch=4,
        operation_id="policy-1",
        idempotency_key_hash="ab" * 32,
        request_fingerprint=_REQUEST,
        fencing_token=3,
        revision=1,
        lease_expires_at="2026-07-28T00:10:01+00:00",
        updated_at="2026-07-28T00:00:01+00:00",
    )
    signer = HmacTestSigner(
        b"policy-secret",
        key_version=key_version,
    )
    signature = signer.sign_sha256_digest(
        bytes.fromhex(envelope.digest.bare)
    )
    signed = SignedPolicyDecision(
        unsigned_envelope=envelope,
        unsigned_envelope_digest=envelope.digest,
        attestation_id=envelope.digest,
        key_version=key_version,
        algorithm=_ALGORITHM,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )
    completion = PolicyAttestorCompletion(
        operation_id=operation.operation_id,
        request_fingerprint=operation.request_fingerprint,
        fencing_token=operation.fencing_token,
        signed_decision=signed,
    )
    message = PolicyCompletionEnvelope(
        completion=completion,
        message_id="message-1",
        audience="aigear-policy-completion",
        publisher_principal=_PUBLISHER,
        issued_at="2026-07-28T00:00:03+00:00",
        expires_at="2026-07-28T00:05:03+00:00",
    )
    return operation, lock, message


def _authenticate(
    operation,
    lock,
    message,
    *,
    registry=None,
    verifier=None,
    trust=None,
    token_verifier=None,
    **overrides,
):
    values = {
        "operation": operation,
        "current_lock": lock,
        "registry": registry or FakeRegistryV2(),
        "oidc_token": "token",
        "expected_audience": "aigear-policy-completion",
        "allowed_publishers": (_PUBLISHER,),
        "expected_environment_id": "production",
        "expected_environment_fingerprint": _FP,
        "signing_trust": trust or _trust(),
        "verifier": verifier or _PrewarmedVerifier(),
        "at": "2026-07-28T00:01:00+00:00",
        "token_verifier": token_verifier
        or (lambda token, **kwargs: _PUBLISHER),
    }
    values.update(overrides)
    return authenticate_policy_completion(message, **values)


def test_authenticated_completion_verifies_and_advances_operation():
    operation, lock, message = _setup()
    registry = FakeRegistryV2()
    registry.put_policy_decision_operation(operation)

    authenticated = _authenticate(
        operation,
        lock,
        message,
        registry=registry,
    )
    verified = mark_policy_completion_verified(
        operation,
        authenticated,
    )
    registry.put_policy_decision_operation(verified)

    assert authenticated.attestation.attestation_id == (
        operation.request.unsigned_envelope_digest
    )
    assert authenticated.reused_existing_attestation is False
    assert authenticated.verification.publisher_principal == _PUBLISHER
    assert verified.phase is PolicyDecisionOperationPhase.VERIFIED
    assert verified.revision == operation.revision + 1
    assert verified.verified_completion == authenticated.verification
    assert registry.get_policy_decision_operation(
        operation.idempotency_key_hash
    ) == verified
    assert decode_record(type(verified), encode_record(verified)) == verified


def test_existing_same_attestation_is_verified_and_reused():
    operation, lock, message = _setup()
    existing = message.completion.signed_decision.to_attestation_record()
    registry = FakeRegistryV2()
    registry.put_attestation(existing)

    authenticated = _authenticate(
        operation,
        lock,
        message,
        registry=registry,
    )

    assert authenticated.attestation is existing
    assert authenticated.reused_existing_attestation is True


def test_verified_operation_message_retry_is_idempotent():
    operation, lock, message = _setup()
    first = _authenticate(operation, lock, message)
    verified = mark_policy_completion_verified(operation, first)

    replay = _authenticate(
        verified,
        lock,
        message,
        at="2026-07-28T00:02:00+00:00",
    )

    assert replay.verification == first.verification
    assert mark_policy_completion_verified(verified, replay) is verified


@pytest.mark.parametrize(
    "change,error",
    [
        (
            lambda operation, lock, message: (
                operation,
                replace(lock, fencing_token=4, revision=2),
                message,
            ),
            "fence",
        ),
        (
            lambda operation, lock, message: (
                operation,
                lock,
                replace(message, audience="wrong"),
            ),
            "audience",
        ),
        (
            lambda operation, lock, message: (
                operation,
                lock,
                replace(message, publisher_principal="attacker@example.test"),
            ),
            "publisher",
        ),
        (
            lambda operation, lock, message: (
                operation,
                lock,
                replace(
                    message,
                    completion=replace(
                        message.completion,
                        request_fingerprint=TypedId.from_bare("ee" * 32),
                    ),
                ),
            ),
            "current policy operation",
        ),
    ],
)
def test_context_and_fence_tampering_is_rejected(change, error):
    operation, lock, message = change(*_setup())

    with pytest.raises(PolicyCompletionError, match=error):
        _authenticate(operation, lock, message)


def test_verified_oidc_principal_must_match_message_and_allowlist():
    operation, lock, message = _setup()

    with pytest.raises(PolicyCompletionError, match="publisher"):
        _authenticate(
            operation,
            lock,
            message,
            token_verifier=lambda token, **kwargs: "attacker@example.test",
        )


def test_signature_and_algorithm_verification_fail_closed():
    operation, lock, message = _setup()
    tampered = replace(
        message,
        completion=replace(
            message.completion,
            signed_decision=replace(
                message.completion.signed_decision,
                signature_b64=base64.b64encode(b"tampered").decode("ascii"),
            ),
        ),
    )
    with pytest.raises(PolicyCompletionError, match="signature"):
        _authenticate(operation, lock, tampered)

    with pytest.raises(PolicyCompletionError, match="algorithm"):
        _authenticate(
            operation,
            lock,
            message,
            verifier=_PrewarmedVerifier(
                algorithm="RSA_SIGN_PKCS1_2048_SHA256"
            ),
        )


def test_manifest_key_and_unwarmed_verifier_are_rejected():
    manifest_resource = (
        "projects/p/locations/l/keyRings/r/cryptoKeys/manifest"
    )
    manifest_key = f"{manifest_resource}/cryptoKeyVersions/1"
    operation, lock, message = _setup(key_version=manifest_key)

    with pytest.raises(PolicyCompletionError, match="resource"):
        _authenticate(operation, lock, message)

    operation, lock, message = _setup()
    verifier = _PrewarmedVerifier()
    verifier.is_warm = False
    with pytest.raises(PolicyCompletionError, match="prewarmed"):
        _authenticate(
            operation,
            lock,
            message,
            verifier=verifier,
        )


@pytest.mark.parametrize(
    "at,error",
    [
        ("2026-07-28T00:00:02+00:00", "currently valid"),
        ("2026-07-28T00:05:03+00:00", "currently valid"),
        ("2026-07-28T01:00:01+00:00", "lease"),
    ],
)
def test_completion_validity_window_is_closed(at, error):
    operation, lock, message = _setup()
    with pytest.raises(PolicyCompletionError, match=error):
        _authenticate(operation, lock, message, at=at)


def test_expired_reservation_lease_rejects_completion():
    operation, lock, message = _setup()
    expired_lock = replace(
        lock,
        lease_expires_at="2026-07-28T00:00:30+00:00",
    )
    expired_operation = replace(
        operation,
        lease_expires_at=expired_lock.lease_expires_at,
    )

    with pytest.raises(PolicyCompletionConflict, match="lease"):
        _authenticate(
            expired_operation,
            expired_lock,
            message,
        )


def test_registry_returning_another_attestation_identity_is_conflict():
    operation, lock, message = _setup()
    _, _, other_message = _setup(
        key_version=(
            "projects/p/locations/l/keyRings/r/cryptoKeys/"
            "other/cryptoKeyVersions/1"
        )
    )
    other = other_message.completion.signed_decision.to_attestation_record()

    class Registry:
        def get_attestation(self, attestation_id):
            return other

    with pytest.raises(PolicyCompletionConflict, match="different evidence"):
        _authenticate(
            operation,
            lock,
            message,
            registry=Registry(),
        )

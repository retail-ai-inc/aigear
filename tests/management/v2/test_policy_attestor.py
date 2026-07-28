from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import aigear.management.v2.policy_attestor as policy_attestor_module
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_attestor import (
    CloudKmsPolicyDecisionSigner,
    PolicyApprovalSnapshot,
    PolicyAttestorError,
    PolicySigningTrust,
    SignedPolicyDecision,
    sign_policy_decision,
)
from aigear.management.v2.policy_evidence import (
    EvidenceNodeKind,
    PolicyEvidenceClosure,
    PolicyEvidenceNode,
    PolicyEvidenceRequest,
    _closure_digest,
    compute_policy_evidence_closure,
)
from aigear.management.v2.policy_reservation import reserve_policy_decision
from aigear.management.v2.record_codec import decode_record, encode_record
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionOperationPhase,
)

_READ_TIME = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
_SERVER_TIME = _READ_TIME + timedelta(seconds=1)
_FP = TypedId.from_bare("aa" * 32)
_SUBJECT = TypedId.from_bare("bb" * 32)
_PRODUCER = TypedId.from_bare("cc" * 32)
_POLICY_KEY_RESOURCE = (
    "projects/p/locations/l/keyRings/r/cryptoKeys/policy-approval"
)
_POLICY_KEY = f"{_POLICY_KEY_RESOURCE}/cryptoKeyVersions/1"
_ALGORITHM = "RSA_SIGN_PSS_2048_SHA256"


class _Signer:
    def __init__(
        self,
        *,
        key_version: str = _POLICY_KEY,
        algorithm: str = _ALGORITHM,
        signature: bytes = b"policy-signature",
    ) -> None:
        self.key_version = key_version
        self.algorithm = algorithm
        self.signature = signature
        self.calls: list[bytes] = []

    def sign_sha256_digest(self, digest: bytes) -> bytes:
        self.calls.append(digest)
        return self.signature


def _snapshot(**overrides) -> PolicyApprovalSnapshot:
    values = {
        "schema_version": "2.0",
        "environment_id": "production",
        "environment_fingerprint": _FP,
        "policy_version": "policy-2026-07",
        "allowed_producer_identities": (_PRODUCER,),
        "allowed_scanners": (("scanner", "1"),),
        "max_depth": 8,
        "max_nodes": 100,
        "page_size": 20,
    }
    values.update(overrides)
    return PolicyApprovalSnapshot(**values)


def _trust(**overrides) -> PolicySigningTrust:
    values = {
        "allowed_key_resources": (_POLICY_KEY_RESOURCE,),
        "allowed_key_versions": (_POLICY_KEY,),
        "allowed_algorithms": (_ALGORITHM,),
    }
    values.update(overrides)
    return PolicySigningTrust(**values)


def _closure(
    snapshot: PolicyApprovalSnapshot,
    *,
    approvable: bool = True,
    read_time: datetime = _READ_TIME,
) -> PolicyEvidenceClosure:
    request = PolicyEvidenceRequest(
        subject_asset_version_id=_SUBJECT,
        environment_fingerprint=_FP,
        read_time=read_time,
        allowed_producer_identities=snapshot.allowed_producer_identities,
        allowed_scanners=snapshot.allowed_scanners,
        max_depth=snapshot.max_depth,
        max_nodes=snapshot.max_nodes,
        page_size=snapshot.page_size,
    )
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
        "filter_digest": request.filter_digest,
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


def _operation(
    snapshot: PolicyApprovalSnapshot,
    closure: PolicyEvidenceClosure,
):
    return reserve_policy_decision(
        FakeRegistryV2(server_read_time=_SERVER_TIME),
        environment_id="production",
        environment_fingerprint=_FP,
        decision=PolicyDecision.APPROVED,
        closure=closure,
        policy_version=snapshot.policy_version,
        policy_snapshot_digest=snapshot.digest,
        key_version=_POLICY_KEY,
        idempotency_key="policy-request-1",
        owner_principal="controller@example.test",
        validity_seconds=3600,
    )


def test_policy_snapshot_digest_has_stable_cross_language_vector():
    assert _snapshot().digest.typed == (
        "sha256:13c8783487db4bdb5e8665d2dc7bc001"
        "314c9beef6922bfa185e7d0a105f3f29"
    )


def _sign(monkeypatch, *, computed_closure=None, signer=None, trust=None):
    snapshot = _snapshot()
    reserved_closure = _closure(snapshot)
    operation = _operation(snapshot, reserved_closure)
    observed = {}

    def compute(registry, request):
        observed["registry"] = registry
        observed["request"] = request
        return computed_closure or reserved_closure

    monkeypatch.setattr(
        policy_attestor_module,
        "compute_policy_evidence_closure",
        compute,
    )
    registry = object()
    selected_signer = signer or _Signer()
    completion = sign_policy_decision(
        operation.request,
        reservation=operation,
        registry=registry,
        policy_snapshot=snapshot,
        signer=selected_signer,
        signing_trust=trust or _trust(),
    )
    return (
        completion,
        operation,
        snapshot,
        reserved_closure,
        selected_signer,
        observed,
    )


def test_attestor_recomputes_closure_and_signs_exact_reserved_jcs(monkeypatch):
    completion, operation, snapshot, closure, signer, observed = _sign(
        monkeypatch
    )

    signed = completion.signed_decision
    assert observed["request"] == snapshot.evidence_request(
        operation.request.unsigned_envelope
    )
    assert observed["registry"] is not None
    assert completion.operation_id == operation.operation_id
    assert completion.fencing_token == operation.fencing_token
    assert completion.request_fingerprint == operation.request_fingerprint
    assert signed.unsigned_envelope == operation.request.unsigned_envelope
    assert signed.attestation_id == operation.request.unsigned_envelope_digest
    assert signed.key_version == _POLICY_KEY
    assert signed.algorithm == _ALGORITHM
    assert signer.calls == [
        bytes.fromhex(operation.request.unsigned_envelope_digest.bare)
    ]
    assert closure.closure_digest == signed.unsigned_envelope.evidence_closure_digest
    attestation = signed.to_attestation_record()
    assert attestation.attestation_kind == "policy_decision"
    assert attestation.attestation_id == signed.attestation_id
    assert attestation.unsigned_envelope == signed.unsigned_envelope.to_jcs_dict()
    assert (
        decode_record(type(completion), encode_record(completion))
        == completion
    )


def test_revocation_uses_real_read_only_evidence_recomputation():
    snapshot = _snapshot()
    registry = FakeRegistryV2(server_read_time=_SERVER_TIME)
    evidence_request = PolicyEvidenceRequest(
        subject_asset_version_id=_SUBJECT,
        environment_fingerprint=_FP,
        read_time=_READ_TIME,
        allowed_producer_identities=snapshot.allowed_producer_identities,
        allowed_scanners=snapshot.allowed_scanners,
        max_depth=snapshot.max_depth,
        max_nodes=snapshot.max_nodes,
        page_size=snapshot.page_size,
    )
    closure = compute_policy_evidence_closure(registry, evidence_request)
    assert closure.approvable is False
    operation = reserve_policy_decision(
        registry,
        environment_id="production",
        environment_fingerprint=_FP,
        decision=PolicyDecision.REVOKED,
        closure=closure,
        policy_version=snapshot.policy_version,
        policy_snapshot_digest=snapshot.digest,
        key_version=_POLICY_KEY,
        idempotency_key="revoke-request-1",
        owner_principal="controller@example.test",
        validity_seconds=3600,
    )

    completion = sign_policy_decision(
        operation.request,
        reservation=operation,
        registry=registry,
        policy_snapshot=snapshot,
        signer=_Signer(),
        signing_trust=_trust(),
    )

    assert (
        completion.signed_decision.unsigned_envelope.decision
        is PolicyDecision.REVOKED
    )


def test_attestor_retry_keeps_identical_attestation_identity(monkeypatch):
    first, operation, snapshot, closure, _, _ = _sign(monkeypatch)
    second_signer = _Signer(signature=b"another-valid-signature")
    monkeypatch.setattr(
        policy_attestor_module,
        "compute_policy_evidence_closure",
        lambda registry, request: closure,
    )

    second = sign_policy_decision(
        operation.request,
        reservation=operation,
        registry=object(),
        policy_snapshot=snapshot,
        signer=second_signer,
        signing_trust=_trust(),
    )

    assert (
        second.signed_decision.attestation_id
        == first.signed_decision.attestation_id
    )
    assert (
        second.signed_decision.signature_b64
        != first.signed_decision.signature_b64
    )


def test_signing_request_must_exactly_match_reservation(monkeypatch):
    _, operation, snapshot, closure, _, _ = _sign(monkeypatch)
    changed = replace(
        operation.request,
        expected_head_revision=operation.request.expected_head_revision + 1,
    )

    with pytest.raises(PolicyAttestorError, match="exactly match"):
        sign_policy_decision(
            changed,
            reservation=operation,
            registry=object(),
            policy_snapshot=snapshot,
            signer=_Signer(),
            signing_trust=_trust(),
        )


def test_policy_snapshot_must_match_reserved_digest(monkeypatch):
    _, operation, snapshot, closure, _, _ = _sign(monkeypatch)
    changed = replace(snapshot, policy_version="policy-attacker")

    with pytest.raises(PolicyAttestorError, match="snapshot"):
        sign_policy_decision(
            operation.request,
            reservation=operation,
            registry=object(),
            policy_snapshot=changed,
            signer=_Signer(),
            signing_trust=_trust(),
        )


def test_independent_closure_must_match_every_reserved_digest(monkeypatch):
    snapshot = _snapshot()
    changed = _closure(snapshot, approvable=False)

    with pytest.raises(PolicyAttestorError, match="does not match"):
        _sign(monkeypatch, computed_closure=changed)


@pytest.mark.parametrize(
    "signer,trust,error",
    [
        (
            _Signer(
                key_version=(
                    "projects/p/locations/l/keyRings/r/cryptoKeys/"
                    "manifest-integrity/cryptoKeyVersions/1"
                )
            ),
            _trust(),
            "does not match",
        ),
        (
            _Signer(),
            PolicySigningTrust(
                allowed_key_resources=(
                    "projects/p/locations/l/keyRings/r/cryptoKeys/other",
                ),
                allowed_key_versions=(
                    "projects/p/locations/l/keyRings/r/cryptoKeys/"
                    "other/cryptoKeyVersions/1",
                ),
                allowed_algorithms=(_ALGORITHM,),
            ),
            "resource",
        ),
        (
            _Signer(),
            PolicySigningTrust(
                allowed_key_resources=(_POLICY_KEY_RESOURCE,),
                allowed_key_versions=(
                    f"{_POLICY_KEY_RESOURCE}/cryptoKeyVersions/2",
                ),
                allowed_algorithms=(_ALGORITHM,),
            ),
            "version",
        ),
        (
            _Signer(),
            PolicySigningTrust(
                allowed_key_resources=(_POLICY_KEY_RESOURCE,),
                allowed_key_versions=(_POLICY_KEY,),
                allowed_algorithms=("RSA_SIGN_PKCS1_2048_SHA256",),
            ),
            "algorithm",
        ),
    ],
)
def test_untrusted_key_resource_version_or_algorithm_fails_closed(
    monkeypatch,
    signer,
    trust,
    error,
):
    with pytest.raises(PolicyAttestorError, match=error):
        _sign(monkeypatch, signer=signer, trust=trust)

    assert signer.calls == []


def test_attestor_refuses_post_attestation_operation_phase(monkeypatch):
    _, operation, snapshot, closure, _, _ = _sign(monkeypatch)
    verified = replace(
        operation,
        phase=PolicyDecisionOperationPhase.VERIFIED,
        revision=operation.revision + 1,
    )

    with pytest.raises(PolicyAttestorError, match="reserved or attesting"):
        sign_policy_decision(
            verified.request,
            reservation=verified,
            registry=object(),
            policy_snapshot=snapshot,
            signer=_Signer(),
            signing_trust=_trust(),
        )


def test_signer_failure_is_closed_without_partial_completion(monkeypatch):
    class FailingSigner(_Signer):
        def sign_sha256_digest(self, digest: bytes) -> bytes:
            raise RuntimeError("KMS unavailable")

    with pytest.raises(PolicyAttestorError, match="signing failed"):
        _sign(monkeypatch, signer=FailingSigner())


def test_signed_decision_rejects_tampered_identity(monkeypatch):
    completion, *_ = _sign(monkeypatch)

    with pytest.raises(PolicyAttestorError, match="identity"):
        replace(
            completion.signed_decision,
            attestation_id=TypedId.from_bare("ff" * 32),
        )
    with pytest.raises(PolicyAttestorError, match="canonical base64"):
        replace(completion.signed_decision, signature_b64="not-base64")


def test_cloud_kms_policy_signer_pins_metadata_and_digest():
    class Client:
        def __init__(self):
            self.public_key_requests = []
            self.sign_requests = []

        def get_public_key(self, *, request):
            self.public_key_requests.append(request)
            return SimpleNamespace(
                name=_POLICY_KEY,
                algorithm=_ALGORITHM,
            )

        def asymmetric_sign(self, *, request):
            self.sign_requests.append(request)
            return SimpleNamespace(signature=b"kms-signature")

    client = Client()
    signer = CloudKmsPolicyDecisionSigner(
        _POLICY_KEY,
        _ALGORITHM,
        client=client,
    )
    digest = hashlib.sha256(b"decision").digest()

    assert signer.sign_sha256_digest(digest) == b"kms-signature"
    assert signer.sign_sha256_digest(digest) == b"kms-signature"
    assert client.public_key_requests == [{"name": _POLICY_KEY}]
    assert client.sign_requests == [
        {"name": _POLICY_KEY, "digest": {"sha256": digest}},
        {"name": _POLICY_KEY, "digest": {"sha256": digest}},
    ]


def test_cloud_kms_policy_signer_rejects_algorithm_drift_before_sign():
    class Client:
        sign_called = False

        def get_public_key(self, *, request):
            return SimpleNamespace(
                name=_POLICY_KEY,
                algorithm="RSA_SIGN_PKCS1_2048_SHA256",
            )

        def asymmetric_sign(self, *, request):
            self.sign_called = True
            return SimpleNamespace(signature=b"must-not-sign")

    client = Client()
    signer = CloudKmsPolicyDecisionSigner(
        _POLICY_KEY,
        _ALGORITHM,
        client=client,
    )

    with pytest.raises(PolicyAttestorError, match="algorithm"):
        signer.sign_sha256_digest(hashlib.sha256(b"decision").digest())
    assert client.sign_called is False

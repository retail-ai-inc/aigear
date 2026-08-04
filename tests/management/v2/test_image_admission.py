from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.image_admission import (
    AdmissionImage,
    BreakGlassGrant,
    ImageAdmissionError,
    ImageAdmissionPolicy,
    create_break_glass_attestation,
    evaluate_image_admission,
)


_NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)
_FP = TypedId.from_bare("aa" * 32)
_DIGEST = TypedId.from_bare("bb" * 32)
_KEY = "projects/prod/locations/global/keyRings/admission/cryptoKeys/break/cryptoKeyVersions/1"
_BUILDER = "projects/prod/attestors/trusted-builder"
_SECURITY = "projects/prod/attestors/security-qualified"
_SIGNER = HmacTestSigner(b"break-glass", key_version=_KEY)
_VERIFIER = HmacTestVerifier(b"break-glass", key_version=_KEY)


def _policy():
    return ImageAdmissionPolicy(
        schema_version="2.0",
        policy_id="production-images-v1",
        environment_fingerprint=_FP,
        project_id="prod",
        cluster_specifier="asia-east1.prod-cluster",
        required_attestors=(_SECURITY, _BUILDER),
        break_glass_key_versions=(_KEY,),
        max_break_glass_ttl_seconds=900,
    )


def _image(*attestors, digest=_DIGEST):
    return AdmissionImage(
        image_reference=f"asia-docker.pkg.dev/prod/runtime/predictor@{digest.typed}",
        image_digest=digest,
        verified_attestors=tuple(sorted(attestors)),
    )


def _decide(image, attestation=None, now=_NOW):
    return evaluate_image_admission(
        _policy(),
        (image,),
        break_glass_attestation=attestation,
        break_glass_verifier=_VERIFIER,
        now=now,
    )


def test_direct_workload_requires_builder_and_security_attestors():
    assert _decide(_image(_BUILDER, _SECURITY)).allowed
    assert not _decide(_image()).allowed
    assert not _decide(_image(_BUILDER)).allowed


def test_mutable_image_is_rejected_before_admission_evaluation():
    with pytest.raises(ImageAdmissionError, match="digest pinned"):
        AdmissionImage(
            image_reference="asia-docker.pkg.dev/prod/runtime/predictor:latest",
            image_digest=_DIGEST,
            verified_attestors=(),
        )


def _grant(*, expires_at=None, image_digest=_DIGEST):
    grant = BreakGlassGrant(
        policy_id="production-images-v1",
        image_digest=image_digest,
        ticket_id="incident-123",
        owner="security@example.com",
        reason="restore critical service",
        issued_at=_NOW.isoformat(),
        expires_at=(expires_at or (_NOW + timedelta(minutes=10))).isoformat(),
    )
    return create_break_glass_attestation(
        grant, environment_fingerprint=_FP, signer=_SIGNER
    )


def test_signed_break_glass_is_single_image_bounded_and_audited():
    decision = _decide(_image(), _grant())

    assert decision.allowed
    assert decision.audit_fields["ticket_id"] == "incident-123"
    assert decision.audit_fields["owner"] == "security@example.com"
    assert decision.audit_fields["reason"] == "restore critical service"
    assert decision.audit_fields["expires_at"]
    assert decision.audit_fields["grant_attestation_id"].startswith("sha256:")

    other = TypedId.from_bare("cc" * 32)
    assert not _decide(_image(), _grant(image_digest=other)).allowed

    mixed = evaluate_image_admission(
        _policy(),
        (_image(), _image(digest=other)),
        break_glass_attestation=_grant(),
        break_glass_verifier=_VERIFIER,
        now=_NOW,
    )
    assert not mixed.allowed


def test_expired_or_overlong_break_glass_automatically_stops_authorizing():
    expired = _grant(expires_at=_NOW + timedelta(minutes=1))
    assert not _decide(_image(), expired, now=_NOW + timedelta(minutes=1)).allowed

    overlong = _grant(expires_at=_NOW + timedelta(hours=1))
    assert not _decide(_image(), overlong).allowed


def test_binary_authorization_policy_is_enforced_and_requires_both_attestors():
    document = _policy().to_binary_authorization_policy()
    rule = document["clusterAdmissionRules"]["asia-east1.prod-cluster"]

    assert document["name"] == "projects/prod/policy"
    assert rule["evaluationMode"] == "REQUIRE_ATTESTATION"
    assert rule["enforcementMode"] == "ENFORCED_BLOCK_AND_AUDIT_LOG"
    assert rule["requireAttestationsBy"] == [_SECURITY, _BUILDER]
    assert "admissionWhitelistPatterns" not in document

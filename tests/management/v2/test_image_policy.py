from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.image_policy import (
    HighSeverityException,
    ImagePolicyError,
    ImageSupplyChainBundle,
    RegistryImagePolicyVerifier,
    create_image_supply_chain_attestation,
    verify_image_supply_chain,
)
from aigear.management.v2.release_manifest import ReleaseImageBinding


_NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)
_FP = TypedId.from_bare("aa" * 32)
_KEY = "image-attestor/versions/1"
_SIGNER = HmacTestSigner(b"image-policy", key_version=_KEY)
_VERIFIER = HmacTestVerifier(b"image-policy", key_version=_KEY)


def _bundle(image, **overrides):
    values = {
        "image_digest": image.image_digest,
        "builder_id": "cloud-build-production",
        "source_revision": "commit-abc123",
        "build_config_digest": TypedId.from_bare("11" * 32),
        "materials_digest": TypedId.from_bare("22" * 32),
        "sbom_digest": image.sbom_digest,
        "image_signature_digest": TypedId.from_bare("33" * 32),
        "vulnerability_scan_digest": TypedId.from_bare("44" * 32),
        "critical_count": 0,
        "high_count": 0,
        "license_decision_digest": TypedId.from_bare("55" * 32),
        "license_approved": True,
        "generated_at": _NOW.isoformat(),
    }
    values.update(overrides)
    return ImageSupplyChainBundle(**values)


def _evidence(**overrides):
    image_digest = TypedId.from_bare("04" * 32)
    image = ReleaseImageBinding(
        image_reference=f"repo/predictor@{image_digest.typed}",
        image_digest=image_digest,
        provenance_attestation_id=TypedId.from_bare("06" * 32),
        sbom_digest=TypedId.from_bare("07" * 32),
    )
    bundle = _bundle(image, **overrides)
    attestation = create_image_supply_chain_attestation(
        bundle, environment_fingerprint=_FP, signer=_SIGNER
    )
    return replace(image, provenance_attestation_id=attestation.attestation_id), attestation


def _verify(image, attestation, *, now=_NOW):
    calls = []
    result = verify_image_supply_chain(
        image,
        attestation,
        verifier=_VERIFIER,
        expected_environment_fingerprint=_FP,
        allowed_key_versions=(_KEY,),
        allowed_builder_ids=("cloud-build-production",),
        verify_image_signature=lambda image_id, signature_id: calls.append(
            (image_id, signature_id)
        ),
        now=now,
        max_evidence_age_seconds=24 * 60 * 60,
    )
    return result, calls


def test_signed_bundle_binds_all_image_evidence_and_verifies_signature():
    image, attestation = _evidence()

    result, calls = _verify(image, attestation)

    assert result.image_digest == image.image_digest
    assert result.sbom_digest == image.sbom_digest
    assert calls == [(image.image_digest, result.image_signature_digest)]


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"critical_count": 1}, "Critical"),
        ({"high_count": 1}, "current exception"),
        ({"license_approved": False}, "license"),
    ],
)
def test_critical_unexcepted_high_and_unapproved_license_are_rejected(
    overrides, message
):
    image, attestation = _evidence(**overrides)

    with pytest.raises(ImagePolicyError, match=message):
        _verify(image, attestation)


def test_high_exception_requires_owner_reason_and_unexpired_deadline():
    current = HighSeverityException(
        owner="security@example.com",
        reason="no fixed package is available",
        expires_at=(_NOW + timedelta(days=1)).isoformat(),
    )
    image, attestation = _evidence(high_count=1, high_severity_exception=current)
    assert _verify(image, attestation)[0].high_count == 1

    expired = replace(current, expires_at=(_NOW - timedelta(seconds=1)).isoformat())
    image, attestation = _evidence(high_count=1, high_severity_exception=expired)
    with pytest.raises(ImagePolicyError, match="current exception"):
        _verify(image, attestation)


def test_unsigned_mismatched_or_untrusted_builder_evidence_is_rejected():
    image, attestation = _evidence()
    with pytest.raises(ImagePolicyError, match="mismatched"):
        _verify(replace(image, provenance_attestation_id=TypedId.from_bare("99" * 32)), attestation)

    image, attestation = _evidence(builder_id="developer-laptop")
    with pytest.raises(ImagePolicyError, match="builder"):
        _verify(image, attestation)

    image, attestation = _evidence()
    with pytest.raises(ValueError, match="signature"):
        verify_image_supply_chain(
            image,
            replace(attestation, signature_b64="YWJj"),
            verifier=_VERIFIER,
            expected_environment_fingerprint=_FP,
            allowed_key_versions=(_KEY,),
            allowed_builder_ids=("cloud-build-production",),
            verify_image_signature=lambda _image, _signature: None,
            now=_NOW,
            max_evidence_age_seconds=24 * 60 * 60,
        )


def test_cross_environment_and_stale_evidence_are_rejected():
    image, attestation = _evidence()
    with pytest.raises(ImagePolicyError, match="mismatched"):
        verify_image_supply_chain(
            image,
            attestation,
            verifier=_VERIFIER,
            expected_environment_fingerprint=TypedId.from_bare("bb" * 32),
            allowed_key_versions=(_KEY,),
            allowed_builder_ids=("cloud-build-production",),
            verify_image_signature=lambda _image, _signature: None,
            now=_NOW,
            max_evidence_age_seconds=24 * 60 * 60,
        )

    with pytest.raises(ImagePolicyError, match="stale"):
        _verify(image, attestation, now=_NOW + timedelta(days=2))


def test_registry_adapter_rejects_missing_unsigned_evidence():
    image, _attestation = _evidence()
    adapter = RegistryImagePolicyVerifier(
        registry=object(),
        verifier=_VERIFIER,
        environment_fingerprint=_FP,
        allowed_key_versions=(_KEY,),
        allowed_builder_ids=("cloud-build-production",),
        verify_image_signature=lambda _image, _signature: None,
        max_evidence_age_seconds=24 * 60 * 60,
    )

    with pytest.raises(ImagePolicyError, match="missing"):
        adapter(image, _NOW)

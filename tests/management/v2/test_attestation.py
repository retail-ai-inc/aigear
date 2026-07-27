from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.attestation import (
    AttestationError,
    CloudKmsAsymmetricSigner,
    HmacTestSigner,
    create_attestation,
)
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId


_FP = TypedId.from_bare("aa" * 32)


def _record():
    return create_attestation(
        schema_version="2.0",
        attestation_kind="manifest_integrity",
        environment_fingerprint=_FP,
        subject={"asset_version_id": "sha256:" + "bb" * 32},
        signer=HmacTestSigner(b"secret", key_version="test/key/cryptoKeyVersions/1"),
    )


def test_create_attestation_is_domain_separated_and_self_verifying():
    record = _record()
    assert record.unsigned_envelope["domain"] == "aigear.attestation.manifest_integrity.v2"
    assert record.attestation_id.typed.startswith("sha256:")


def test_attestation_rejects_tampered_unsigned_envelope():
    record = _record()
    with pytest.raises(AttestationError, match="does not match"):
        replace(record, unsigned_envelope={**record.unsigned_envelope, "subject": {"tampered": True}})


def test_registry_converges_same_envelope_with_different_signature_bytes():
    registry = FakeRegistryV2()
    first = _record()
    second = replace(first, signature_b64="YW5vdGhlci12YWxpZC1zaWduYXR1cmU=")
    assert registry.put_attestation(first) is first
    assert registry.put_attestation(second) is first


def test_cloud_kms_signer_pins_version_and_sends_sha256_digest():
    class Response:
        signature = b"kms-signature"

    class Client:
        def __init__(self):
            self.request = None

        def asymmetric_sign(self, *, request):
            self.request = request
            return Response()

    key = "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/7"
    client = Client()
    signer = CloudKmsAsymmetricSigner(key, client=client)
    digest = b"x" * 32
    assert signer.sign_sha256_digest(digest) == b"kms-signature"
    assert client.request == {"name": key, "digest": {"sha256": digest}}

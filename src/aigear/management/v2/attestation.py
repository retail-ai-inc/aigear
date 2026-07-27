"""Create-only, domain-separated Pipeline V2 integrity attestations.

The signed identity is the RFC-8785 canonical ``unsigned_envelope``.  The
Firestore receive timestamp and signature bytes are deliberately excluded
from ``attestation_id`` so an idempotent retry can reuse the same evidence.

``HmacTestSigner`` is only a deterministic test/development signer.
Production callers must inject ``CloudKmsAsymmetricSigner`` (or another
reviewed implementation of :class:`DigestSigner`) and grant that principal
access to one pinned CryptoKeyVersion only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "AttestationError",
    "DigestSigner",
    "AttestationRecord",
    "HmacTestSigner",
    "CloudKmsAsymmetricSigner",
    "create_attestation",
    "same_attestation_identity",
]


class AttestationError(ValueError):
    """Raised for a malformed or unverifiable attestation operation."""


class DigestSigner(Protocol):
    @property
    def key_version(self) -> str: ...

    def sign_sha256_digest(self, digest: bytes) -> bytes: ...


@dataclass(frozen=True)
class AttestationRecord:
    schema_version: str
    attestation_kind: str
    attestation_id: TypedId
    environment_fingerprint: TypedId
    unsigned_envelope: Mapping[str, Any]
    key_version: str
    signature_b64: str

    def __post_init__(self) -> None:
        if not self.attestation_kind:
            raise AttestationError("attestation_kind must be non-empty")
        if not self.key_version:
            raise AttestationError("key_version must be non-empty")
        expected = TypedId.from_bare(hashlib.sha256(canonicalize_json(self.unsigned_envelope)).hexdigest())
        if expected != self.attestation_id:
            raise AttestationError("attestation_id does not match unsigned_envelope")
        try:
            signature = base64.b64decode(self.signature_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise AttestationError("signature_b64 must be canonical base64") from exc
        if not signature:
            raise AttestationError("signature must be non-empty")


def same_attestation_identity(left: AttestationRecord, right: AttestationRecord) -> bool:
    """Compare signed identity while deliberately ignoring signature bytes.

    Asymmetric signatures need not be byte-for-byte deterministic. Concurrent
    signers and transaction retries therefore converge on the same envelope
    and reuse whichever valid signature was committed first.
    """
    return (
        left.schema_version == right.schema_version
        and left.attestation_kind == right.attestation_kind
        and left.attestation_id == right.attestation_id
        and left.environment_fingerprint == right.environment_fingerprint
        and left.unsigned_envelope == right.unsigned_envelope
        and left.key_version == right.key_version
    )


class HmacTestSigner:
    """Deterministic signer for unit tests; never accepted by production mode."""

    def __init__(self, secret: bytes = b"aigear-v2-test-signer", *, key_version: str = "test-only") -> None:
        if not secret:
            raise AttestationError("test signer secret must be non-empty")
        self._secret = bytes(secret)
        self._key_version = key_version

    @property
    def key_version(self) -> str:
        return self._key_version

    def sign_sha256_digest(self, digest: bytes) -> bytes:
        if len(digest) != hashlib.sha256().digest_size:
            raise AttestationError("sign_sha256_digest requires exactly one SHA-256 digest")
        return hmac.new(self._secret, digest, hashlib.sha256).digest()


class CloudKmsAsymmetricSigner:
    """Google Cloud KMS asymmetric signer with a pinned CryptoKeyVersion.

    The import is lazy so users that only use Legacy/V1 or test fakes do not
    need the optional GCP dependency at import time.
    """

    def __init__(self, key_version: str, *, client: Any | None = None) -> None:
        if "/cryptoKeyVersions/" not in key_version:
            raise AttestationError(
                "key_version must be a full pinned CryptoKeyVersion resource name"
            )
        if client is None:
            try:
                from google.cloud import kms_v1
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise AttestationError(
                    "CloudKmsAsymmetricSigner requires google-cloud-kms"
                ) from exc
            client = kms_v1.KeyManagementServiceClient()
        self._client = client
        self._key_version = key_version

    @property
    def key_version(self) -> str:
        return self._key_version

    def sign_sha256_digest(self, digest: bytes) -> bytes:
        if len(digest) != hashlib.sha256().digest_size:
            raise AttestationError("sign_sha256_digest requires exactly one SHA-256 digest")
        response = self._client.asymmetric_sign(
            request={"name": self._key_version, "digest": {"sha256": digest}}
        )
        signature = bytes(response.signature)
        if not signature:
            raise AttestationError("Cloud KMS returned an empty signature")
        return signature


def create_attestation(
    *,
    schema_version: str,
    attestation_kind: str,
    environment_fingerprint: TypedId,
    subject: Mapping[str, Any],
    signer: DigestSigner,
) -> AttestationRecord:
    if not attestation_kind:
        raise AttestationError("attestation_kind must be non-empty")
    unsigned_envelope = {
        "domain": f"aigear.attestation.{attestation_kind}.v2",
        "schema_version": schema_version,
        "attestation_kind": attestation_kind,
        "environment_fingerprint": environment_fingerprint.typed,
        "key_version": signer.key_version,
        "subject": dict(subject),
    }
    canonical = canonicalize_json(unsigned_envelope)
    digest = hashlib.sha256(canonical).digest()
    attestation_id = TypedId.from_bare(digest.hex())
    signature = signer.sign_sha256_digest(digest)
    return AttestationRecord(
        schema_version=schema_version,
        attestation_kind=attestation_kind,
        attestation_id=attestation_id,
        environment_fingerprint=environment_fingerprint,
        unsigned_envelope=unsigned_envelope,
        key_version=signer.key_version,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )

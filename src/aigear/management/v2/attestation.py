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
from typing import Any, Mapping, Protocol, Sequence

from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "AttestationError",
    "DigestSigner",
    "AttestationVerifier",
    "AttestationRecord",
    "HmacTestSigner",
    "CloudKmsAsymmetricSigner",
    "HmacTestVerifier",
    "CloudKmsAttestationVerifier",
    "create_attestation",
    "verify_attestation",
    "same_attestation_identity",
]


class AttestationError(ValueError):
    """Raised for a malformed or unverifiable attestation operation."""


class DigestSigner(Protocol):
    @property
    def key_version(self) -> str: ...

    def sign_sha256_digest(self, digest: bytes) -> bytes: ...


class AttestationVerifier(Protocol):
    def verify_sha256_digest(
        self, *, key_version: str, digest: bytes, signature: bytes
    ) -> None: ...


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
        expected_envelope = {
            "domain": f"aigear.attestation.{self.attestation_kind}.v2",
            "schema_version": self.schema_version,
            "attestation_kind": self.attestation_kind,
            "environment_fingerprint": self.environment_fingerprint.typed,
            "key_version": self.key_version,
        }
        if not isinstance(self.unsigned_envelope, Mapping) or any(
            self.unsigned_envelope.get(key) != value
            for key, value in expected_envelope.items()
        ):
            raise AttestationError("unsigned_envelope metadata does not match the record")
        if (
            self.attestation_kind != "policy_decision"
            and not isinstance(self.unsigned_envelope.get("subject"), Mapping)
        ):
            raise AttestationError("unsigned_envelope subject must be a mapping")
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


class HmacTestVerifier:
    """Explicit test verifier keyed by the signer's declared key version."""

    def __init__(
        self,
        secret: bytes = b"aigear-v2-test-signer",
        *,
        key_version: str = "test-only",
    ) -> None:
        if not secret or not key_version:
            raise AttestationError("test verifier secret and key_version must be non-empty")
        self._secret = bytes(secret)
        self._key_version = key_version

    def verify_sha256_digest(
        self, *, key_version: str, digest: bytes, signature: bytes
    ) -> None:
        if key_version != self._key_version:
            raise AttestationError("attestation key version is not allowlisted")
        expected = hmac.new(self._secret, digest, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise AttestationError("attestation signature verification failed")


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


class CloudKmsAttestationVerifier:
    """Verify signatures using public keys from pinned Cloud KMS versions."""

    def __init__(self, allowed_key_versions: Sequence[str], *, client: Any | None = None) -> None:
        versions = frozenset(allowed_key_versions)
        if not versions or any("/cryptoKeyVersions/" not in value for value in versions):
            raise AttestationError("allowed_key_versions must contain pinned KMS key versions")
        if client is None:
            try:
                from google.cloud import kms_v1
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise AttestationError(
                    "CloudKmsAttestationVerifier requires google-cloud-kms"
                ) from exc
            client = kms_v1.KeyManagementServiceClient()
        self._client = client
        self._allowed_key_versions = versions
        self._public_keys: dict[str, tuple[Any, str]] = {}

    @staticmethod
    def _algorithm_name(value: Any) -> str:
        name = getattr(value, "name", None)
        if name:
            return str(name)
        try:  # pragma: no cover - exercised with the optional GCP dependency
            from google.cloud import kms_v1

            return kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm(value).name
        except (ImportError, TypeError, ValueError):
            return str(value)

    def _public_key(self, key_version: str):
        cached = self._public_keys.get(key_version)
        if cached is not None:
            return cached
        response = self._client.get_public_key(request={"name": key_version})
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
        except ImportError as exc:  # pragma: no cover - dependency of the GCP extra
            raise AttestationError(
                "CloudKmsAttestationVerifier requires cryptography"
            ) from exc
        public_key = load_pem_public_key(response.pem.encode("ascii"))
        result = (public_key, self._algorithm_name(response.algorithm))
        self._public_keys[key_version] = result
        return result

    def warm(self) -> None:
        """Fetch and parse every allowlisted key outside Registry transactions."""
        for key_version in sorted(self._allowed_key_versions):
            self._public_key(key_version)

    @property
    def is_warm(self) -> bool:
        return self._allowed_key_versions.issubset(self._public_keys)

    def algorithm_for(self, key_version: str) -> str:
        if key_version not in self._allowed_key_versions:
            raise AttestationError("attestation key version is not allowlisted")
        cached = self._public_keys.get(key_version)
        if cached is None:
            raise AttestationError(
                "attestation public key was not prewarmed"
            )
        return cached[1]

    def verify_sha256_digest(
        self, *, key_version: str, digest: bytes, signature: bytes
    ) -> None:
        if key_version not in self._allowed_key_versions:
            raise AttestationError("attestation key version is not allowlisted")
        if len(digest) != hashlib.sha256().digest_size:
            raise AttestationError("verify_sha256_digest requires exactly one SHA-256 digest")
        public_key, algorithm = self._public_key(key_version)
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec, padding, utils

            prehashed = utils.Prehashed(hashes.SHA256())
            if algorithm.startswith("RSA_SIGN_PKCS1_") and algorithm.endswith("_SHA256"):
                public_key.verify(signature, digest, padding.PKCS1v15(), prehashed)
            elif algorithm.startswith("RSA_SIGN_PSS_") and algorithm.endswith("_SHA256"):
                public_key.verify(
                    signature,
                    digest,
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                    prehashed,
                )
            elif algorithm.startswith("EC_SIGN_") and algorithm.endswith("_SHA256"):
                public_key.verify(signature, digest, ec.ECDSA(prehashed))
            else:
                raise AttestationError(
                    f"unsupported Cloud KMS attestation algorithm: {algorithm!r}"
                )
        except AttestationError:
            raise
        except Exception as exc:
            raise AttestationError("attestation signature verification failed") from exc


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


def verify_attestation(record: AttestationRecord, verifier: AttestationVerifier) -> None:
    """Cryptographically verify one self-consistent attestation record."""
    digest = hashlib.sha256(canonicalize_json(record.unsigned_envelope)).digest()
    try:
        signature = base64.b64decode(record.signature_b64, validate=True)
    except (TypeError, ValueError) as exc:  # defensive for decoded external data
        raise AttestationError("signature_b64 must be canonical base64") from exc
    verifier.verify_sha256_digest(
        key_version=record.key_version,
        digest=digest,
        signature=signature,
    )

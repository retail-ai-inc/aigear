"""Independent, read-only policy decision attestation.

The attestor recomputes the bounded evidence closure at the reservation's
fixed Firestore read time.  It signs only the exact JCS envelope already
sealed by the decision operation and never writes Registry state.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Tuple

from aigear.management.v2.attestation import AttestationRecord
from aigear.management.v2.canonical import (
    canonicalize_json,
    digest_sha256_of_jcs,
)
from aigear.management.v2.control_document import parse_schema_version
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_evidence import (
    PolicyEvidenceClosure,
    PolicyEvidenceRequest,
    compute_policy_evidence_closure,
)
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionOperationPhase,
    PolicyDecisionOperationRecord,
    PolicyDecisionRequest,
    PolicyDecisionUnsignedEnvelope,
)

__all__ = [
    "PolicyAttestorError",
    "PolicyApprovalSnapshot",
    "PolicySigningTrust",
    "PolicyDecisionSigner",
    "CloudKmsPolicyDecisionSigner",
    "SignedPolicyDecision",
    "PolicyAttestorCompletion",
    "sign_policy_decision",
]


class PolicyAttestorError(ValueError):
    pass


class PolicyDecisionSigner(Protocol):
    @property
    def key_version(self) -> str: ...

    @property
    def algorithm(self) -> str: ...

    def sign_sha256_digest(self, digest: bytes) -> bytes: ...


def _non_empty(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyAttestorError(f"{field_name} must be a non-empty str")
    return value


def _sorted_unique(field_name: str, values: tuple, *, key=None) -> None:
    if not values:
        raise PolicyAttestorError(f"{field_name} must not be empty")
    ordered = tuple(sorted(values, key=key))
    if values != ordered or len(set(values)) != len(values):
        raise PolicyAttestorError(f"{field_name} must be unique and sorted")


def _split_key_version(key_version: str) -> tuple[str, str]:
    marker = "/cryptoKeyVersions/"
    resource, separator, version = key_version.rpartition(marker)
    if not separator or not resource or not version or "/" in version:
        raise PolicyAttestorError(
            "key_version must be a full pinned CryptoKeyVersion resource"
        )
    return resource, version


@dataclass(frozen=True)
class PolicyApprovalSnapshot:
    """Content-addressed policy inputs used to rebuild an evidence request."""

    schema_version: str
    environment_id: str
    environment_fingerprint: TypedId
    policy_version: str
    allowed_producer_identities: Tuple[TypedId, ...]
    allowed_scanners: Tuple[Tuple[str, str], ...]
    max_depth: int = 8
    max_nodes: int = 500
    page_size: int = 50

    def __post_init__(self) -> None:
        if isinstance(self.allowed_producer_identities, list):
            object.__setattr__(
                self,
                "allowed_producer_identities",
                tuple(self.allowed_producer_identities),
            )
        if isinstance(self.allowed_scanners, list):
            object.__setattr__(
                self,
                "allowed_scanners",
                tuple(tuple(value) for value in self.allowed_scanners),
            )
        parse_schema_version(self.schema_version)
        _non_empty("environment_id", self.environment_id)
        if not isinstance(self.environment_fingerprint, TypedId):
            raise PolicyAttestorError(
                "environment_fingerprint must be TypedId"
            )
        _non_empty("policy_version", self.policy_version)
        if not all(
            isinstance(value, TypedId)
            for value in self.allowed_producer_identities
        ):
            raise PolicyAttestorError(
                "allowed_producer_identities must contain TypedId values"
            )
        _sorted_unique(
            "allowed_producer_identities",
            self.allowed_producer_identities,
            key=lambda value: value.typed.encode("utf-8"),
        )
        if not all(
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(part, str) and part for part in value)
            for value in self.allowed_scanners
        ):
            raise PolicyAttestorError(
                "allowed_scanners must contain (scanner_id, version) tuples"
            )
        _sorted_unique("allowed_scanners", self.allowed_scanners)
        bounds = (
            ("max_depth", self.max_depth, 0, 32),
            ("max_nodes", self.max_nodes, 1, 10_000),
            ("page_size", self.page_size, 1, 500),
        )
        for field_name, value, minimum, maximum in bounds:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise PolicyAttestorError(
                    f"{field_name} must be between {minimum} and {maximum}"
                )

    def canonical_dict(self) -> dict:
        return {
            "domain": "aigear.policy-approval-snapshot.v2",
            "schema_version": self.schema_version,
            "environment_id": self.environment_id,
            "environment_fingerprint": self.environment_fingerprint.typed,
            "policy_version": self.policy_version,
            "allowed_producer_identities": [
                value.typed for value in self.allowed_producer_identities
            ],
            "allowed_scanners": [
                list(value) for value in self.allowed_scanners
            ],
            "max_depth": self.max_depth,
            "max_nodes": self.max_nodes,
            "page_size": self.page_size,
            "traversal": "asset-inputs-depth-first-v1",
        }

    @property
    def digest(self) -> TypedId:
        return TypedId.from_bare(
            digest_sha256_of_jcs(self.canonical_dict())
        )

    def evidence_request(
        self, envelope: PolicyDecisionUnsignedEnvelope
    ) -> PolicyEvidenceRequest:
        if not isinstance(envelope, PolicyDecisionUnsignedEnvelope):
            raise PolicyAttestorError(
                "envelope must be PolicyDecisionUnsignedEnvelope"
            )
        return PolicyEvidenceRequest(
            subject_asset_version_id=envelope.subject_asset_version_id,
            environment_fingerprint=envelope.environment_fingerprint,
            read_time=_parse_read_time(envelope.firestore_read_time),
            allowed_producer_identities=self.allowed_producer_identities,
            allowed_scanners=self.allowed_scanners,
            max_depth=self.max_depth,
            max_nodes=self.max_nodes,
            page_size=self.page_size,
        )


@dataclass(frozen=True)
class PolicySigningTrust:
    """Deployment-owned allowlist for the independent approval key."""

    allowed_key_resources: Tuple[str, ...]
    allowed_key_versions: Tuple[str, ...]
    allowed_algorithms: Tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "allowed_key_resources",
            "allowed_key_versions",
            "allowed_algorithms",
        ):
            value = getattr(self, field_name)
            if isinstance(value, list):
                value = tuple(value)
                object.__setattr__(self, field_name, value)
            if not all(isinstance(item, str) and item for item in value):
                raise PolicyAttestorError(
                    f"{field_name} must contain non-empty strings"
                )
            _sorted_unique(field_name, value)
        for key_version in self.allowed_key_versions:
            resource, _ = _split_key_version(key_version)
            if resource not in self.allowed_key_resources:
                raise PolicyAttestorError(
                    "allowed key version belongs to an untrusted key resource"
                )
        if not all(
            value.endswith("_SHA256")
            and value.startswith(("RSA_SIGN_", "EC_SIGN_"))
            for value in self.allowed_algorithms
        ):
            raise PolicyAttestorError(
                "allowed_algorithms must be asymmetric SHA-256 algorithms"
            )

    def authorize(
        self,
        *,
        key_version: str,
        algorithm: str,
    ) -> None:
        resource, _ = _split_key_version(key_version)
        if resource not in self.allowed_key_resources:
            raise PolicyAttestorError(
                "policy signing key resource is not allowlisted"
            )
        if key_version not in self.allowed_key_versions:
            raise PolicyAttestorError(
                "policy signing key version is not allowlisted"
            )
        if algorithm not in self.allowed_algorithms:
            raise PolicyAttestorError(
                "policy signing algorithm is not allowlisted"
            )


class CloudKmsPolicyDecisionSigner:
    """Cloud KMS signer pinned to one key version and one exact algorithm."""

    def __init__(
        self,
        key_version: str,
        algorithm: str,
        *,
        client: Any | None = None,
    ) -> None:
        _split_key_version(key_version)
        _non_empty("algorithm", algorithm)
        if client is None:
            try:
                from google.cloud import kms_v1
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise PolicyAttestorError(
                    "CloudKmsPolicyDecisionSigner requires google-cloud-kms"
                ) from exc
            client = kms_v1.KeyManagementServiceClient()
        self._client = client
        self._key_version = key_version
        self._algorithm = algorithm
        self._metadata_verified = False

    @property
    def key_version(self) -> str:
        return self._key_version

    @property
    def algorithm(self) -> str:
        return self._algorithm

    @staticmethod
    def _algorithm_name(value: Any) -> str:
        name = getattr(value, "name", None)
        if name is not None:
            return str(name)
        try:  # pragma: no cover - exercised with the optional GCP dependency
            from google.cloud import kms_v1

            return kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm(
                value
            ).name
        except (ImportError, TypeError, ValueError):
            return str(value)

    def _verify_metadata(self) -> None:
        if self._metadata_verified:
            return
        response = self._client.get_public_key(
            request={"name": self._key_version}
        )
        if getattr(response, "name", None) != self._key_version:
            raise PolicyAttestorError(
                "Cloud KMS returned metadata for another key version"
            )
        if self._algorithm_name(getattr(response, "algorithm", None)) != (
            self._algorithm
        ):
            raise PolicyAttestorError(
                "Cloud KMS key algorithm does not match pinned algorithm"
            )
        self._metadata_verified = True

    def sign_sha256_digest(self, digest: bytes) -> bytes:
        if not isinstance(digest, bytes) or len(digest) != hashlib.sha256().digest_size:
            raise PolicyAttestorError(
                "sign_sha256_digest requires exactly one SHA-256 digest"
            )
        self._verify_metadata()
        response = self._client.asymmetric_sign(
            request={
                "name": self._key_version,
                "digest": {"sha256": digest},
            }
        )
        signature = bytes(getattr(response, "signature", b""))
        if not signature:
            raise PolicyAttestorError("Cloud KMS returned an empty signature")
        return signature


@dataclass(frozen=True)
class SignedPolicyDecision:
    unsigned_envelope: PolicyDecisionUnsignedEnvelope
    unsigned_envelope_digest: TypedId
    attestation_id: TypedId
    key_version: str
    algorithm: str
    signature_b64: str

    def __post_init__(self) -> None:
        if not isinstance(
            self.unsigned_envelope, PolicyDecisionUnsignedEnvelope
        ):
            raise PolicyAttestorError(
                "unsigned_envelope must be PolicyDecisionUnsignedEnvelope"
            )
        if (
            self.unsigned_envelope.digest != self.unsigned_envelope_digest
            or self.attestation_id != self.unsigned_envelope_digest
        ):
            raise PolicyAttestorError(
                "attestation identity does not match unsigned envelope"
            )
        if self.key_version != self.unsigned_envelope.key_version:
            raise PolicyAttestorError(
                "signed key version does not match unsigned envelope"
            )
        _non_empty("algorithm", self.algorithm)
        try:
            signature = base64.b64decode(self.signature_b64, validate=True)
        except (TypeError, ValueError) as exc:
            raise PolicyAttestorError(
                "signature_b64 must be canonical base64"
            ) from exc
        if not signature:
            raise PolicyAttestorError("signature must not be empty")

    def to_attestation_record(self) -> AttestationRecord:
        return AttestationRecord(
            schema_version=self.unsigned_envelope.schema_version,
            attestation_kind="policy_decision",
            attestation_id=self.attestation_id,
            environment_fingerprint=(
                self.unsigned_envelope.environment_fingerprint
            ),
            unsigned_envelope=self.unsigned_envelope.to_jcs_dict(),
            key_version=self.key_version,
            signature_b64=self.signature_b64,
        )


@dataclass(frozen=True)
class PolicyAttestorCompletion:
    operation_id: str
    request_fingerprint: TypedId
    fencing_token: int
    signed_decision: SignedPolicyDecision

    def __post_init__(self) -> None:
        if not isinstance(self.signed_decision, SignedPolicyDecision):
            raise PolicyAttestorError(
                "signed_decision must be SignedPolicyDecision"
            )
        envelope = self.signed_decision.unsigned_envelope
        if (
            self.operation_id != envelope.operation_id
            or self.fencing_token != envelope.fencing_token
        ):
            raise PolicyAttestorError(
                "completion is not bound to signed operation fence"
            )
        if not isinstance(self.request_fingerprint, TypedId):
            raise PolicyAttestorError(
                "request_fingerprint must be TypedId"
            )


def _parse_read_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise PolicyAttestorError(
            "firestore_read_time must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyAttestorError(
            "firestore_read_time must be timezone-aware"
        )
    return parsed.astimezone(timezone.utc)


def _evidence_digests(
    closure: PolicyEvidenceClosure,
) -> tuple[TypedId, ...]:
    values = {
        closure.filter_digest,
        closure.closure_digest,
        *(node.evidence_digest for node in closure.nodes),
    }
    return tuple(
        sorted(values, key=lambda value: value.typed.encode("utf-8"))
    )


def _validate_snapshot(
    snapshot: PolicyApprovalSnapshot,
    envelope: PolicyDecisionUnsignedEnvelope,
) -> None:
    if not isinstance(snapshot, PolicyApprovalSnapshot):
        raise PolicyAttestorError(
            "policy_snapshot must be PolicyApprovalSnapshot"
        )
    if (
        snapshot.digest != envelope.policy_snapshot_digest
        or snapshot.schema_version != envelope.schema_version
        or snapshot.environment_id != envelope.environment_id
        or snapshot.environment_fingerprint
        != envelope.environment_fingerprint
        or snapshot.policy_version != envelope.policy_version
    ):
        raise PolicyAttestorError(
            "policy snapshot does not match reserved decision"
        )


def sign_policy_decision(
    request: PolicyDecisionRequest,
    *,
    reservation: PolicyDecisionOperationRecord,
    registry,
    policy_snapshot: PolicyApprovalSnapshot,
    signer: PolicyDecisionSigner,
    signing_trust: PolicySigningTrust,
) -> PolicyAttestorCompletion:
    """Rebuild evidence and sign the exact reservation without Registry writes."""

    if not isinstance(request, PolicyDecisionRequest):
        raise PolicyAttestorError("request must be PolicyDecisionRequest")
    if not isinstance(reservation, PolicyDecisionOperationRecord):
        raise PolicyAttestorError(
            "reservation must be PolicyDecisionOperationRecord"
        )
    if reservation.phase not in {
        PolicyDecisionOperationPhase.RESERVED,
        PolicyDecisionOperationPhase.ATTESTING,
    }:
        raise PolicyAttestorError(
            "only reserved or attesting decisions may be signed"
        )
    if request != reservation.request:
        raise PolicyAttestorError(
            "signing request does not exactly match reservation"
        )
    envelope = request.unsigned_envelope
    _validate_snapshot(policy_snapshot, envelope)

    key_version = _non_empty(
        "signer.key_version", getattr(signer, "key_version", None)
    )
    algorithm = _non_empty(
        "signer.algorithm", getattr(signer, "algorithm", None)
    )
    if key_version != envelope.key_version:
        raise PolicyAttestorError(
            "signer key version does not match reserved decision"
        )
    if not isinstance(signing_trust, PolicySigningTrust):
        raise PolicyAttestorError(
            "signing_trust must be PolicySigningTrust"
        )
    signing_trust.authorize(
        key_version=key_version,
        algorithm=algorithm,
    )

    evidence_request = policy_snapshot.evidence_request(envelope)
    if evidence_request.filter_digest != reservation.evidence_filter_digest:
        raise PolicyAttestorError(
            "reserved evidence filter does not match policy snapshot"
        )
    try:
        closure = compute_policy_evidence_closure(
            registry,
            evidence_request,
        )
    except Exception as exc:
        raise PolicyAttestorError(
            "independent evidence closure failed"
        ) from exc
    if (
        closure.subject_asset_version_id
        != envelope.subject_asset_version_id
        or closure.environment_fingerprint
        != envelope.environment_fingerprint
        or closure.read_time != envelope.firestore_read_time
        or closure.filter_digest != reservation.evidence_filter_digest
        or closure.closure_digest != envelope.evidence_closure_digest
        or _evidence_digests(closure) != envelope.evidence_digests
    ):
        raise PolicyAttestorError(
            "independent evidence closure does not match reservation"
        )
    if envelope.decision is PolicyDecision.APPROVED and not closure.approvable:
        raise PolicyAttestorError(
            "approval evidence closure is not approvable"
        )

    canonical = canonicalize_json(envelope.to_jcs_dict())
    digest = hashlib.sha256(canonical).digest()
    if TypedId.from_bare(digest.hex()) != request.unsigned_envelope_digest:
        raise PolicyAttestorError(
            "reserved unsigned envelope digest is invalid"
        )
    try:
        signature = signer.sign_sha256_digest(digest)
    except Exception as exc:
        raise PolicyAttestorError("policy decision signing failed") from exc
    if not isinstance(signature, bytes) or not signature:
        raise PolicyAttestorError(
            "policy decision signer returned an invalid signature"
        )
    signed = SignedPolicyDecision(
        unsigned_envelope=envelope,
        unsigned_envelope_digest=request.unsigned_envelope_digest,
        attestation_id=request.unsigned_envelope_digest,
        key_version=key_version,
        algorithm=algorithm,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )
    return PolicyAttestorCompletion(
        operation_id=reservation.operation_id,
        request_fingerprint=reservation.request_fingerprint,
        fencing_token=reservation.fencing_token,
        signed_decision=signed,
    )

"""Cluster-side image admission contract for GKE Binary Authorization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

from aigear.management.v2.attestation import (
    AttestationRecord,
    AttestationVerifier,
    DigestSigner,
    create_attestation,
    verify_attestation,
)
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "AdmissionDecision",
    "AdmissionImage",
    "BreakGlassGrant",
    "ImageAdmissionError",
    "ImageAdmissionPolicy",
    "create_break_glass_attestation",
    "evaluate_image_admission",
]


class ImageAdmissionError(ValueError):
    pass


def _text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImageAdmissionError(f"{field} must be non-empty")
    return value


def _time(field: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImageAdmissionError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImageAdmissionError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class AdmissionImage:
    image_reference: str
    image_digest: TypedId
    verified_attestors: tuple[str, ...]

    def __post_init__(self) -> None:
        _text("image_reference", self.image_reference)
        if not isinstance(self.image_digest, TypedId):
            raise ImageAdmissionError("image_digest must be a TypedId")
        if self.image_reference.count("@") != 1:
            raise ImageAdmissionError("image reference must be digest pinned")
        repository, digest = self.image_reference.split("@", 1)
        if not repository or digest != self.image_digest.typed:
            raise ImageAdmissionError("image reference must match image digest")
        attestors = tuple(self.verified_attestors)
        if not all(isinstance(value, str) and value for value in attestors):
            raise ImageAdmissionError("verified_attestors must be non-empty strings")
        if attestors != tuple(sorted(set(attestors))):
            raise ImageAdmissionError("verified_attestors must be sorted and unique")
        object.__setattr__(self, "verified_attestors", attestors)


@dataclass(frozen=True)
class ImageAdmissionPolicy:
    schema_version: str
    policy_id: str
    environment_fingerprint: TypedId
    project_id: str
    cluster_specifier: str
    required_attestors: tuple[str, ...]
    break_glass_key_versions: tuple[str, ...]
    max_break_glass_ttl_seconds: int

    def __post_init__(self) -> None:
        if self.schema_version != "2.0":
            raise ImageAdmissionError("admission policy schema_version must be 2.0")
        for field in ("policy_id", "project_id", "cluster_specifier"):
            _text(field, getattr(self, field))
        if not isinstance(self.environment_fingerprint, TypedId):
            raise ImageAdmissionError("environment_fingerprint must be a TypedId")
        attestors = tuple(self.required_attestors)
        if len(attestors) < 2 or attestors != tuple(sorted(set(attestors))):
            raise ImageAdmissionError(
                "required_attestors must contain sorted unique builder and security attestors"
            )
        expected_prefix = f"projects/{self.project_id}/attestors/"
        if not all(value.startswith(expected_prefix) for value in attestors):
            raise ImageAdmissionError("required attestors must belong to the policy project")
        keys = tuple(self.break_glass_key_versions)
        if not keys or keys != tuple(sorted(set(keys))):
            raise ImageAdmissionError("break-glass key versions must be sorted and unique")
        if (
            isinstance(self.max_break_glass_ttl_seconds, bool)
            or not isinstance(self.max_break_glass_ttl_seconds, int)
            or not 1 <= self.max_break_glass_ttl_seconds <= 3600
        ):
            raise ImageAdmissionError("break-glass TTL must be in [1, 3600] seconds")
        object.__setattr__(self, "required_attestors", attestors)
        object.__setattr__(self, "break_glass_key_versions", keys)

    def to_binary_authorization_policy(self) -> dict:
        rule = {
            "evaluationMode": "REQUIRE_ATTESTATION",
            "enforcementMode": "ENFORCED_BLOCK_AND_AUDIT_LOG",
            "requireAttestationsBy": list(self.required_attestors),
        }
        return {
            "name": f"projects/{self.project_id}/policy",
            "globalPolicyEvaluationMode": "ENABLE",
            "defaultAdmissionRule": dict(rule),
            "clusterAdmissionRules": {self.cluster_specifier: dict(rule)},
        }


@dataclass(frozen=True)
class BreakGlassGrant:
    policy_id: str
    image_digest: TypedId
    ticket_id: str
    owner: str
    reason: str
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        for field in ("policy_id", "ticket_id", "owner", "reason"):
            _text(field, getattr(self, field))
        if not isinstance(self.image_digest, TypedId):
            raise ImageAdmissionError("image_digest must be a TypedId")
        if _time("expires_at", self.expires_at) <= _time("issued_at", self.issued_at):
            raise ImageAdmissionError("break-glass expiry must follow issuance")

    def to_jcs_dict(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "image_digest": self.image_digest.typed,
            "ticket_id": self.ticket_id,
            "owner": self.owner,
            "reason": self.reason,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BreakGlassGrant":
        expected = {
            "policy_id",
            "image_digest",
            "ticket_id",
            "owner",
            "reason",
            "issued_at",
            "expires_at",
        }
        if set(value) != expected:
            raise ImageAdmissionError("break-glass grant fields are invalid")
        try:
            return cls(
                policy_id=value["policy_id"],
                image_digest=TypedId.from_typed(value["image_digest"]),
                ticket_id=value["ticket_id"],
                owner=value["owner"],
                reason=value["reason"],
                issued_at=value["issued_at"],
                expires_at=value["expires_at"],
            )
        except (TypeError, ValueError) as exc:
            raise ImageAdmissionError("break-glass grant is invalid") from exc


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    reason: str
    audit_fields: Mapping[str, str]


def create_break_glass_attestation(
    grant: BreakGlassGrant,
    *,
    environment_fingerprint: TypedId,
    signer: DigestSigner,
) -> AttestationRecord:
    if not isinstance(grant, BreakGlassGrant):
        raise ImageAdmissionError("grant must be a BreakGlassGrant")
    return create_attestation(
        schema_version="2.0",
        attestation_kind="image_admission_break_glass",
        environment_fingerprint=environment_fingerprint,
        subject=grant.to_jcs_dict(),
        signer=signer,
    )


def _valid_break_glass(
    policy: ImageAdmissionPolicy,
    image: AdmissionImage,
    attestation: AttestationRecord | None,
    *,
    verifier: AttestationVerifier,
    now: datetime,
) -> BreakGlassGrant | None:
    if attestation is None:
        return None
    if (
        attestation.attestation_kind != "image_admission_break_glass"
        or attestation.environment_fingerprint != policy.environment_fingerprint
        or attestation.key_version not in policy.break_glass_key_versions
    ):
        return None
    verify_attestation(attestation, verifier)
    subject = attestation.unsigned_envelope.get("subject")
    if not isinstance(subject, Mapping):
        raise ImageAdmissionError("break-glass subject is invalid")
    grant = BreakGlassGrant.from_dict(subject)
    now_utc = _time("now", now.isoformat())
    issued = _time("issued_at", grant.issued_at)
    expires = _time("expires_at", grant.expires_at)
    if (
        grant.policy_id != policy.policy_id
        or grant.image_digest != image.image_digest
        or now_utc < issued
        or now_utc >= expires
        or (expires - issued).total_seconds() > policy.max_break_glass_ttl_seconds
    ):
        return None
    return grant


def evaluate_image_admission(
    policy: ImageAdmissionPolicy,
    images: Sequence[AdmissionImage],
    *,
    break_glass_attestation: AttestationRecord | None,
    break_glass_verifier: AttestationVerifier,
    now: datetime,
) -> AdmissionDecision:
    if not isinstance(policy, ImageAdmissionPolicy) or not images:
        raise ImageAdmissionError("policy and at least one image are required")
    required = frozenset(policy.required_attestors)
    break_glass_grant = None
    for image in images:
        if not isinstance(image, AdmissionImage):
            raise ImageAdmissionError("images must contain AdmissionImage values")
        if required.issubset(image.verified_attestors):
            continue
        grant = _valid_break_glass(
            policy,
            image,
            break_glass_attestation,
            verifier=break_glass_verifier,
            now=now,
        )
        if grant is None:
            return AdmissionDecision(False, "required image attestations are missing", {})
        break_glass_grant = grant
    if break_glass_grant is not None:
        return AdmissionDecision(
            True,
            "signed break-glass grant",
            {
                "ticket_id": break_glass_grant.ticket_id,
                "owner": break_glass_grant.owner,
                "reason": break_glass_grant.reason,
                "expires_at": break_glass_grant.expires_at,
                "image_digest": break_glass_grant.image_digest.typed,
                "grant_attestation_id": break_glass_attestation.attestation_id.typed,
            },
        )
    return AdmissionDecision(True, "all required image attestations verified", {})

"""Signed image provenance and current vulnerability/license policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from aigear.management.v2.attestation import (
    AttestationRecord,
    AttestationVerifier,
    DigestSigner,
    create_attestation,
    verify_attestation,
)
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_manifest import ReleaseImageBinding

__all__ = [
    "HighSeverityException",
    "ImagePolicyError",
    "ImageSupplyChainBundle",
    "create_image_supply_chain_attestation",
    "verify_image_supply_chain",
]


class ImagePolicyError(ValueError):
    pass


def _text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImagePolicyError(f"{field} must be non-empty")
    return value


def _typed(field: str, value: object) -> TypedId:
    if not isinstance(value, TypedId):
        raise ImagePolicyError(f"{field} must be a TypedId")
    return value


def _count(field: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImagePolicyError(f"{field} must be a non-negative int")
    return value


def _time(field: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImagePolicyError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImagePolicyError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class HighSeverityException:
    owner: str
    reason: str
    expires_at: str

    def __post_init__(self) -> None:
        _text("owner", self.owner)
        _text("reason", self.reason)
        _time("expires_at", self.expires_at)

    def to_jcs_dict(self) -> dict:
        return {
            "owner": self.owner,
            "reason": self.reason,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HighSeverityException":
        if set(value) != {"owner", "reason", "expires_at"}:
            raise ImagePolicyError("high severity exception fields are invalid")
        return cls(value["owner"], value["reason"], value["expires_at"])


@dataclass(frozen=True)
class ImageSupplyChainBundle:
    image_digest: TypedId
    builder_id: str
    source_revision: str
    build_config_digest: TypedId
    materials_digest: TypedId
    sbom_digest: TypedId
    image_signature_digest: TypedId
    vulnerability_scan_digest: TypedId
    critical_count: int
    high_count: int
    license_decision_digest: TypedId
    license_approved: bool
    generated_at: str
    high_severity_exception: HighSeverityException | None = None

    def __post_init__(self) -> None:
        for field in (
            "image_digest",
            "build_config_digest",
            "materials_digest",
            "sbom_digest",
            "image_signature_digest",
            "vulnerability_scan_digest",
            "license_decision_digest",
        ):
            _typed(field, getattr(self, field))
        _text("builder_id", self.builder_id)
        _text("source_revision", self.source_revision)
        _count("critical_count", self.critical_count)
        _count("high_count", self.high_count)
        if not isinstance(self.license_approved, bool):
            raise ImagePolicyError("license_approved must be a bool")
        _time("generated_at", self.generated_at)
        if self.high_severity_exception is not None and not isinstance(
            self.high_severity_exception, HighSeverityException
        ):
            raise ImagePolicyError(
                "high_severity_exception must be a HighSeverityException"
            )

    def to_jcs_dict(self) -> dict:
        return {
            "image_digest": self.image_digest.typed,
            "builder_id": self.builder_id,
            "source_revision": self.source_revision,
            "build_config_digest": self.build_config_digest.typed,
            "materials_digest": self.materials_digest.typed,
            "sbom_digest": self.sbom_digest.typed,
            "image_signature_digest": self.image_signature_digest.typed,
            "vulnerability_scan_digest": self.vulnerability_scan_digest.typed,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "license_decision_digest": self.license_decision_digest.typed,
            "license_approved": self.license_approved,
            "generated_at": self.generated_at,
            "high_severity_exception": (
                None
                if self.high_severity_exception is None
                else self.high_severity_exception.to_jcs_dict()
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ImageSupplyChainBundle":
        expected = {
            "image_digest",
            "builder_id",
            "source_revision",
            "build_config_digest",
            "materials_digest",
            "sbom_digest",
            "image_signature_digest",
            "vulnerability_scan_digest",
            "critical_count",
            "high_count",
            "license_decision_digest",
            "license_approved",
            "generated_at",
            "high_severity_exception",
        }
        if set(value) != expected:
            raise ImagePolicyError("image supply-chain evidence fields are invalid")
        exception = value["high_severity_exception"]
        if exception is not None:
            if not isinstance(exception, Mapping):
                raise ImagePolicyError("high severity exception must be a mapping")
            exception = HighSeverityException.from_dict(exception)
        try:
            return cls(
                image_digest=TypedId.from_typed(value["image_digest"]),
                builder_id=value["builder_id"],
                source_revision=value["source_revision"],
                build_config_digest=TypedId.from_typed(value["build_config_digest"]),
                materials_digest=TypedId.from_typed(value["materials_digest"]),
                sbom_digest=TypedId.from_typed(value["sbom_digest"]),
                image_signature_digest=TypedId.from_typed(
                    value["image_signature_digest"]
                ),
                vulnerability_scan_digest=TypedId.from_typed(
                    value["vulnerability_scan_digest"]
                ),
                critical_count=value["critical_count"],
                high_count=value["high_count"],
                license_decision_digest=TypedId.from_typed(
                    value["license_decision_digest"]
                ),
                license_approved=value["license_approved"],
                generated_at=value["generated_at"],
                high_severity_exception=exception,
            )
        except (TypeError, ValueError) as exc:
            raise ImagePolicyError("image supply-chain evidence is invalid") from exc


def create_image_supply_chain_attestation(
    bundle: ImageSupplyChainBundle,
    *,
    environment_fingerprint: TypedId,
    signer: DigestSigner,
) -> AttestationRecord:
    if not isinstance(bundle, ImageSupplyChainBundle):
        raise ImagePolicyError("bundle must be an ImageSupplyChainBundle")
    return create_attestation(
        schema_version="2.0",
        attestation_kind="image_supply_chain",
        environment_fingerprint=environment_fingerprint,
        subject=bundle.to_jcs_dict(),
        signer=signer,
    )


def verify_image_supply_chain(
    image: ReleaseImageBinding,
    attestation: AttestationRecord,
    *,
    verifier: AttestationVerifier,
    expected_environment_fingerprint: TypedId,
    allowed_key_versions: Sequence[str],
    allowed_builder_ids: Sequence[str],
    verify_image_signature: Callable[[TypedId, TypedId], None],
    now: datetime,
    max_evidence_age_seconds: int,
) -> ImageSupplyChainBundle:
    if not isinstance(image, ReleaseImageBinding):
        raise ImagePolicyError("image must be a ReleaseImageBinding")
    if (
        not isinstance(attestation, AttestationRecord)
        or attestation.attestation_kind != "image_supply_chain"
        or attestation.attestation_id != image.provenance_attestation_id
        or attestation.environment_fingerprint != expected_environment_fingerprint
    ):
        raise ImagePolicyError("image supply-chain attestation is missing or mismatched")
    if attestation.key_version not in frozenset(allowed_key_versions):
        raise ImagePolicyError("image attestation key version is not allowlisted")
    verify_attestation(attestation, verifier)
    subject = attestation.unsigned_envelope.get("subject")
    if not isinstance(subject, Mapping):
        raise ImagePolicyError("image supply-chain subject is invalid")
    bundle = ImageSupplyChainBundle.from_dict(subject)
    if bundle.image_digest != image.image_digest or bundle.sbom_digest != image.sbom_digest:
        raise ImagePolicyError("image digest or SBOM digest does not match release")
    if bundle.builder_id not in frozenset(allowed_builder_ids):
        raise ImagePolicyError("image builder is not allowlisted")
    if not callable(verify_image_signature):
        raise ImagePolicyError("image signature verifier is required")
    verify_image_signature(bundle.image_digest, bundle.image_signature_digest)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ImagePolicyError("verification time must be timezone-aware")
    if (
        isinstance(max_evidence_age_seconds, bool)
        or not isinstance(max_evidence_age_seconds, int)
        or max_evidence_age_seconds < 1
    ):
        raise ImagePolicyError("max evidence age must be a positive int")
    verification_time = now.astimezone(timezone.utc)
    generated_at = _time("generated_at", bundle.generated_at)
    age_seconds = (verification_time - generated_at).total_seconds()
    if age_seconds < 0 or age_seconds > max_evidence_age_seconds:
        raise ImagePolicyError("image security evidence is stale or from the future")
    if bundle.critical_count:
        raise ImagePolicyError("Critical vulnerabilities are not allowed")
    if bundle.high_count:
        exception = bundle.high_severity_exception
        if exception is None or _time("expires_at", exception.expires_at) <= verification_time:
            raise ImagePolicyError("High vulnerabilities require a current exception")
    if not bundle.license_approved:
        raise ImagePolicyError("image license decision is not approved")
    return bundle

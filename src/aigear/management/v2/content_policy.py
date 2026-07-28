"""Fail-closed content inspection for quarantined external imports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Protocol, Tuple

from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_executor import ImportExecutorCompletion

__all__ = [
    "ContentPolicyError",
    "ScanVerdict",
    "FormatRule",
    "ImportContentPolicy",
    "ContentGovernance",
    "ProducerEvidence",
    "ScannerResult",
    "ContentScanner",
    "InspectionPayloadEvidence",
    "ContentInspectionEvidence",
    "create_scanner_result",
    "compute_governance_digest",
    "inspect_import_content",
]

_PICKLE_SUFFIXES = frozenset({".pkl", ".pickle", ".joblib", ".cloudpickle"})
_PICKLE_MEDIA_TYPES = frozenset(
    {
        "application/python-pickle",
        "application/x-pickle",
        "application/x-joblib",
    }
)


class ContentPolicyError(ValueError):
    pass


class ScanVerdict(str, Enum):
    CLEAN = "clean"
    BLOCKED = "blocked"
    ERROR = "error"


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ContentPolicyError(f"{field_name} must be a non-empty str")


def _aware(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ContentPolicyError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContentPolicyError(f"{field_name} must be timezone-aware")
    return parsed


def _tuple(field_name: str, value: object) -> tuple:
    if isinstance(value, list):
        value = tuple(value)
    if not isinstance(value, tuple) or not value:
        raise ContentPolicyError(f"{field_name} must be a non-empty tuple")
    return value


@dataclass(frozen=True)
class FormatRule:
    format_name: str
    media_types: Tuple[str, ...]
    file_suffixes: Tuple[str, ...]
    max_size_bytes: int

    def __post_init__(self) -> None:
        _non_empty("format_name", self.format_name)
        object.__setattr__(self, "media_types", _tuple("media_types", self.media_types))
        object.__setattr__(
            self, "file_suffixes", _tuple("file_suffixes", self.file_suffixes)
        )
        if not all(
            isinstance(value, str) and value and value == value.lower()
            for value in self.media_types
        ):
            raise ContentPolicyError("media_types must contain lowercase strings")
        if not all(
            isinstance(value, str)
            and value.startswith(".")
            and value == value.lower()
            for value in self.file_suffixes
        ):
            raise ContentPolicyError(
                "file_suffixes must contain lowercase dot-prefixed strings"
            )
        if (
            isinstance(self.max_size_bytes, bool)
            or not isinstance(self.max_size_bytes, int)
            or self.max_size_bytes < 1
        ):
            raise ContentPolicyError("max_size_bytes must be a positive int")

    def canonical_dict(self) -> dict:
        return {
            "format_name": self.format_name,
            "media_types": list(self.media_types),
            "file_suffixes": list(self.file_suffixes),
            "max_size_bytes": self.max_size_bytes,
        }


@dataclass(frozen=True)
class ImportContentPolicy:
    policy_version: str
    format_rules: Tuple[FormatRule, ...]
    allowed_scanners: Tuple[Tuple[str, str], ...]
    allowed_producers: Tuple[str, ...]
    required_logical_paths: Tuple[Tuple[str, str], ...] = ()
    allow_external_pickle: bool = False

    def __post_init__(self) -> None:
        _non_empty("policy_version", self.policy_version)
        object.__setattr__(
            self, "format_rules", _tuple("format_rules", self.format_rules)
        )
        object.__setattr__(
            self, "allowed_scanners", _tuple("allowed_scanners", self.allowed_scanners)
        )
        object.__setattr__(
            self, "allowed_producers", _tuple("allowed_producers", self.allowed_producers)
        )
        if not all(isinstance(value, FormatRule) for value in self.format_rules):
            raise ContentPolicyError("format_rules must contain FormatRule values")
        names = [value.format_name for value in self.format_rules]
        if len(set(names)) != len(names):
            raise ContentPolicyError("format rule names must be unique")
        if not all(
            isinstance(value, tuple)
            and len(value) == 2
            and all(isinstance(part, str) and part for part in value)
            for value in self.allowed_scanners
        ):
            raise ContentPolicyError(
                "allowed_scanners must contain (scanner_id, version) tuples"
            )
        if len(set(self.allowed_scanners)) != len(self.allowed_scanners):
            raise ContentPolicyError("allowed_scanners must be unique")
        if not all(isinstance(value, str) and value for value in self.allowed_producers):
            raise ContentPolicyError("allowed_producers must contain non-empty strings")
        if len(set(self.allowed_producers)) != len(self.allowed_producers):
            raise ContentPolicyError("allowed_producers must be unique")
        if not all(
            isinstance(value, tuple)
            and len(value) == 2
            and value[0] in ("components", "attachments")
            and all(isinstance(part, str) and part for part in value)
            for value in self.required_logical_paths
        ):
            raise ContentPolicyError(
                "required_logical_paths must contain typed logical paths"
            )
        if len(set(self.required_logical_paths)) != len(self.required_logical_paths):
            raise ContentPolicyError("required_logical_paths must be unique")
        if not isinstance(self.allow_external_pickle, bool):
            raise ContentPolicyError("allow_external_pickle must be bool")

    @property
    def digest(self) -> TypedId:
        value = {
            "domain": "aigear.import-content-policy.v2",
            "policy_version": self.policy_version,
            "format_rules": [rule.canonical_dict() for rule in self.format_rules],
            "allowed_scanners": [list(value) for value in self.allowed_scanners],
            "allowed_producers": list(self.allowed_producers),
            "required_logical_paths": [
                list(value) for value in self.required_logical_paths
            ],
            "allow_external_pickle": self.allow_external_pickle,
        }
        return TypedId.from_bare(hashlib.sha256(canonicalize_json(value)).hexdigest())


@dataclass(frozen=True)
class ContentGovernance:
    owner: str
    data_classification: str
    purpose: str
    license_or_consent_ref: str
    residency: str
    retention_class: str
    legal_hold: bool
    policy_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "owner",
            "data_classification",
            "purpose",
            "license_or_consent_ref",
            "residency",
            "retention_class",
            "policy_version",
        ):
            _non_empty(field_name, getattr(self, field_name))
        if not isinstance(self.legal_hold, bool):
            raise ContentPolicyError("legal_hold must be bool")

    def canonical_dict(self) -> dict:
        return {
            "owner": self.owner,
            "data_classification": self.data_classification,
            "purpose": self.purpose,
            "license_or_consent_ref": self.license_or_consent_ref,
            "residency": self.residency,
            "retention_class": self.retention_class,
            "legal_hold": self.legal_hold,
            "policy_version": self.policy_version,
        }


def compute_governance_digest(governance: ContentGovernance) -> TypedId:
    if not isinstance(governance, ContentGovernance):
        raise ContentPolicyError("governance must be ContentGovernance")
    return TypedId.from_bare(
        hashlib.sha256(
            canonicalize_json(
                {
                    "domain": "aigear.content-governance.v2",
                    **governance.canonical_dict(),
                }
            )
        ).hexdigest()
    )


@dataclass(frozen=True)
class ProducerEvidence:
    producer_id: str
    evidence_digest: TypedId
    verified: bool

    def __post_init__(self) -> None:
        _non_empty("producer_id", self.producer_id)
        if not isinstance(self.evidence_digest, TypedId):
            raise ContentPolicyError("evidence_digest must be a TypedId")
        if not isinstance(self.verified, bool):
            raise ContentPolicyError("verified must be bool")


@dataclass(frozen=True)
class ScannerResult:
    scanner_id: str
    scanner_version: str
    payload_sha256: str
    verdict: ScanVerdict
    finding_codes: Tuple[str, ...]
    completed_at: str
    report_digest: TypedId

    def __post_init__(self) -> None:
        for field_name in ("scanner_id", "scanner_version", "payload_sha256"):
            _non_empty(field_name, getattr(self, field_name))
        if (
            len(self.payload_sha256) != 64
            or self.payload_sha256 != self.payload_sha256.lower()
            or any(value not in "0123456789abcdef" for value in self.payload_sha256)
        ):
            raise ContentPolicyError("payload_sha256 must be lowercase SHA-256 hex")
        if not isinstance(self.verdict, ScanVerdict):
            raise ContentPolicyError("verdict must be a ScanVerdict")
        if isinstance(self.finding_codes, list):
            object.__setattr__(self, "finding_codes", tuple(self.finding_codes))
        if not all(
            isinstance(value, str) and value and len(value) <= 128
            for value in self.finding_codes
        ):
            raise ContentPolicyError("finding_codes must contain bounded rule IDs")
        _aware("completed_at", self.completed_at)
        if self.report_digest != _scanner_result_digest(
            scanner_id=self.scanner_id,
            scanner_version=self.scanner_version,
            payload_sha256=self.payload_sha256,
            verdict=self.verdict,
            finding_codes=self.finding_codes,
            completed_at=self.completed_at,
        ):
            raise ContentPolicyError("scanner report_digest does not match result")


class ContentScanner(Protocol):
    scanner_id: str
    scanner_version: str

    def scan(
        self, data: bytes, *, media_type: str, expected_sha256: str
    ) -> ScannerResult: ...


@dataclass(frozen=True)
class InspectionPayloadEvidence:
    payload_kind: str
    payload_key: str
    quarantine_object_name: str
    quarantine_generation: str
    sha256: str
    size_bytes: int
    format_name: str
    scanner_id: str
    scanner_version: str
    scanner_report_digest: TypedId

    def canonical_dict(self) -> dict:
        return {
            "payload_kind": self.payload_kind,
            "payload_key": self.payload_key,
            "quarantine_object_name": self.quarantine_object_name,
            "quarantine_generation": self.quarantine_generation,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "format_name": self.format_name,
            "scanner_id": self.scanner_id,
            "scanner_version": self.scanner_version,
            "scanner_report_digest": self.scanner_report_digest.typed,
        }


@dataclass(frozen=True)
class ContentInspectionEvidence:
    operation_id: str
    ticket_digest: TypedId
    environment_fingerprint: TypedId
    fencing_token: int
    payload_set_digest: TypedId
    policy_digest: TypedId
    producer_evidence_digest: TypedId
    governance_digest: TypedId
    payloads: Tuple[InspectionPayloadEvidence, ...]
    inspected_at: str
    evidence_digest: TypedId

    def __post_init__(self) -> None:
        _aware("inspected_at", self.inspected_at)
        if not self.payloads:
            raise ContentPolicyError("inspection evidence requires payloads")
        if self.evidence_digest != _inspection_digest(
            operation_id=self.operation_id,
            ticket_digest=self.ticket_digest,
            environment_fingerprint=self.environment_fingerprint,
            fencing_token=self.fencing_token,
            payload_set_digest=self.payload_set_digest,
            policy_digest=self.policy_digest,
            producer_evidence_digest=self.producer_evidence_digest,
            governance_digest=self.governance_digest,
            payloads=self.payloads,
            inspected_at=self.inspected_at,
        ):
            raise ContentPolicyError("inspection evidence_digest does not match fields")


def _scanner_result_digest(
    *,
    scanner_id: str,
    scanner_version: str,
    payload_sha256: str,
    verdict: ScanVerdict,
    finding_codes: Tuple[str, ...],
    completed_at: str,
) -> TypedId:
    value = {
        "domain": "aigear.content-scan-report.v2",
        "scanner_id": scanner_id,
        "scanner_version": scanner_version,
        "payload_sha256": payload_sha256,
        "verdict": verdict.value,
        "finding_codes": list(finding_codes),
        "completed_at": completed_at,
    }
    return TypedId.from_bare(hashlib.sha256(canonicalize_json(value)).hexdigest())


def create_scanner_result(
    *,
    scanner_id: str,
    scanner_version: str,
    payload_sha256: str,
    verdict: ScanVerdict,
    finding_codes: Tuple[str, ...],
    completed_at: str,
) -> ScannerResult:
    digest = _scanner_result_digest(
        scanner_id=scanner_id,
        scanner_version=scanner_version,
        payload_sha256=payload_sha256,
        verdict=verdict,
        finding_codes=tuple(finding_codes),
        completed_at=completed_at,
    )
    return ScannerResult(
        scanner_id=scanner_id,
        scanner_version=scanner_version,
        payload_sha256=payload_sha256,
        verdict=verdict,
        finding_codes=tuple(finding_codes),
        completed_at=completed_at,
        report_digest=digest,
    )


def _inspection_digest(
    *,
    operation_id: str,
    ticket_digest: TypedId,
    environment_fingerprint: TypedId,
    fencing_token: int,
    payload_set_digest: TypedId,
    policy_digest: TypedId,
    producer_evidence_digest: TypedId,
    governance_digest: TypedId,
    payloads: Tuple[InspectionPayloadEvidence, ...],
    inspected_at: str,
) -> TypedId:
    value = {
        "domain": "aigear.content-inspection.v2",
        "operation_id": operation_id,
        "ticket_digest": ticket_digest.typed,
        "environment_fingerprint": environment_fingerprint.typed,
        "fencing_token": fencing_token,
        "payload_set_digest": payload_set_digest.typed,
        "policy_digest": policy_digest.typed,
        "producer_evidence_digest": producer_evidence_digest.typed,
        "governance_digest": governance_digest.typed,
        "payloads": [value.canonical_dict() for value in payloads],
        "inspected_at": inspected_at,
    }
    return TypedId.from_bare(hashlib.sha256(canonicalize_json(value)).hexdigest())


def _format_rule(
    *, file_name: str, media_type: str, size_bytes: int, policy: ImportContentPolicy
) -> FormatRule:
    suffix = PurePosixPath(file_name).suffix.lower()
    if (
        suffix in _PICKLE_SUFFIXES or media_type.lower() in _PICKLE_MEDIA_TYPES
    ) and not policy.allow_external_pickle:
        raise ContentPolicyError("external pickle/joblib/cloudpickle is forbidden")
    matches = [
        rule
        for rule in policy.format_rules
        if media_type in rule.media_types and suffix in rule.file_suffixes
    ]
    if len(matches) != 1:
        raise ContentPolicyError("payload format is unknown or ambiguous")
    if size_bytes > matches[0].max_size_bytes:
        raise ContentPolicyError("payload exceeds its format size limit")
    return matches[0]


def inspect_import_content(
    completion: ImportExecutorCompletion,
    *,
    quarantine_gcs: GcsClientV2,
    policy: ImportContentPolicy,
    governance: ContentGovernance,
    producer: ProducerEvidence,
    scanner: ContentScanner,
    inspected_at: str,
) -> ContentInspectionEvidence:
    """Exact-read and scan every payload; return digest-only accepted evidence."""

    if not isinstance(completion, ImportExecutorCompletion):
        raise ContentPolicyError("completion must be an ImportExecutorCompletion")
    if not isinstance(policy, ImportContentPolicy):
        raise ContentPolicyError("policy must be an ImportContentPolicy")
    if not isinstance(governance, ContentGovernance):
        raise ContentPolicyError("governance must be ContentGovernance")
    if governance.policy_version != policy.policy_version:
        raise ContentPolicyError("governance policy_version mismatch")
    if (
        not isinstance(producer, ProducerEvidence)
        or not producer.verified
        or producer.producer_id not in policy.allowed_producers
    ):
        raise ContentPolicyError("producer evidence is missing or not allowed")
    inspected_instant = _aware("inspected_at", inspected_at)
    scanner_identity = (
        getattr(scanner, "scanner_id", None),
        getattr(scanner, "scanner_version", None),
    )
    if scanner_identity not in policy.allowed_scanners:
        raise ContentPolicyError("configured content scanner is not allowlisted")

    logical_paths = {
        (value.payload_kind, value.payload_key) for value in completion.payloads
    }
    missing = set(policy.required_logical_paths) - logical_paths
    if missing:
        raise ContentPolicyError(
            f"required schema/content payloads are missing: {sorted(missing)!r}"
        )

    accepted = []
    for descriptor in completion.payloads:
        try:
            snapshot = quarantine_gcs.get_object(
                descriptor.object_name, generation=descriptor.generation
            )
        except Exception as exc:
            raise ContentPolicyError("exact quarantine payload is unavailable") from exc
        digest = hashlib.sha256(snapshot.data).hexdigest()
        if (
            snapshot.object_name != descriptor.object_name
            or snapshot.generation != descriptor.generation
            or snapshot.size_bytes != descriptor.size_bytes
            or snapshot.sha256 != descriptor.sha256
            or snapshot.crc32c != descriptor.crc32c
            or digest != descriptor.sha256
        ):
            raise ContentPolicyError("quarantine payload integrity mismatch")
        if snapshot.data.startswith(b"\x80") and not policy.allow_external_pickle:
            raise ContentPolicyError("pickle byte signature is forbidden")
        rule = _format_rule(
            file_name=descriptor.file_name,
            media_type=descriptor.media_type,
            size_bytes=descriptor.size_bytes,
            policy=policy,
        )
        try:
            result = scanner.scan(
                snapshot.data,
                media_type=descriptor.media_type,
                expected_sha256=descriptor.sha256,
            )
        except Exception as exc:
            raise ContentPolicyError("content scanner failed or timed out") from exc
        if not isinstance(result, ScannerResult):
            raise ContentPolicyError("content scanner returned unknown evidence")
        if (
            (result.scanner_id, result.scanner_version) != scanner_identity
            or result.payload_sha256 != descriptor.sha256
            or result.verdict is not ScanVerdict.CLEAN
            or _aware("scanner.completed_at", result.completed_at) > inspected_instant
        ):
            raise ContentPolicyError("content scanner did not return an allowed clean result")
        accepted.append(
            InspectionPayloadEvidence(
                payload_kind=descriptor.payload_kind,
                payload_key=descriptor.payload_key,
                quarantine_object_name=descriptor.object_name,
                quarantine_generation=descriptor.generation,
                sha256=descriptor.sha256,
                size_bytes=descriptor.size_bytes,
                format_name=rule.format_name,
                scanner_id=result.scanner_id,
                scanner_version=result.scanner_version,
                scanner_report_digest=result.report_digest,
            )
        )

    governance_digest = compute_governance_digest(governance)
    values = {
        "operation_id": completion.operation_id,
        "ticket_digest": completion.ticket_digest,
        "environment_fingerprint": completion.environment_fingerprint,
        "fencing_token": completion.fencing_token,
        "payload_set_digest": completion.payload_set_digest,
        "policy_digest": policy.digest,
        "producer_evidence_digest": producer.evidence_digest,
        "governance_digest": governance_digest,
        "payloads": tuple(accepted),
        "inspected_at": inspected_at,
    }
    digest = _inspection_digest(**values)
    return ContentInspectionEvidence(**values, evidence_digest=digest)

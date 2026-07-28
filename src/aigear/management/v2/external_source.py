"""Strict parser and allowlist policy for exact external GCS sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple
from urllib.parse import urlsplit

from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.import_operation import ExactImportSource

__all__ = [
    "ExternalSourceError",
    "AllowedExternalBucket",
    "ExternalSourcePolicy",
    "parse_exact_external_source",
]


class ExternalSourceError(ValueError):
    pass


@dataclass(frozen=True)
class AllowedExternalBucket:
    project_id: str
    bucket: str
    region: str

    def __post_init__(self) -> None:
        for field_name in ("project_id", "bucket", "region"):
            object.__setattr__(
                self,
                field_name,
                validate_segment(getattr(self, field_name), field_name=field_name),
            )


@dataclass(frozen=True)
class ExternalSourcePolicy:
    target_environment_id: str
    allowed_buckets: Tuple[AllowedExternalBucket, ...]
    max_size_bytes: int

    def __post_init__(self) -> None:
        if isinstance(self.allowed_buckets, list):
            object.__setattr__(self, "allowed_buckets", tuple(self.allowed_buckets))
        object.__setattr__(
            self,
            "target_environment_id",
            validate_segment(self.target_environment_id, field_name="target_environment_id"),
        )
        if not self.allowed_buckets or not all(
            isinstance(value, AllowedExternalBucket) for value in self.allowed_buckets
        ):
            raise ExternalSourceError(
                "allowed_buckets must contain at least one AllowedExternalBucket"
            )
        bucket_names = [value.bucket for value in self.allowed_buckets]
        if len(set(bucket_names)) != len(bucket_names):
            raise ExternalSourceError("allowed bucket names must be unique")
        if (
            isinstance(self.max_size_bytes, bool)
            or not isinstance(self.max_size_bytes, int)
            or self.max_size_bytes < 1
        ):
            raise ExternalSourceError("max_size_bytes must be a positive int")


def parse_exact_external_source(
    uri: str,
    *,
    policy: ExternalSourcePolicy,
    observed_region: str,
    observed_size_bytes: int,
) -> ExactImportSource:
    if not isinstance(uri, str) or not uri or any(ord(char) > 127 for char in uri):
        raise ExternalSourceError("external source URI must be non-empty ASCII")
    if not isinstance(policy, ExternalSourcePolicy):
        raise ExternalSourceError("policy must be an ExternalSourcePolicy")
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "gs"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ExternalSourceError("external source must use gs://bucket/object#generation")
    if parsed.query:
        raise ExternalSourceError("query credentials and signed URLs are forbidden")
    if not parsed.fragment or not parsed.fragment.isascii() or not parsed.fragment.isdigit():
        raise ExternalSourceError("external source URI must pin a decimal generation")
    if int(parsed.fragment) < 1:
        raise ExternalSourceError("external source generation must be positive")
    if "%" in parsed.path or "\\" in parsed.path:
        raise ExternalSourceError("encoded or backslash object paths are forbidden")
    object_name = parsed.path.removeprefix("/")
    parts = object_name.split("/")
    if (
        not object_name
        or object_name.startswith("/")
        or any(part in ("", ".", "..") for part in parts)
        or any(ord(char) < 0x20 for char in object_name)
    ):
        raise ExternalSourceError("external source object name is ambiguous or unsafe")
    matches = [value for value in policy.allowed_buckets if value.bucket == parsed.netloc]
    if len(matches) != 1:
        raise ExternalSourceError("external source bucket is not allowlisted")
    allowed = matches[0]
    if observed_region != allowed.region:
        raise ExternalSourceError("external source region does not match allowlist")
    if (
        isinstance(observed_size_bytes, bool)
        or not isinstance(observed_size_bytes, int)
        or observed_size_bytes < 0
        or observed_size_bytes > policy.max_size_bytes
    ):
        raise ExternalSourceError("external source size is invalid or exceeds policy")
    return ExactImportSource(
        environment_id=policy.target_environment_id,
        project_id=allowed.project_id,
        bucket=allowed.bucket,
        object_name=object_name,
        generation=parsed.fragment,
        region=allowed.region,
        size_bytes=observed_size_bytes,
    )

"""Path contracts. These helpers never create directories or write storage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from aigear.management.asset_errors import InvalidAssetPath

MANIFEST_NAME = "manifest.json"
RESERVED_PREFIXES = frozenset({"running", "assets", "trash", "backup", "_aigear"})
ARTIFACT_CATEGORIES = frozenset({"dataset", "feature", "training"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I)


def relative_path(value: str) -> str:
    """Validate an object key and normalize only trailing forward slashes.

    Windows aliases are rejected on every platform so a cloud object always has
    an unambiguous representation in the local backend and cache.
    """
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise InvalidAssetPath("Expected a nonempty bucket-relative path")
    if any(ord(c) < 32 or ord(c) == 127 or c in '\\:*?<>|"' for c in value):
        raise InvalidAssetPath("Path contains a URL, drive, wildcard or unsafe character")
    value = value.rstrip("/")
    for part in value.split("/"):
        if not part or part in {".", ".."} or part.endswith((".", " ")) or _WINDOWS_DEVICE.fullmatch(part):
            raise InvalidAssetPath("Path contains an unsafe or ambiguous segment")
    return value


def identifier(value: str) -> str:
    """Validate pipeline/run IDs without changing the existing run ID algorithm."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidAssetPath("Expected a safe single-segment identifier")
    relative_path(value)
    if value.casefold() in RESERVED_PREFIXES | {MANIFEST_NAME}:
        raise InvalidAssetPath("Identifier conflicts with a reserved name")
    return value


def _member_path(value: str) -> str:
    value = relative_path(value)
    parts = value.split("/")
    if parts[0].casefold() in RESERVED_PREFIXES or any(p.casefold() == MANIFEST_NAME for p in parts):
        raise InvalidAssetPath("Path conflicts with a reserved prefix or manifest file")
    return value


def release_path(value: str) -> str:
    return _member_path(value)


def artifact_path(value: str) -> str:
    value = _member_path(value)
    parts = value.split("/")
    if len(parts) < 2 or parts[0] not in ARTIFACT_CATEGORIES:
        raise InvalidAssetPath("Artifact path must include dataset/, feature/ or training/ and a file")
    return value


def validate_file_set(values: Iterable[str]) -> None:
    """Reject file/directory overlaps and aliases in a portable object set."""
    keys = set()
    for value in values:
        key = relative_path(value).casefold()
        if key in keys:
            raise InvalidAssetPath("Duplicate or case-aliased file paths")
        keys.add(key)
    for key in keys:
        parts = key.split("/")
        if any("/".join(parts[:i]) in keys for i in range(1, len(parts))):
            raise InvalidAssetPath("File paths overlap as a file and directory")


def utc_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Time must include a timezone")
    return value.astimezone(timezone.utc)


def snapshot_directory(run_id: str, run_started_at: datetime) -> str:
    started = utc_datetime(run_started_at)
    stamp = f"{started.year:04d}{started.month:02d}{started.day:02d}T{started.hour:02d}{started.minute:02d}{started.second:02d}{started.microsecond:06d}Z"
    return f"{identifier(run_id)}_{stamp}"


def workspace_prefix(pipeline_version: str) -> str:
    return f"running/{identifier(pipeline_version)}"


def snapshot_prefix(location: str, pipeline_version: str, run_id: str, run_started_at: datetime) -> str:
    if location not in {"assets", "trash"}:
        raise InvalidAssetPath("Snapshot location must be assets or trash")
    return f"{location}/{identifier(pipeline_version)}/{snapshot_directory(run_id, run_started_at)}"


def release_prefix(pipeline_version: str, path: Optional[str] = None, *, backup: bool = False) -> str:
    identifier(pipeline_version)
    target = release_path(pipeline_version if path is None else path)
    return f"backup/{target}" if backup else target


def manifest_key(prefix: str) -> str:
    return f"{relative_path(prefix)}/{MANIFEST_NAME}"


def resolve_under(root: Path, key: str) -> Path:
    """Resolve existing symlinks too; lexical containment alone is insufficient."""
    root = Path(root).resolve()
    target = root.joinpath(*relative_path(key).split("/")).resolve()
    if not target.is_relative_to(root) or target == root:
        raise InvalidAssetPath("Resolved path escapes its local root")
    return target


@dataclass(frozen=True)
class LocalAssetRoots:
    bucket: Path
    cache: Path


def local_asset_roots(project_dir: Path, bucket_name: str, pipeline_version: str) -> LocalAssetRoots:
    """Derive separate, contained roots without creating directories."""
    project = Path(project_dir).resolve()
    bucket_name = identifier(bucket_name)
    pipeline_version = identifier(pipeline_version)
    bucket = resolve_under(project, f"asset/buckets/{bucket_name}")
    cache = resolve_under(project, f"asset/cache/{bucket_name}/{pipeline_version}")
    if not bucket.is_relative_to(project / "asset" / "buckets") or not cache.is_relative_to(project / "asset" / "cache"):
        raise InvalidAssetPath("Local roots must stay in their bucket/cache namespaces")
    if bucket.is_relative_to(cache) or cache.is_relative_to(bucket):
        raise InvalidAssetPath("Bucket and cache roots must not overlap")
    return LocalAssetRoots(bucket=bucket, cache=cache)

"""Versioned storage contracts. Generations are decimal strings; condition 0
means create only. None retains legacy unconditional uploads. Copy/rewrite pin
both live source and destination versions, including when conditions are omitted.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from aigear.management.asset_errors import AssetError


class StorageError(AssetError):
    """Storage failure with phase and original cause."""


class ObjectNotFound(StorageError, FileNotFoundError):
    pass


class VersionMismatch(StorageError):
    """The fixed version is no longer the live version."""


class PreconditionFailed(StorageError):
    pass


class StoragePermissionDenied(StorageError, PermissionError):
    pass


@dataclass(frozen=True)
class ObjectMetadata:
    name: str
    generation: str
    metageneration: str
    size: int
    crc32c: str
    custom_time: Optional[datetime] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    content_type: Optional[str] = None


@dataclass(frozen=True)
class BucketLifecycle:
    metageneration: str
    rules: List[Dict[str, Any]]


class BucketABC(ABC):
    """Internal storage protocol, without run ownership or asset registration.

    ``get_metadata`` / ``list_metadata`` return live object descriptors. Reads
    and downloads with ``generation=...`` require that exact live generation;
    old versions never fall back to a newer object. Writes return the committed
    descriptor, including CRC32C in GCS base64 big-endian format.

    For JSON CAS, retain the descriptor from ``write_json(name, value,
    if_generation_match="0")``, then supply its generation on the next write.
    ``read_json`` returns the decoded value; use ``get_metadata`` and a fixed
    read when the caller needs both a value and its version.

    Copy/rewrite accepts another backend instance as ``destination_bucket``;
    source and destination must use the same backend. None Custom-Time creates
    a new object without that field; patching with None leaves it unchanged.
    Delete requires a positive generation, and metadata patches require both
    generation and metageneration. Lifecycle updates replace the supplied full
    rule list under bucket metageneration CAS; callers merge owned rules first.

    Missing objects, fixed-version mismatches, write condition failures and
    permission errors use distinct exceptions above, retaining ``cause`` and
    ``phase``. Neither backend provides a historical-version fallback, multi-
    object transaction, implicit cleanup, nor automatic conflict retry.
    """

    def __init__(self, project_id: str, bucket_name: str):
        self.project_id = project_id
        self.bucket_name = bucket_name
        self._clock = lambda: datetime.now(timezone.utc)

    @abstractmethod
    def close(self):
        pass

    @abstractmethod
    def get_metadata(self, name: str) -> ObjectMetadata:
        pass

    @abstractmethod
    def list_metadata(self, prefix: str = "") -> List[ObjectMetadata]:
        pass

    @abstractmethod
    def read_bytes(self, name: str, *, generation: Optional[str] = None) -> bytes:
        pass

    @abstractmethod
    def write_bytes(self, name: str, data: bytes, *, if_generation_match=None,
                    custom_time=None, metadata=None, content_type=None) -> ObjectMetadata:
        pass

    def read_json(self, name: str, *, generation: Optional[str] = None):
        return json.loads(self.read_bytes(name, generation=generation))

    def write_json(self, name: str, value, *, if_generation_match=None,
                   custom_time=None) -> ObjectMetadata:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        return self.write_bytes(name, data, if_generation_match=if_generation_match,
                                custom_time=custom_time, content_type="application/json")

    @abstractmethod
    def download(self, bucket_blob_name, local_blob_path, *, generation=None):
        pass

    @abstractmethod
    def upload(self, local_blob_name, bucket_blob_name, *, if_generation_match=None,
               custom_time=None, metadata=None, content_type=None) -> ObjectMetadata:
        pass

    @abstractmethod
    def copy_blob(self, source_blob_name, destination_blob_name, *, destination_bucket=None,
                  source_generation=None, if_generation_match=None,
                  custom_time=None) -> ObjectMetadata:
        """Create a new generation, omitting source Custom-Time by default."""
        pass

    def rewrite(self, source_blob_name, destination_blob_name, **kwargs) -> ObjectMetadata:
        return self.copy_blob(source_blob_name, destination_blob_name, **kwargs)

    @abstractmethod
    def delete(self, name: str, *, if_generation_match: str):
        pass

    @abstractmethod
    def update_metadata(self, name: str, *, if_generation_match: str,
                        if_metageneration_match: str, custom_time=None,
                        metadata=None) -> ObjectMetadata:
        """None leaves Custom-Time unchanged; clearing/backdating needs rewrite."""
        pass

    @abstractmethod
    def get_lifecycle(self) -> BucketLifecycle:
        pass

    @abstractmethod
    def update_lifecycle(self, rules, *, if_metageneration_match: str) -> BucketLifecycle:
        pass

    def lifecycle_candidates(self) -> List[ObjectMetadata]:
        """Read-only eligibility for Delete + daysSinceCustomTime rules.

        This shared diagnostic does not delete objects or predict when GCS will
        execute a rule. Unsupported conditions are never approximated. Tests
        can replace the private clock without changing backend construction.
        """
        now = self._clock()
        rules = self.get_lifecycle().rules
        candidates = []
        for item in self.list_metadata():
            for rule in rules:
                condition = rule.get("condition", {})
                if (rule.get("action") != {"type": "Delete"}
                        or set(condition) - {"matchesPrefix", "daysSinceCustomTime"}
                        or "daysSinceCustomTime" not in condition or item.custom_time is None):
                    continue
                prefixes = condition.get("matchesPrefix", [""])
                if (any(item.name.startswith(prefix) for prefix in prefixes)
                        and now >= item.custom_time + timedelta(days=condition["daysSinceCustomTime"])):
                    candidates.append(item)
                    break
        return candidates

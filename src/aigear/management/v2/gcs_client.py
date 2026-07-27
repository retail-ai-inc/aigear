"""Generation-aware GCS backend used by Pipeline V2.

All reads and copies address an exact generation.  All creates/updates use an
``if_generation_match`` precondition; there is no unguarded "latest" write in
this adapter.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional, Protocol

from aigear.management.v2.fake_gcs import (
    GenerationPreconditionError,
    GcsObjectSnapshot,
    ObjectNotFoundError,
)

__all__ = ["GcsClientV2", "GoogleGcsClientV2"]


class GcsClientV2(Protocol):
    def upload_file(
        self,
        object_name: str,
        source: Path,
        *,
        if_generation_match: int = 0,
        expected_sha256: Optional[str] = None,
    ) -> GcsObjectSnapshot: ...

    def put_object(
        self, object_name: str, data: bytes, *, if_generation_match: Optional[int] = None
    ) -> GcsObjectSnapshot: ...

    def get_object(self, object_name: str, *, generation: str) -> GcsObjectSnapshot: ...

    def get_live_object(self, object_name: str) -> Optional[GcsObjectSnapshot]: ...

    def copy_object(
        self,
        source_object_name: str,
        source_generation: str,
        dest_object_name: str,
        *,
        if_generation_match: int = 0,
    ) -> GcsObjectSnapshot: ...

    def download_to_file(self, object_name: str, generation: str, target: Path) -> None: ...


class GoogleGcsClientV2:
    """Real Google Cloud Storage adapter for one configured asset bucket."""

    def __init__(self, bucket_name: str, *, client: Any | None = None) -> None:
        if not bucket_name:
            raise ValueError("bucket_name must be non-empty")
        if client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("GoogleGcsClientV2 requires google-cloud-storage") from exc
            client = storage.Client()
        self.bucket_name = bucket_name
        self.client = client
        self.bucket = client.bucket(bucket_name)

    @staticmethod
    def _translate(exc: BaseException, object_name: str, generation: str | None = None):
        code = getattr(exc, "code", None)
        if code == 404:
            raise ObjectNotFoundError(
                f"no object {object_name!r} at generation {generation!r}"
            ) from exc
        if code in (409, 412):
            raise GenerationPreconditionError(
                f"generation precondition failed for {object_name!r}"
            ) from exc
        raise exc

    @staticmethod
    def _snapshot(blob: Any, data: bytes) -> GcsObjectSnapshot:
        return GcsObjectSnapshot(
            object_name=blob.name,
            generation=str(blob.generation),
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            crc32c=str(blob.crc32c or ""),
            size_bytes=len(data),
        )

    def put_object(
        self, object_name: str, data: bytes, *, if_generation_match: Optional[int] = None
    ) -> GcsObjectSnapshot:
        blob = self.bucket.blob(object_name)
        try:
            blob.upload_from_string(data, if_generation_match=if_generation_match)
            blob.reload()
        except BaseException as exc:  # google exceptions are optional imports
            self._translate(exc, object_name)
        return self._snapshot(blob, bytes(data))

    def upload_file(
        self,
        object_name: str,
        source: Path,
        *,
        if_generation_match: int = 0,
        expected_sha256: Optional[str] = None,
    ) -> GcsObjectSnapshot:
        digest = hashlib.sha256()
        size = 0
        with open(source, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ValueError("source file changed before upload")
        blob = self.bucket.blob(object_name)
        try:
            blob.upload_from_filename(str(source), if_generation_match=if_generation_match)
            blob.reload()
        except BaseException as exc:
            self._translate(exc, object_name)
        return GcsObjectSnapshot(
            object_name=object_name,
            generation=str(blob.generation),
            data=b"",
            sha256=actual_sha256,
            crc32c=str(blob.crc32c or ""),
            size_bytes=size,
        )

    def get_object(self, object_name: str, *, generation: str) -> GcsObjectSnapshot:
        blob = self.bucket.blob(object_name, generation=int(generation))
        try:
            data = blob.download_as_bytes(if_generation_match=int(generation))
            blob.reload()
        except BaseException as exc:
            self._translate(exc, object_name, generation)
        return self._snapshot(blob, data)

    def get_live_object(self, object_name: str) -> Optional[GcsObjectSnapshot]:
        blob = self.bucket.blob(object_name)
        try:
            blob.reload()
        except BaseException as exc:
            if getattr(exc, "code", None) == 404:
                return None
            self._translate(exc, object_name)
        return self.get_object(object_name, generation=str(blob.generation))

    def copy_object(
        self,
        source_object_name: str,
        source_generation: str,
        dest_object_name: str,
        *,
        if_generation_match: int = 0,
    ) -> GcsObjectSnapshot:
        source = self.bucket.blob(source_object_name, generation=int(source_generation))
        try:
            copied = self.bucket.copy_blob(
                source,
                self.bucket,
                new_name=dest_object_name,
                source_generation=int(source_generation),
                if_generation_match=if_generation_match,
            )
            copied.reload()
            data = copied.download_as_bytes(if_generation_match=int(copied.generation))
        except BaseException as exc:
            self._translate(exc, dest_object_name)
        return self._snapshot(copied, data)

    def download_to_file(self, object_name: str, generation: str, target: Path) -> None:
        blob = self.bucket.blob(object_name, generation=int(generation))
        try:
            with open(target, "wb") as handle:
                blob.download_to_file(handle, if_generation_match=int(generation))
        except BaseException as exc:
            self._translate(exc, object_name, generation)

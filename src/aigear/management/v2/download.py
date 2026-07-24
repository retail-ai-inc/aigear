"""Exact download protocol (spec section 6.6).

Implements the parts of spec 6.6's download protocol that are pure local
filesystem/verification logic, independent of which GCS client backs it:
download to a temp file in the *same directory* as the target (so the final
``os.replace`` is an atomic same-filesystem rename), verify size and full
SHA-256 against the resolved handle, ``fsync`` before renaming, and clean up
the temp file on any failure so a consumer never sees a partial file.

Trust/lifecycle/attestation validation already happened in
:func:`~aigear.management.v2.resolver.resolve` (T24) -- this module only
re-verifies what it can locally recompute from the downloaded bytes
themselves (size, SHA-256). Real manifest-signature/attestation
cryptographic verification needs the same KMS infrastructure
``finalizer.py`` already documents as out of scope for Phase B.

Also out of scope: spec 6.7's manifest-based bundle download (multiple
components into a ``<target>/<role>/<logical_name>`` tree) -- this module
only downloads the single component a :class:`~aigear.management.v2.resolver.
ResolvedHandle` most commonly carries in Phase B (finalizer.py is
single-payload-per-output only, see its module docstring), and rejects a
handle with more than one Blob outright.

Real streaming timeouts/backpressure are meaningless against
:class:`~aigear.management.v2.fake_gcs.FakeGcsClient` (an in-memory, blocking
double); ``max_size_bytes`` is checked against the resolved handle's declared
size before any data is even read, which is the one part of "限制最大字节数
和超时" (spec 6.6 step 3) this module can meaningfully enforce without a real
network client.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.resolver import ResolvedHandle

__all__ = [
    "DownloadError",
    "download_exact",
    "compute_local_cache_key",
    "verify_cached_file",
]


class DownloadError(ValueError):
    """Raised when an exact download cannot be completed as requested."""


def compute_local_cache_key(blob_id: TypedId, generation: str, location_revision: int) -> str:
    """The local cache key spec 6.6 mandates: ``blob_id + generation +
    current_location_revision``, never ``blob_id`` alone (a Blob's bytes at
    a stale generation/location_revision must never be served as current)."""
    return f"{blob_id.bare}:{generation}:{location_revision}"


def verify_cached_file(path: Path, *, expected_sha256: str, expected_size: int) -> bool:
    """Spec 6.6: "cache hit 仍要验证文件摘要" -- a cache key match alone is not
    trusted; the file's actual digest/size must still match every time."""
    if not path.is_file():
        return False
    if path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256


def download_exact(
    handle: ResolvedHandle,
    gcs: FakeGcsClient,
    target_path: Path,
    *,
    now: Optional[datetime] = None,
    max_size_bytes: Optional[int] = None,
) -> Path:
    """Download the resolved handle's single Blob to ``target_path`` (spec 6.6)."""
    if now is not None and now >= handle.expires_at:
        raise DownloadError(
            f"resolved handle expired at {handle.expires_at.isoformat()!r}; re-resolve before downloading"
        )
    if len(handle.blobs) != 1:
        raise DownloadError(
            f"download_exact only supports a single-component handle, got {len(handle.blobs)} "
            "components; bundle download (spec 6.7) is out of scope"
        )
    blob = handle.blobs[0]
    if max_size_bytes is not None and blob.size_bytes > max_size_bytes:
        raise DownloadError(
            f"Blob {blob.blob_id.typed!r} size {blob.size_bytes} exceeds max_size_bytes {max_size_bytes}"
        )

    snapshot = gcs.get_object(blob.object_name, generation=blob.generation)
    if snapshot.size_bytes != blob.size_bytes:
        raise DownloadError(
            f"Blob {blob.blob_id.typed!r} size mismatch: resolved handle says "
            f"{blob.size_bytes}, downloaded object is {snapshot.size_bytes}"
        )
    if snapshot.sha256 != blob.sha256:
        raise DownloadError(
            f"Blob {blob.blob_id.typed!r} SHA-256 mismatch: resolved handle says "
            f"{blob.sha256!r}, downloaded object is {snapshot.sha256!r}"
        )

    target_path = Path(target_path)
    tmp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp_path, "wb") as file_obj:
            file_obj.write(snapshot.data)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, target_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target_path

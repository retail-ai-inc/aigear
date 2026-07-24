"""Minimal in-memory, generation-aware GCS test double for Pipeline V2 (spec 6.5).

``aigear.db.bucket.LocalGCSMock`` (the legacy V1 mock) is a plain
path-based ``shutil.copy`` wrapper with no concept of a GCS *generation* at
all. The V2 finalize protocol is built entirely around generation
semantics -- create-only staging upload, re-reading a staging object at its
exact reported generation, and a create-only (``ifGenerationMatch=0``)
canonical copy -- so that legacy mock cannot stand in for it. This module is
the smallest double that can: one monotonically increasing generation
counter per object name, content-addressed by the raw bytes actually
stored, so a caller can genuinely re-verify a reported digest/size against
what is "in the bucket" instead of trusting it blindly.

``crc32c`` here is a placeholder digest (``zlib.crc32``, not the real
Castagnoli polynomial GCS uses), matching how every other test double in
this package already treats ``crc32c`` as an opaque, unenforced string --
see ``BlobRecord``'s own field validation and its tests.
"""

from __future__ import annotations

import hashlib
import zlib
from base64 import b64encode
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

__all__ = [
    "FakeGcsError",
    "ObjectNotFoundError",
    "GenerationPreconditionError",
    "GcsObjectSnapshot",
    "FakeGcsClient",
]


class FakeGcsError(ValueError):
    """Base class for this module's errors."""


class ObjectNotFoundError(FakeGcsError):
    """No object exists at the given name (and generation, if specified)."""


class GenerationPreconditionError(FakeGcsError):
    """An ``if_generation_match`` precondition did not hold (spec 6.5's create-only writes)."""


def _fake_crc32c(data: bytes) -> str:
    return b64encode(zlib.crc32(data).to_bytes(4, "big")).decode("ascii")


@dataclass(frozen=True)
class GcsObjectSnapshot:
    """One immutable generation of one object."""

    object_name: str
    generation: str
    data: bytes
    sha256: str
    crc32c: str
    size_bytes: int


class FakeGcsClient:
    """In-memory bucket: object_name -> live generation, plus every past
    generation kept around so exact-generation reads always succeed."""

    def __init__(self) -> None:
        self._live: Dict[str, GcsObjectSnapshot] = {}
        self._by_generation: Dict[Tuple[str, str], GcsObjectSnapshot] = {}
        self._generation_counter = 0

    def put_object(
        self, object_name: str, data: bytes, *, if_generation_match: Optional[int] = None
    ) -> GcsObjectSnapshot:
        """Write ``data`` to ``object_name``. ``if_generation_match=0`` means
        create-only (spec 6.5 steps 3/4); any other int requires the current
        live generation to match exactly."""
        current = self._live.get(object_name)
        current_generation = int(current.generation) if current is not None else 0
        if if_generation_match is not None and current_generation != if_generation_match:
            raise GenerationPreconditionError(
                f"if_generation_match={if_generation_match} does not match current "
                f"generation {current_generation} for {object_name!r}"
            )

        self._generation_counter += 1
        snapshot = GcsObjectSnapshot(
            object_name=object_name,
            generation=str(self._generation_counter),
            data=bytes(data),
            sha256=hashlib.sha256(data).hexdigest(),
            crc32c=_fake_crc32c(data),
            size_bytes=len(data),
        )
        self._live[object_name] = snapshot
        self._by_generation[(object_name, snapshot.generation)] = snapshot
        return snapshot

    def get_object(self, object_name: str, *, generation: str) -> GcsObjectSnapshot:
        """Read back the exact generation reported earlier (spec 6.5 step 2/4:
        finalizer must re-read by the reported generation, never "latest")."""
        snapshot = self._by_generation.get((object_name, generation))
        if snapshot is None:
            raise ObjectNotFoundError(
                f"no object {object_name!r} at generation {generation!r}"
            )
        return snapshot

    def get_live_object(self, object_name: str) -> Optional[GcsObjectSnapshot]:
        return self._live.get(object_name)

    def copy_object(
        self,
        source_object_name: str,
        source_generation: str,
        dest_object_name: str,
        *,
        if_generation_match: int = 0,
    ) -> GcsObjectSnapshot:
        """Copy an exact source generation to a destination object (spec 6.5
        step 4: canonical copy uses ``ifGenerationMatch=0``, i.e. create-only)."""
        source = self.get_object(source_object_name, generation=source_generation)
        return self.put_object(dest_object_name, source.data, if_generation_match=if_generation_match)

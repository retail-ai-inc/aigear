"""Typed full SHA-256 identity codec for Pipeline V2.

Per docs/pipeline-asset-lifecycle-management-v2.md section 5.2, all V2 internal
identities (``blob_id``, ``manifest_digest``, ``asset_version_id``, ``label_id``,
``occurrence_id``, ...) are full SHA-256 digests. JSON fields use the typed form
``sha256:<64 lowercase hex chars>``; Firestore document IDs and GCS path segments
use the bare 64-character lowercase hex form without the prefix. This module is
the single place that converts between those two forms so every caller agrees on
the exact rules instead of hand-rolling ``str.split("sha256:")`` in multiple
places.

Short digests (12/16/24 hex chars) are display-only per the same section and are
deliberately *not* representable by :class:`TypedId` — constructing one always
requires a full 64-character digest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "InvalidTypedIdError",
    "TypedId",
    "SHA256_TYPED_PREFIX",
]

SHA256_TYPED_PREFIX = "sha256:"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class InvalidTypedIdError(ValueError):
    """Raised when a value is not a well-formed full SHA-256 identity."""


@dataclass(frozen=True)
class TypedId:
    """An immutable, validated full SHA-256 identity.

    Stores the bare (unprefixed) lowercase hex digest internally; use
    :attr:`typed` / :attr:`bare` to obtain either external representation.
    """

    hex_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.hex_digest, str) or not _SHA256_HEX_RE.fullmatch(
            self.hex_digest
        ):
            raise InvalidTypedIdError(
                "Expected a bare, lowercase, 64-character hex SHA-256 digest, "
                f"got {self.hex_digest!r}"
            )

    @classmethod
    def from_typed(cls, value: str) -> "TypedId":
        """Parse a JSON-style typed ID, e.g. ``sha256:abcd...``."""
        if not isinstance(value, str) or not value.startswith(SHA256_TYPED_PREFIX):
            raise InvalidTypedIdError(
                f"Typed ID must start with {SHA256_TYPED_PREFIX!r}, got {value!r}"
            )
        return cls(value[len(SHA256_TYPED_PREFIX) :])

    @classmethod
    def from_bare(cls, value: str) -> "TypedId":
        """Parse a bare hex digest, e.g. a Firestore document ID or GCS path segment."""
        return cls(value)

    @property
    def typed(self) -> str:
        """The ``sha256:<hex>`` form used in JSON fields."""
        return f"{SHA256_TYPED_PREFIX}{self.hex_digest}"

    @property
    def bare(self) -> str:
        """The unprefixed hex form used as a Firestore document ID / GCS path segment."""
        return self.hex_digest

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.typed

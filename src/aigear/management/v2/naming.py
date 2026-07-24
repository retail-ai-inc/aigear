"""Shared name/path segment validator for Pipeline V2 (spec section 5.1).

Every dynamic segment used in a V2 GCS object path, local bundle path or
composite identity (``project_name``, ``pipeline_version``, ``asset_type``,
``asset_name``, ``display_version``, ``run_id``, ``step_name``, ``output_name``,
``attempt_no``, ``operation_id``, ``role``, ``logical_name``, ...) must go
through :func:`validate_segment` instead of being validated ad hoc by callers.
CLI, SDK, controller, migrator and ``LocalGCSMock`` are all expected to share
this module rather than reimplementing the rules.

These are stable, ASCII-only *path keys* — not human-facing display names.
Unicode display names/labels are stored separately as metadata and must never
be fed directly into this validator to produce a path segment.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

__all__ = [
    "InvalidSegmentError",
    "MAX_SEGMENT_BYTES",
    "normalize_segment",
    "validate_segment",
    "collision_key",
    "ensure_no_collisions",
]

MAX_SEGMENT_BYTES = 128

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Windows reserved device names are case-insensitive and reserved both bare
# and with any extension (e.g. "CON", "con.txt").
_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class InvalidSegmentError(ValueError):
    """Raised when a value is not a valid V2 path/identity segment."""


def normalize_segment(value: str) -> str:
    """Apply the mandatory Unicode NFC normalization step before validation."""
    if not isinstance(value, str):
        raise InvalidSegmentError(f"Segment must be a str, got {type(value)!r}")
    return unicodedata.normalize("NFC", value)


def validate_segment(value: str, *, field_name: str = "segment") -> str:
    """Normalize and validate a single V2 path/identity segment.

    Returns the normalized segment on success. Raises
    :class:`InvalidSegmentError` for anything that does not satisfy every
    rule in spec section 5.1: NFC normalization, the
    ``^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`` charset, no ``.``/``..``, no
    trailing space/dot, no control characters or path separators, no
    Windows reserved device names, and a 128 UTF-8 byte cap.
    """
    normalized = normalize_segment(value)

    if normalized == "":
        raise InvalidSegmentError(f"{field_name} must not be empty")

    if normalized in (".", ".."):
        raise InvalidSegmentError(f"{field_name} must not be '.' or '..': {value!r}")

    if not _SEGMENT_RE.fullmatch(normalized):
        raise InvalidSegmentError(
            f"{field_name} must match ^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$, "
            f"got {value!r}"
        )

    # The charset above still allows a *trailing* '.', which Windows treats
    # as insignificant (`foo.` and `foo` name the same file); reject it
    # explicitly instead of silently colliding at the filesystem layer.
    if normalized.endswith("."):
        raise InvalidSegmentError(f"{field_name} must not end with '.': {value!r}")

    stem = normalized.split(".", 1)[0]
    if stem.upper() in _WINDOWS_RESERVED_STEMS:
        raise InvalidSegmentError(
            f"{field_name} collides with a reserved Windows device name: {value!r}"
        )

    encoded_len = len(normalized.encode("utf-8"))
    if encoded_len > MAX_SEGMENT_BYTES:
        raise InvalidSegmentError(
            f"{field_name} exceeds {MAX_SEGMENT_BYTES} UTF-8 bytes "
            f"({encoded_len} bytes): {value!r}"
        )

    return normalized


def collision_key(value: str) -> str:
    """Return a normalized key for detecting cross-platform filename collisions.

    Two segments collide if they are equal after NFC normalization, Unicode
    case folding, and stripping Windows-insignificant trailing dots/spaces
    (spec section 5.1: "所有会落到本地同一目录的 key 还要做 NFC + case-fold +
    Windows trailing-dot/space 比较").
    """
    normalized = unicodedata.normalize("NFC", value)
    stripped = normalized.rstrip(" .")
    return stripped.casefold()


def ensure_no_collisions(segments: Iterable[str], *, field_name: str = "segment") -> None:
    """Reject a batch of segments that would collide once normalized.

    Identical repeated values are allowed; two *different* original values
    that normalize to the same collision key raise :class:`InvalidSegmentError`.
    """
    seen: dict[str, str] = {}
    for segment in segments:
        key = collision_key(segment)
        existing = seen.get(key)
        if existing is not None and existing != segment:
            raise InvalidSegmentError(
                f"{field_name} values collide under NFC+case-fold(+trailing "
                f"dot/space) normalization: {existing!r} vs {segment!r}"
            )
        seen[key] = segment

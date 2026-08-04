"""RFC 8785 JSON Canonicalization Scheme (JCS) — minimal supported subset.

Pipeline V2 identities (``asset_version_id``, ``label_id``, ``occurrence_id``, ...)
are computed as ``SHA256(JCS(canonical_manifest))`` (see
docs/pipeline-asset-lifecycle-management-v2.md sections 5.2/5.3). Every part of the
system that computes or verifies one of these identities must use this exact
encoding, so it lives in a single shared module instead of being reimplemented
ad hoc next to each identity computation.

Supported JSON value subset: ``dict``, ``list``/``tuple``, ``str``, ``bool``,
``int`` and ``None``. ``float`` is intentionally rejected: RFC 8785 requires
ECMAScript ``Number::toString`` formatting for numbers, which is a large,
easy-to-get-subtly-wrong surface area that none of the current V2 canonical
manifests actually need (they only ever hash strings, integers, booleans,
null, arrays and objects). Callers that need to include a decimal value must
pre-format it to a string themselves so the identity computation stays
unambiguous.
"""

from __future__ import annotations

import hashlib
from typing import Any

__all__ = [
    "CanonicalizationError",
    "canonicalize_json",
    "digest_sha256_of_jcs",
]


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented in the supported JCS subset."""


_CONTROL_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def canonicalize_json(obj: Any) -> bytes:
    """Serialize ``obj`` to RFC 8785 canonical UTF-8 bytes.

    No whitespace is emitted, object members are ordered by the UTF-16 code
    unit sequence of their (string) keys, and non-ASCII characters are
    written as literal UTF-8 rather than ``\\uXXXX`` escapes, per RFC 8785.
    """
    return _encode(obj).encode("utf-8")


def digest_sha256_of_jcs(obj: Any) -> str:
    """Return the lowercase hex SHA-256 digest of ``canonicalize_json(obj)``."""
    return hashlib.sha256(canonicalize_json(obj)).hexdigest()


def _encode(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise CanonicalizationError(
            "float values are not supported by canonicalize_json(); "
            "pre-format decimal values to str or int before canonicalizing"
        )
    if isinstance(value, str):
        return _encode_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        return _encode_object(value)
    raise CanonicalizationError(f"Unsupported type for canonicalization: {type(value)!r}")


def _encode_object(value: dict) -> str:
    for key in value.keys():
        if not isinstance(key, str):
            raise CanonicalizationError(
                f"JSON object keys must be strings, got {type(key)!r}"
            )
    ordered_keys = sorted(value.keys(), key=_utf16_sort_key)
    members = (f"{_encode_string(key)}:{_encode(value[key])}" for key in ordered_keys)
    return "{" + ",".join(members) + "}"


def _encode_string(value: str) -> str:
    parts = ['"']
    for ch in value:
        escape = _CONTROL_ESCAPES.get(ch)
        if escape is not None:
            parts.append(escape)
        elif ord(ch) < 0x20:
            parts.append(f"\\u{ord(ch):04x}")
        else:
            parts.append(ch)
    parts.append('"')
    return "".join(parts)


def _utf16_sort_key(value: str) -> tuple:
    """Sort key matching RFC 8785's "UTF-16 code unit sequence" member ordering.

    This differs from plain Python string comparison (which orders by Unicode
    code point) whenever a key contains a supplementary-plane character
    (code point > U+FFFF, encoded as a UTF-16 surrogate pair): the pair's
    lead surrogate (0xD800-0xDBFF) can be numerically smaller than an
    unpaired code unit like U+E000-U+FFFF, flipping the ordering relative to
    code point comparison.
    """
    encoded = value.encode("utf-16-be")
    return tuple(
        int.from_bytes(encoded[i : i + 2], "big") for i in range(0, len(encoded), 2)
    )

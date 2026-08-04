from __future__ import annotations

import pytest

from aigear.management.v2.identifiers import (
    SHA256_TYPED_PREFIX,
    InvalidTypedIdError,
    TypedId,
)

_VALID_HEX = "ab" * 32  # 64 lowercase hex chars


def test_from_bare_accepts_valid_hex():
    typed_id = TypedId.from_bare(_VALID_HEX)
    assert typed_id.bare == _VALID_HEX
    assert typed_id.typed == f"sha256:{_VALID_HEX}"
    assert str(typed_id) == f"sha256:{_VALID_HEX}"


def test_from_typed_accepts_prefixed_form():
    typed_id = TypedId.from_typed(f"sha256:{_VALID_HEX}")
    assert typed_id.bare == _VALID_HEX


def test_from_typed_and_from_bare_are_equivalent():
    assert TypedId.from_typed(f"sha256:{_VALID_HEX}") == TypedId.from_bare(_VALID_HEX)


def test_prefix_constant_matches_typed_output():
    typed_id = TypedId.from_bare(_VALID_HEX)
    assert typed_id.typed.startswith(SHA256_TYPED_PREFIX)


@pytest.mark.parametrize(
    "value",
    [
        "AB" * 32,  # uppercase not accepted; callers must normalize explicitly
        "a" * 63,  # too short
        "a" * 65,  # too long
        "g" * 64,  # non-hex character
        "",
        "sha256:" + "a" * 64,  # bare parser must reject the typed prefix
    ],
)
def test_from_bare_rejects_malformed_input(value):
    with pytest.raises(InvalidTypedIdError):
        TypedId.from_bare(value)


@pytest.mark.parametrize(
    "value",
    [
        "a" * 64,  # missing prefix
        "sha1:" + "a" * 64,  # wrong prefix
        "sha256:" + "a" * 63,  # short digest after valid prefix
        "sha256:" + "A" * 64,  # uppercase digest after valid prefix
    ],
)
def test_from_typed_rejects_malformed_input(value):
    with pytest.raises(InvalidTypedIdError):
        TypedId.from_typed(value)


def test_typed_id_is_immutable_and_hashable():
    typed_id = TypedId.from_bare(_VALID_HEX)
    with pytest.raises(AttributeError):
        typed_id.hex_digest = "c" * 64  # type: ignore[misc]
    assert {typed_id, TypedId.from_bare(_VALID_HEX)} == {typed_id}


def test_short_digest_is_never_representable():
    # Section 5.2: 12/16/24-char short digests are display-only and must
    # never be usable as a standalone identity.
    with pytest.raises(InvalidTypedIdError):
        TypedId.from_bare(_VALID_HEX[:16])

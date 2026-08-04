from __future__ import annotations

import hashlib

import pytest

from aigear.management.v2.canonical import (
    CanonicalizationError,
    canonicalize_json,
    digest_sha256_of_jcs,
)


def test_scalar_encoding():
    assert canonicalize_json(None) == b"null"
    assert canonicalize_json(True) == b"true"
    assert canonicalize_json(False) == b"false"
    assert canonicalize_json(0) == b"0"
    assert canonicalize_json(-42) == b"-42"


def test_string_escaping_matches_json_minimal_escape_set():
    assert canonicalize_json("a\"b\\c") == b'"a\\"b\\\\c"'
    assert canonicalize_json("\n\t\r\b\f") == b'"\\n\\t\\r\\b\\f"'
    assert canonicalize_json("\x01\x1f") == b'"\\u0001\\u001f"'


def test_forward_slash_is_not_escaped():
    assert canonicalize_json("a/b") == b'"a/b"'


def test_non_ascii_is_emitted_as_raw_utf8_not_escaped():
    # RFC 8785 requires literal UTF-8 output for non-ASCII text, not \uXXXX.
    encoded = canonicalize_json("café")
    assert encoded == '"café"'.encode("utf-8")
    assert b"\\u" not in encoded


def test_array_encoding_has_no_whitespace():
    assert canonicalize_json([1, "a", None, True]) == b'[1,"a",null,true]'


def test_object_keys_are_sorted_and_compact():
    assert canonicalize_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_nested_structures_round_trip_deterministically():
    payload = {
        "z": [1, 2, {"y": "x", "a": "b"}],
        "a": {"nested": True, "value": None},
    }
    first = canonicalize_json(payload)
    second = canonicalize_json(
        {
            "a": {"value": None, "nested": True},
            "z": [1, 2, {"a": "b", "y": "x"}],
        }
    )
    assert first == second


def test_object_member_ordering_uses_utf16_code_unit_sequence_not_code_point():
    # U+1F600 (a supplementary-plane emoji) encodes as the UTF-16 surrogate
    # pair (0xD83D, 0xDE00); its lead surrogate 0xD83D is numerically smaller
    # than the unpaired code unit 0xFFFF for U+FFFF. RFC 8785 requires
    # ordering by UTF-16 code unit sequence, so the emoji key must sort
    # *before* "\uffff" even though its Unicode code point is larger.
    payload = {"\U0001f600": 1, "\uffff": 2}
    encoded = canonicalize_json(payload).decode("utf-8")
    assert encoded == '{"\U0001f600":1,"\uffff":2}'
    assert encoded.index("\U0001f600") < encoded.index("\uffff")


def test_non_string_object_keys_are_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize_json({1: "a"})  # type: ignore[dict-item]


def test_float_is_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize_json(1.5)


def test_unsupported_type_is_rejected():
    with pytest.raises(CanonicalizationError):
        canonicalize_json(object())


def test_digest_sha256_of_jcs_matches_manual_hash():
    payload = {"b": 1, "a": 2}
    expected = hashlib.sha256(b'{"a":2,"b":1}').hexdigest()
    assert digest_sha256_of_jcs(payload) == expected


def test_digest_sha256_of_jcs_is_stable_regardless_of_input_key_order():
    assert digest_sha256_of_jcs({"a": 1, "b": 2}) == digest_sha256_of_jcs(
        {"b": 2, "a": 1}
    )


def test_domain_separator_array_pattern_used_by_v2_identity_formulas():
    # Mirrors the "aigear.<kind>.v2" JCS array pattern used throughout the
    # V2 spec, e.g. label_id = sha256(JCS(["aigear.label.v2", ...])).
    payload = ["aigear.label.v2", "model", "logistic_regression", "v2026.07.23"]
    encoded = canonicalize_json(payload)
    assert encoded == (
        b'["aigear.label.v2","model","logistic_regression","v2026.07.23"]'
    )

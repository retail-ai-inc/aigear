from __future__ import annotations

import pytest

from aigear.management.v2.naming import (
    InvalidSegmentError,
    collision_key,
    ensure_no_collisions,
    normalize_segment,
    validate_segment,
)


# ── validate_segment: happy path ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "a",
        "A9",
        "logistic_regression",
        "v2026.07.23",
        "model-v2",
        "a" * 128,
    ],
)
def test_validate_segment_accepts_well_formed_values(value):
    assert validate_segment(value) == value


# ── validate_segment: rejections ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "-leading-dash",
        ".leading-dot",
        "_leading-underscore",
        "trailing.",
        "has space",
        "has/slash",
        "has\\backslash",
        "control\x01char",
        "a" * 129,
        "café",  # non-ASCII is not a valid path key even after NFC
    ],
)
def test_validate_segment_rejects_invalid_values(value):
    with pytest.raises(InvalidSegmentError):
        validate_segment(value)


@pytest.mark.parametrize(
    "value",
    ["CON", "con", "PRN", "AUX", "NUL", "COM1", "lpt9", "CON.txt", "com1.json"],
)
def test_validate_segment_rejects_windows_reserved_device_names(value):
    with pytest.raises(InvalidSegmentError):
        validate_segment(value)


def test_validate_segment_error_message_includes_field_name():
    with pytest.raises(InvalidSegmentError, match="run_id"):
        validate_segment("bad/value", field_name="run_id")


def test_validate_segment_128_byte_boundary():
    validate_segment("a" * 128)
    with pytest.raises(InvalidSegmentError):
        validate_segment("a" * 129)


# ── normalize_segment ──────────────────────────────────────────────────────────


def test_normalize_segment_is_a_no_op_for_ascii():
    assert normalize_segment("model-v2") == "model-v2"


def test_normalize_segment_applies_nfc():
    decomposed = "e\u0301"  # "e" + combining acute accent
    precomposed = "\u00e9"  # "é"
    assert normalize_segment(decomposed) == precomposed


def test_normalize_segment_rejects_non_str():
    with pytest.raises(InvalidSegmentError):
        normalize_segment(123)  # type: ignore[arg-type]


# ── collision_key / ensure_no_collisions ───────────────────────────────────────


@pytest.mark.parametrize(
    "left,right",
    [
        ("Model", "model"),
        ("MODEL", "model"),
        ("foo.", "foo"),
        ("foo ", "foo"),
        ("e\u0301", "\u00e9"),
    ],
)
def test_collision_key_treats_these_pairs_as_colliding(left, right):
    assert collision_key(left) == collision_key(right)


def test_collision_key_does_not_conflate_distinct_names():
    assert collision_key("model") != collision_key("models")


def test_ensure_no_collisions_allows_repeated_identical_values():
    ensure_no_collisions(["model", "model", "model"])


def test_ensure_no_collisions_allows_distinct_values():
    ensure_no_collisions(["model", "scaler", "schema"])


def test_ensure_no_collisions_rejects_case_fold_collision():
    with pytest.raises(InvalidSegmentError):
        ensure_no_collisions(["Model", "model"])


def test_ensure_no_collisions_rejects_trailing_dot_collision():
    with pytest.raises(InvalidSegmentError):
        ensure_no_collisions(["model", "model."])

from __future__ import annotations

import pytest

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.label import (
    InvalidLabelRecordError,
    LabelRecord,
    ReadableManifestProjection,
    compute_label_id,
)

_FINGERPRINT_HEX = "aa" * 32
_ASSET_VERSION_HEX = "bb" * 32


def _fingerprint() -> TypedId:
    return TypedId.from_bare(_FINGERPRINT_HEX)


def _asset_version_id() -> TypedId:
    return TypedId.from_bare(_ASSET_VERSION_HEX)


def _readable_manifest() -> ReadableManifestProjection:
    return ReadableManifestProjection.initial(
        uri="gs://aigear-prod-assets/proj/pipeline/registry/v2/assets/model/"
        "logistic_regression/versions/v2026.07.23/manifest.json"
    )


def _label_record(**overrides) -> LabelRecord:
    label_id = compute_label_id("model", "logistic_regression", "v2026.07.23")
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        label_id=label_id,
        asset_type="model",
        asset_name="logistic_regression",
        display_version="v2026.07.23",
        asset_version_id=_asset_version_id(),
        projection_source_revision=1,
        readable_manifest=_readable_manifest(),
        created_by="finalizer@aigear",
    )
    defaults.update(overrides)
    return LabelRecord(**defaults)


# ── compute_label_id ─────────────────────────────────────────────────────────────


def test_compute_label_id_matches_manual_jcs_hash():
    expected = digest_sha256_of_jcs(
        ["aigear.label.v2", "model", "logistic_regression", "v2026.07.23"]
    )
    assert compute_label_id("model", "logistic_regression", "v2026.07.23").bare == expected


def test_compute_label_id_is_deterministic():
    assert compute_label_id("model", "logistic_regression", "v1") == compute_label_id(
        "model", "logistic_regression", "v1"
    )


def test_compute_label_id_differs_per_display_version():
    assert compute_label_id("model", "logistic_regression", "v1") != compute_label_id(
        "model", "logistic_regression", "v2"
    )


def test_compute_label_id_differs_per_asset_type():
    assert compute_label_id("model", "x", "v1") != compute_label_id("dataset", "x", "v1")


def test_compute_label_id_rejects_invalid_segment():
    with pytest.raises(ValueError):
        compute_label_id("model", "../escape", "v1")


# ── ReadableManifestProjection ─────────────────────────────────────────────────────


def test_readable_manifest_projection_initial_state():
    projection = _readable_manifest()
    assert projection.status == "pending"
    assert projection.observed_generation is None
    assert projection.projection_mutation_fence == 0


def test_readable_manifest_projection_rejects_non_gs_uri():
    with pytest.raises(InvalidLabelRecordError):
        ReadableManifestProjection(
            uri="https://example.com/manifest.json",
            status="pending",
            projection_mutation_fence=0,
            projection_schema_version="1.0",
        )


def test_readable_manifest_projection_rejects_malformed_schema_version():
    with pytest.raises(ValueError):
        ReadableManifestProjection(
            uri="gs://bucket/manifest.json",
            status="ready",
            projection_mutation_fence=0,
            projection_schema_version="1",
        )


def test_readable_manifest_projection_rejects_negative_fence():
    with pytest.raises(InvalidLabelRecordError):
        ReadableManifestProjection(
            uri="gs://bucket/manifest.json",
            status="ready",
            projection_mutation_fence=-1,
            projection_schema_version="1.0",
        )


# ── LabelRecord ──────────────────────────────────────────────────────────────────


def test_label_record_accepts_well_formed_fields():
    record = _label_record()
    assert record.projection_repair_epoch == 0
    assert record.asset_display_name is None
    assert record.display_label is None


def test_label_record_rejects_label_id_mismatch_with_path_keys():
    wrong_id = TypedId.from_bare("cc" * 32)
    with pytest.raises(InvalidLabelRecordError):
        _label_record(label_id=wrong_id)


def test_label_record_rejects_label_id_recomputed_from_different_display_version():
    # label_id was computed for "v2026.07.23"; claiming a different
    # display_version alongside it must be rejected even though every
    # individual field is otherwise well-formed.
    with pytest.raises(InvalidLabelRecordError):
        _label_record(display_version="v2026.07.24")


def test_label_record_rejects_non_typed_id_asset_version_id():
    with pytest.raises(InvalidLabelRecordError):
        _label_record(asset_version_id=_ASSET_VERSION_HEX)  # type: ignore[arg-type]


def test_label_record_rejects_empty_created_by():
    with pytest.raises(InvalidLabelRecordError):
        _label_record(created_by="")


def test_label_record_rejects_negative_projection_source_revision():
    with pytest.raises(InvalidLabelRecordError):
        _label_record(projection_source_revision=-1)


def test_label_record_rejects_non_readable_manifest_projection():
    with pytest.raises(InvalidLabelRecordError):
        _label_record(readable_manifest=object())  # type: ignore[arg-type]


def test_label_record_accepts_optional_display_metadata():
    record = _label_record(asset_display_name="逻辑回归模型", display_label="2026 年 7 月生产版")
    assert record.asset_display_name == "逻辑回归模型"
    assert record.display_label == "2026 年 7 月生产版"


def test_label_record_display_metadata_does_not_affect_label_id():
    with_display = _label_record(asset_display_name="逻辑回归模型")
    without_display = _label_record()
    assert with_display.label_id == without_display.label_id

from __future__ import annotations

import pytest

from aigear.management.v2.gcs_layout import (
    MAX_OBJECT_NAME_BYTES,
    PAYLOAD_KINDS,
    GcsLayoutError,
    GcsLayoutV2,
    ParsedObjectPath,
)
from aigear.management.v2.identifiers import TypedId

_BLOB_HEX = "ab" * 32


@pytest.fixture()
def layout() -> GcsLayoutV2:
    return GcsLayoutV2(
        bucket_name="aigear-prod-assets",
        project_name="aigear_sklearn_pipeline",
        pipeline_version="logistic_regression",
    )


# ── construction ────────────────────────────────────────────────────────────────


def test_object_prefix_and_root_uri(layout):
    assert layout.object_prefix == "aigear_sklearn_pipeline/logistic_regression/registry/v2"
    assert layout.root_uri == (
        "gs://aigear-prod-assets/aigear_sklearn_pipeline/logistic_regression/registry/v2/"
    )


def test_construction_rejects_invalid_bucket_name():
    with pytest.raises(GcsLayoutError):
        GcsLayoutV2(bucket_name="", project_name="p", pipeline_version="v")
    with pytest.raises(GcsLayoutError):
        GcsLayoutV2(bucket_name="has/slash", project_name="p", pipeline_version="v")


def test_construction_rejects_invalid_project_or_pipeline_segment():
    with pytest.raises(ValueError):
        GcsLayoutV2(bucket_name="b", project_name="../escape", pipeline_version="v")
    with pytest.raises(ValueError):
        GcsLayoutV2(bucket_name="b", project_name="p", pipeline_version="")


# ── canonical_blob ───────────────────────────────────────────────────────────────


def test_canonical_blob_uses_two_char_shard_prefix(layout):
    object_name = layout.canonical_blob(f"sha256:{_BLOB_HEX}")
    assert object_name == (
        f"aigear_sklearn_pipeline/logistic_regression/registry/v2/_objects/sha256/"
        f"{_BLOB_HEX[:2]}/{_BLOB_HEX}"
    )


def test_canonical_blob_accepts_typed_id_or_bare_or_typed_string(layout):
    from_typed_id = layout.canonical_blob(TypedId.from_bare(_BLOB_HEX))
    from_bare_string = layout.canonical_blob(_BLOB_HEX)
    from_typed_string = layout.canonical_blob(f"sha256:{_BLOB_HEX}")
    assert from_typed_id == from_bare_string == from_typed_string


# ── staging ──────────────────────────────────────────────────────────────────────


def test_staging_builds_expected_path(layout):
    object_name = layout.staging(
        run_id="run-1",
        step_name="training",
        attempt_no=2,
        operation_id="op-1",
        output_name="model",
        payload_kind="components",
        payload_key="primary",
        file_name="model.onnx",
    )
    assert object_name == (
        "aigear_sklearn_pipeline/logistic_regression/registry/v2/_staging/"
        "run-1/training/2/op-1/model/components/primary/model.onnx"
    )


def test_staging_rejects_invalid_payload_kind(layout):
    with pytest.raises(GcsLayoutError):
        layout.staging(
            run_id="run-1",
            step_name="training",
            attempt_no=1,
            operation_id="op-1",
            output_name="model",
            payload_kind="not-a-kind",
            payload_key="primary",
            file_name="model.onnx",
        )


@pytest.mark.parametrize("attempt_no", [0, -1, "2", 2.0, True])
def test_staging_rejects_invalid_attempt_no(layout, attempt_no):
    with pytest.raises(GcsLayoutError):
        layout.staging(
            run_id="run-1",
            step_name="training",
            attempt_no=attempt_no,
            operation_id="op-1",
            output_name="model",
            payload_kind="components",
            payload_key="primary",
            file_name="model.onnx",
        )


# ── quarantine / quarantine_manifest ──────────────────────────────────────────────


def test_quarantine_builds_expected_path(layout):
    object_name = layout.quarantine(
        import_operation_id="import-1",
        payload_kind="attachments",
        payload_key="metrics",
        file_name="evaluation.json",
    )
    assert object_name == (
        "aigear_sklearn_pipeline/logistic_regression/registry/v2/_quarantine/"
        "import-1/attachments/metrics/evaluation.json"
    )


def test_quarantine_manifest_builds_expected_path(layout):
    object_name = layout.quarantine_manifest("import-1")
    assert object_name == (
        "aigear_sklearn_pipeline/logistic_regression/registry/v2/_quarantine/"
        "import-1/source-manifest.json"
    )


# ── asset_projection / run_projection ─────────────────────────────────────────────


def test_asset_projection_builds_expected_path(layout):
    object_name = layout.asset_projection("model", "logistic_regression", "v2026.07.23")
    assert object_name == (
        "aigear_sklearn_pipeline/logistic_regression/registry/v2/assets/"
        "model/logistic_regression/versions/v2026.07.23/manifest.json"
    )


def test_run_projection_builds_expected_path(layout):
    object_name = layout.run_projection("run-20260723-001", "training", "model")
    assert object_name == (
        "aigear_sklearn_pipeline/logistic_regression/registry/v2/runs/"
        "run-20260723-001/steps/training/outputs/model/occurrence.json"
    )


# ── generate -> parse round trip ──────────────────────────────────────────────────


def test_round_trip_canonical_blob(layout):
    object_name = layout.canonical_blob(_BLOB_HEX)
    parsed = layout.parse_and_validate(layout.to_uri(object_name))
    assert parsed == ParsedObjectPath(
        kind="canonical_blob", fields={"blob_id": f"sha256:{_BLOB_HEX}"}
    )


def test_round_trip_staging(layout):
    object_name = layout.staging(
        run_id="run-1",
        step_name="training",
        attempt_no=3,
        operation_id="op-1",
        output_name="model",
        payload_kind="components",
        payload_key="primary",
        file_name="model.onnx",
    )
    parsed = layout.parse_and_validate(layout.to_uri(object_name))
    assert parsed.kind == "staging"
    assert parsed.fields == {
        "run_id": "run-1",
        "step_name": "training",
        "attempt_no": "3",
        "operation_id": "op-1",
        "output_name": "model",
        "payload_kind": "components",
        "payload_key": "primary",
        "file_name": "model.onnx",
    }


def test_round_trip_quarantine(layout):
    object_name = layout.quarantine(
        import_operation_id="import-1",
        payload_kind="attachments",
        payload_key="metrics",
        file_name="evaluation.json",
    )
    parsed = layout.parse_and_validate(layout.to_uri(object_name))
    assert parsed.kind == "quarantine"
    assert parsed.fields["import_operation_id"] == "import-1"


def test_round_trip_quarantine_manifest(layout):
    object_name = layout.quarantine_manifest("import-1")
    parsed = layout.parse_and_validate(layout.to_uri(object_name))
    assert parsed == ParsedObjectPath(
        kind="quarantine_manifest", fields={"import_operation_id": "import-1"}
    )


def test_round_trip_asset_projection(layout):
    object_name = layout.asset_projection("model", "logistic_regression", "v2026.07.23")
    parsed = layout.parse_and_validate(layout.to_uri(object_name))
    assert parsed == ParsedObjectPath(
        kind="asset_projection",
        fields={
            "asset_type": "model",
            "asset_name": "logistic_regression",
            "display_version": "v2026.07.23",
        },
    )


def test_round_trip_run_projection(layout):
    object_name = layout.run_projection("run-1", "training", "model")
    parsed = layout.parse_and_validate(layout.to_uri(object_name))
    assert parsed == ParsedObjectPath(
        kind="run_projection",
        fields={"run_id": "run-1", "step_name": "training", "output_name": "model"},
    )


# ── parse_and_validate: rejections ────────────────────────────────────────────────


def test_parse_rejects_non_gs_uri(layout):
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate("https://example.com/foo")


def test_parse_rejects_bucket_mismatch(layout):
    object_name = layout.canonical_blob(_BLOB_HEX)
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate(f"gs://some-other-bucket/{object_name}")


def test_parse_rejects_root_escape_outside_managed_prefix(layout):
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate(
            "gs://aigear-prod-assets/aigear_sklearn_pipeline/other_pipeline/registry/v2/_objects/sha256/ab/"
            + _BLOB_HEX
        )


def test_parse_rejects_double_slash(layout):
    object_name = layout.canonical_blob(_BLOB_HEX)
    broken = object_name.replace("_objects/sha256", "_objects//sha256")
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate(layout.to_uri(broken))


def test_parse_rejects_unrecognized_prefix(layout):
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate(layout.to_uri(f"{layout.object_prefix}/_unknown/thing"))


def _oversized_layout() -> GcsLayoutV2:
    # Each individual segment (including project_name/pipeline_version, which
    # are also validated by the same 128-byte-per-segment rule) is allowed to
    # be as long as 128 bytes; combining several of them is what is needed to
    # exceed the 900-byte *whole object name* cap.
    return GcsLayoutV2(
        bucket_name="aigear-prod-assets",
        project_name="p" * 128,
        pipeline_version="q" * 128,
    )


def test_parse_rejects_object_name_exceeding_byte_limit():
    long_segment = "a" * 128
    oversized = _oversized_layout()
    object_name = (
        f"{oversized.object_prefix}/_staging/{long_segment}/{long_segment}/1/"
        f"{long_segment}/{long_segment}/components/{long_segment}/{long_segment}"
    )
    assert len(object_name.encode("utf-8")) > MAX_OBJECT_NAME_BYTES
    with pytest.raises(GcsLayoutError):
        oversized.parse_and_validate(oversized.to_uri(object_name))


def test_build_rejects_object_name_exceeding_byte_limit():
    long_segment = "a" * 128
    oversized = _oversized_layout()
    with pytest.raises(GcsLayoutError):
        oversized.staging(
            run_id=long_segment,
            step_name=long_segment,
            attempt_no=1,
            operation_id=long_segment,
            output_name=long_segment,
            payload_kind="components",
            payload_key=long_segment,
            file_name=long_segment,
        )


def test_parse_rejects_malformed_canonical_blob_shard_mismatch(layout):
    object_name = f"{layout.object_prefix}/_objects/sha256/ff/{_BLOB_HEX}"
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate(layout.to_uri(object_name))


def test_parse_rejects_wrong_segment_count_for_run_projection(layout):
    object_name = f"{layout.object_prefix}/runs/run-1/steps/training/outputs/occurrence.json"
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate(layout.to_uri(object_name))


def test_parse_rejects_dot_dot_segment(layout):
    object_name = f"{layout.object_prefix}/assets/model/../versions/v1/manifest.json"
    with pytest.raises(GcsLayoutError):
        layout.parse_and_validate(layout.to_uri(object_name))


def test_payload_kinds_is_closed_enum():
    assert PAYLOAD_KINDS == {"components", "attachments"}

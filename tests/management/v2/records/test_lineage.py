from __future__ import annotations

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import InvalidSegmentError
from aigear.management.v2.records.lineage import (
    AttachmentEdge,
    ComponentEdge,
    InvalidLineageEdgeError,
    LineageEdge,
    OwnerKind,
    compute_attachment_edge_id,
    compute_component_edge_id,
    compute_lineage_edge_id,
)

_FP = TypedId.from_bare("aa" * 32)
_OUTPUT_OCC = TypedId.from_bare("bb" * 32)
_INPUT_OCC = TypedId.from_bare("cc" * 32)
_ASSET_VERSION_ID = TypedId.from_bare("dd" * 32)
_BLOB_ID = TypedId.from_bare("ee" * 32)


# ── LineageEdge ──────────────────────────────────────────────────────────────


def test_compute_lineage_edge_id_is_deterministic():
    a = compute_lineage_edge_id(_OUTPUT_OCC, "training_features", _INPUT_OCC)
    b = compute_lineage_edge_id(_OUTPUT_OCC, "training_features", _INPUT_OCC)
    assert a == b


def test_compute_lineage_edge_id_changes_with_binding_name():
    a = compute_lineage_edge_id(_OUTPUT_OCC, "features_a", _INPUT_OCC)
    b = compute_lineage_edge_id(_OUTPUT_OCC, "features_b", _INPUT_OCC)
    assert a != b


def test_lineage_edge_self_verifies_id():
    edge_id = compute_lineage_edge_id(_OUTPUT_OCC, "training_features", _INPUT_OCC)
    edge = LineageEdge(
        schema_version="2.0",
        environment_fingerprint=_FP,
        edge_id=edge_id,
        output_occurrence_id=_OUTPUT_OCC,
        input_occurrence_id=_INPUT_OCC,
        binding_name="training_features",
        run_id="run-1",
    )
    assert edge.edge_id == edge_id


def test_lineage_edge_rejects_mismatched_id():
    wrong_id = TypedId.from_bare("11" * 32)
    with pytest.raises(InvalidLineageEdgeError):
        LineageEdge(
            schema_version="2.0",
            environment_fingerprint=_FP,
            edge_id=wrong_id,
            output_occurrence_id=_OUTPUT_OCC,
            input_occurrence_id=_INPUT_OCC,
            binding_name="training_features",
            run_id="run-1",
        )


# ── ComponentEdge ────────────────────────────────────────────────────────────


def test_compute_component_edge_id_is_deterministic():
    a = compute_component_edge_id(_ASSET_VERSION_ID, "model", "model.onnx", _BLOB_ID)
    b = compute_component_edge_id(_ASSET_VERSION_ID, "model", "model.onnx", _BLOB_ID)
    assert a == b


def test_component_edge_self_verifies_id():
    edge_id = compute_component_edge_id(_ASSET_VERSION_ID, "model", "model.onnx", _BLOB_ID)
    edge = ComponentEdge(
        schema_version="2.0",
        environment_fingerprint=_FP,
        component_edge_id=edge_id,
        asset_version_id=_ASSET_VERSION_ID,
        blob_id=_BLOB_ID,
        role="model",
        logical_name="model.onnx",
    )
    assert edge.component_edge_id == edge_id


def test_component_edge_rejects_mismatched_id():
    wrong_id = TypedId.from_bare("11" * 32)
    with pytest.raises(InvalidLineageEdgeError):
        ComponentEdge(
            schema_version="2.0",
            environment_fingerprint=_FP,
            component_edge_id=wrong_id,
            asset_version_id=_ASSET_VERSION_ID,
            blob_id=_BLOB_ID,
            role="model",
            logical_name="model.onnx",
        )


def test_component_edge_rejects_invalid_role_segment():
    with pytest.raises(InvalidSegmentError):
        compute_component_edge_id(_ASSET_VERSION_ID, "bad role", "model.onnx", _BLOB_ID)


# ── AttachmentEdge ───────────────────────────────────────────────────────────


def test_compute_attachment_edge_id_is_deterministic():
    a = compute_attachment_edge_id(OwnerKind.OCCURRENCE, _OUTPUT_OCC.typed, "metrics", "evaluation.json", _BLOB_ID)
    b = compute_attachment_edge_id(OwnerKind.OCCURRENCE, _OUTPUT_OCC.typed, "metrics", "evaluation.json", _BLOB_ID)
    assert a == b


def test_compute_attachment_edge_id_changes_with_owner_kind():
    a = compute_attachment_edge_id(OwnerKind.OCCURRENCE, "owner-1", "metrics", "evaluation.json", _BLOB_ID)
    b = compute_attachment_edge_id(OwnerKind.RELEASE, "owner-1", "metrics", "evaluation.json", _BLOB_ID)
    assert a != b


def test_attachment_edge_self_verifies_id():
    edge_id = compute_attachment_edge_id(
        OwnerKind.OCCURRENCE, _OUTPUT_OCC.typed, "metrics", "evaluation.json", _BLOB_ID
    )
    edge = AttachmentEdge(
        schema_version="2.0",
        environment_fingerprint=_FP,
        attachment_edge_id=edge_id,
        owner_kind=OwnerKind.OCCURRENCE,
        owner_id=_OUTPUT_OCC.typed,
        attachment_kind="metrics",
        logical_name="evaluation.json",
        blob_id=_BLOB_ID,
        media_type="application/json",
    )
    assert edge.attachment_edge_id == edge_id


def test_attachment_edge_rejects_mismatched_id():
    wrong_id = TypedId.from_bare("11" * 32)
    with pytest.raises(InvalidLineageEdgeError):
        AttachmentEdge(
            schema_version="2.0",
            environment_fingerprint=_FP,
            attachment_edge_id=wrong_id,
            owner_kind=OwnerKind.OCCURRENCE,
            owner_id=_OUTPUT_OCC.typed,
            attachment_kind="metrics",
            logical_name="evaluation.json",
            blob_id=_BLOB_ID,
            media_type="application/json",
        )


def test_attachment_edge_rejects_non_owner_kind_enum():
    with pytest.raises(InvalidLineageEdgeError):
        compute_attachment_edge_id("occurrence", "owner-1", "metrics", "evaluation.json", _BLOB_ID)


def test_attachment_edge_rejects_empty_owner_id():
    with pytest.raises(InvalidLineageEdgeError):
        compute_attachment_edge_id(OwnerKind.OCCURRENCE, "", "metrics", "evaluation.json", _BLOB_ID)


def test_attachment_edge_rejects_invalid_attachment_kind_segment():
    with pytest.raises(InvalidSegmentError):
        compute_attachment_edge_id(OwnerKind.OCCURRENCE, "owner-1", "bad kind", "evaluation.json", _BLOB_ID)

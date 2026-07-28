from __future__ import annotations

import pytest

from aigear.management.v2.firestore_paths import (
    FirestorePathsV2,
    InvalidFirestorePathError,
)
from aigear.management.v2.identifiers import TypedId

_HEX = "ab" * 32


@pytest.fixture()
def paths() -> FirestorePathsV2:
    return FirestorePathsV2(
        project_name="aigear_sklearn_pipeline", pipeline_version="logistic_regression"
    )


def test_control_document_path(paths):
    assert paths.control_document == (
        "aigear_projects/aigear_sklearn_pipeline/pipelines/logistic_regression/"
        "registries/v2"
    )


def test_construction_validates_project_and_pipeline_segments():
    with pytest.raises(ValueError):
        FirestorePathsV2(project_name="../escape", pipeline_version="v")
    with pytest.raises(ValueError):
        FirestorePathsV2(project_name="p", pipeline_version="")


def test_blobs_collection_and_document(paths):
    assert paths.blobs_collection() == f"{paths.control_document}/blobs"
    assert paths.blob_document(_HEX) == f"{paths.control_document}/blobs/{_HEX}"


@pytest.mark.parametrize(
    "blob_id",
    [_HEX, f"sha256:{_HEX}", TypedId.from_bare(_HEX)],
)
def test_blob_document_accepts_bare_typed_or_typed_id(paths, blob_id):
    assert paths.blob_document(blob_id) == f"{paths.control_document}/blobs/{_HEX}"


def test_blob_location_revision_document(paths):
    assert paths.blob_location_revisions_collection(_HEX) == (
        f"{paths.control_document}/blobs/{_HEX}/location_revisions"
    )
    assert paths.blob_location_revision_document(_HEX, 2) == (
        f"{paths.control_document}/blobs/{_HEX}/location_revisions/2"
    )


@pytest.mark.parametrize("revision", [0, -1, "2", 2.0, True])
def test_blob_location_revision_document_rejects_invalid_revision(paths, revision):
    with pytest.raises(InvalidFirestorePathError):
        paths.blob_location_revision_document(_HEX, revision)


def test_blob_claim_document(paths):
    assert paths.blob_claim_document(_HEX) == f"{paths.control_document}/blob_claims/{_HEX}"


def test_asset_version_and_attestation_and_policy_documents(paths):
    assert paths.asset_version_document(_HEX) == (
        f"{paths.control_document}/asset_versions/{_HEX}"
    )
    assert paths.attestation_document(_HEX) == f"{paths.control_document}/attestations/{_HEX}"
    assert paths.policy_decision_head_document(_HEX) == (
        f"{paths.control_document}/policy_decision_heads/{_HEX}"
    )
    assert paths.policy_decision_epoch_document(_HEX) == (
        f"{paths.control_document}/policy_decision_epochs/{_HEX}"
    )


def test_label_document(paths):
    assert paths.label_document(_HEX) == f"{paths.control_document}/labels/{_HEX}"


def test_run_family_documents(paths):
    assert paths.run_document("run-1") == f"{paths.control_document}/runs/run-1"
    assert paths.run_input_binding_document("run-1", "training_features") == (
        f"{paths.control_document}/runs/run-1/input_bindings/training_features"
    )
    assert paths.run_step_instance_document("run-1", "training") == (
        f"{paths.control_document}/runs/run-1/step_instances/training"
    )
    assert paths.run_attempt_document("run-1", "training", 2) == (
        f"{paths.control_document}/runs/run-1/step_instances/training/attempts/2"
    )
    assert paths.run_committed_output_document("run-1", _HEX) == (
        f"{paths.control_document}/runs/run-1/committed_outputs/{_HEX}"
    )


@pytest.mark.parametrize("attempt_no", [0, -1, "1"])
def test_run_attempt_document_rejects_invalid_attempt_no(paths, attempt_no):
    with pytest.raises(InvalidFirestorePathError):
        paths.run_attempt_document("run-1", "training", attempt_no)


def test_occurrence_and_edge_documents(paths):
    assert paths.occurrence_document(_HEX) == f"{paths.control_document}/occurrences/{_HEX}"
    assert paths.lineage_edge_document("edge-1") == (
        f"{paths.control_document}/lineage_edges/edge-1"
    )
    assert paths.component_edge_document("edge-2") == (
        f"{paths.control_document}/component_edges/edge-2"
    )
    assert paths.attachment_edge_document("edge-3") == (
        f"{paths.control_document}/attachment_edges/edge-3"
    )


def test_alias_release_and_service_documents(paths):
    assert paths.alias_document("alias-1") == f"{paths.control_document}/aliases/alias-1"
    assert paths.release_document(_HEX) == (
        f"{paths.control_document}/releases/{_HEX}"
    )
    assert paths.service_release_state_document("model-service") == (
        f"{paths.control_document}/services/model-service/release_state/current"
    )
    assert paths.release_operation_document("op-1") == (
        f"{paths.control_document}/release_operations/op-1"
    )
    assert paths.service_runtime_evidence_document("model-service", _HEX) == (
        f"{paths.control_document}/services/model-service/runtime_evidence/{_HEX}"
    )
    assert paths.service_runtime_authorization_lease_document("model-service", _HEX) == (
        f"{paths.control_document}/services/model-service/"
        f"runtime_authorization_leases/{_HEX}"
    )
    assert paths.restore_operation_document("op-2") == (
        f"{paths.control_document}/restore_operations/op-2"
    )
    assert paths.export_operation_document("export-1") == (
        f"{paths.control_document}/export_operations/export-1"
    )
    assert paths.pin_document("pin-1") == f"{paths.control_document}/pins/pin-1"


def test_operation_outbox_event_tombstone_migration_reconcile_documents(paths):
    assert paths.operation_document(_HEX) == f"{paths.control_document}/operations/{_HEX}"
    assert paths.import_operation_document(_HEX) == (
        f"{paths.control_document}/operations/{_HEX}"
    )
    assert paths.outbox_document(_HEX) == f"{paths.control_document}/outbox/{_HEX}"
    assert paths.event_document(_HEX) == f"{paths.control_document}/events/{_HEX}"
    assert paths.tombstone_document(_HEX) == f"{paths.control_document}/tombstones/{_HEX}"
    assert paths.migration_map_document(_HEX) == (
        f"{paths.control_document}/migration_map/{_HEX}"
    )
    assert paths.reconcile_checkpoint_document("gc-job") == (
        f"{paths.control_document}/reconcile_checkpoints/gc-job"
    )


def test_all_documents_share_the_same_control_document_root(paths):
    all_paths = [
        paths.blob_document(_HEX),
        paths.asset_version_document(_HEX),
        paths.label_document(_HEX),
        paths.run_document("run-1"),
        paths.occurrence_document(_HEX),
    ]
    assert all(path.startswith(paths.control_document + "/") for path in all_paths)

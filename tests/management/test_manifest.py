import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from aigear.management import asset_errors as errors
from aigear.management import manifest as m

NOW = "2026-10-23T04:00:00Z"


def output(path="training/model.pkl", generation="1002"):
    return dict(path=path, size=2048, crc32c="AAAAAA==", generation=generation)


def inputs():
    return [dict(
        kind="managed", run_id="run_A", step_name="preprocessing", step_revision="features_A",
        artifact="feature/features.pkl",
        observed_object=dict(key="running/v1/feature/features.pkl", generation="1001"),
    ), dict(kind="external", name="warehouse", uri="bigquery://project/data/table", version=None)]


def step(path="training/model.pkl", revision="training_B"):
    return dict(status="succeeded", producer_run_id="run_B", producer_step_revision=revision,
                completed_at=NOW, inputs=inputs(), outputs=[output(path)])


def workspace(**changes):
    data = dict(pipeline_version="v1", revision=42, updated_at=NOW, steps={"training": step()})
    data.update(changes)
    return m.WorkspaceManifest(**data)


def operation(**changes):
    data = dict(
        operation_id="operation_1", kind="register", phase="copying",
        source=dict(object=dict(key="running/v1/manifest.json", generation="77"), manifest=workspace()),
        expected_targets=[dict(key="assets/v1/run_B_stamp/training/model.pkl", generation=None)],
        progress=[dict(
            source=dict(key="running/v1/training/model.pkl", generation="1002"),
            target=dict(key="assets/v1/run_B_stamp/training/model.pkl", generation="3002"),
            size=2048, crc32c="AAAAAA==",
        )],
    )
    data.update(changes)
    return m.PendingOperation(**data)


def snapshot(**changes):
    data = dict(
        pipeline_version="v1", revision=1, updated_at=NOW, steps={"training": step()},
        run_id="run_B", run_started_at=NOW, registered_at=NOW, included_steps=["training"],
        sealed=False, source_revision=42, location="assets",
    )
    data.update(changes)
    return m.SnapshotManifest(**data)


def release_file():
    return dict(
        path="model.pkl", size=2048, crc32c="AAAAAA==", generation="4002",
        source_path="training/model.pkl", source_object=dict(key="running/v1/training/model.pkl", generation="1002"),
        step_name="training", producer_run_id="run_B", producer_step_revision="training_B", inputs=inputs(),
    )


def release(**changes):
    data = dict(pipeline_version="v1", revision=3, updated_at=NOW,
                source_pipeline_version="v1", published_at=NOW, files=[release_file()])
    data.update(changes)
    return m.ReleaseManifest(**data)


@pytest.mark.parametrize("factory", [workspace, snapshot, release])
def test_manifest_json_roundtrip(factory):
    manifest = factory()
    assert type(manifest).from_json(manifest.to_json()) == manifest
    assert json.loads(manifest.to_json())["schema_version"] == 1


def test_copy_changes_output_version_without_rewriting_historical_inputs():
    original = workspace()
    copied = step()
    copied["outputs"][0]["generation"] = "9002"
    saved = snapshot(steps={"training": copied})
    restored = m.SnapshotManifest.from_json(saved.to_json())
    assert restored.steps["training"].inputs == original.steps["training"].inputs
    assert restored.steps["training"].inputs[0].run_id == "run_A"
    assert restored.steps["training"].inputs[0].observed_object.generation == "1001"
    assert restored.steps["training"].producer_run_id == "run_B"
    assert restored.steps["training"].producer_step_revision == "training_B"
    assert restored.steps["training"].outputs[0].generation == "9002"
    assert restored.source_revision == 42


def test_utc_normalization_and_microseconds():
    manifest = workspace(updated_at="2026-10-23T12:00:00.123456+08:00")
    assert manifest.updated_at.utcoffset() == timedelta(0)
    assert json.loads(manifest.to_json())["updated_at"] == "2026-10-23T04:00:00.123456Z"


@pytest.mark.parametrize("value", ["2026-10-23T04:00:00", "2026-10-23", 1000, True, None])
def test_naive_or_numeric_times_rejected(value):
    with pytest.raises(ValidationError):
        workspace(updated_at=value)


@pytest.mark.parametrize("value", [0, 2, "1", True, 1.0, None])
def test_unknown_schema_version_is_integrity_failure(value):
    data = workspace().model_dump(mode="json")
    data["schema_version"] = value
    with pytest.raises(errors.IntegrityError) as failure:
        m.WorkspaceManifest.from_json(json.dumps(data))
    assert isinstance(failure.value.cause, ValidationError)
    assert failure.value.__cause__ is failure.value.cause


@pytest.mark.parametrize("value", [1, True, "", "1.2", "-1"])
def test_generations_must_be_decimal_strings(value):
    with pytest.raises(ValidationError):
        m.ArtifactObject(**output(generation=value))


def test_schema_rejects_unknown_fields_and_malformed_json():
    with pytest.raises(ValidationError):
        workspace(unknown=True)
    with pytest.raises(errors.IntegrityError):
        m.WorkspaceManifest.from_json('{"schema_version":')
    with pytest.raises(ValidationError):
        m.ManagedInput(**{**inputs()[0], "unknown": "field"})


@pytest.mark.parametrize("changes", [{"revision": "42"}, {"revision": True}, {"revision": -1}])
def test_manifest_revision_is_counter(changes):
    with pytest.raises(ValidationError):
        workspace(**changes)


def test_production_revision_cannot_be_workspace_counter():
    record = step()
    record["producer_step_revision"] = 42
    with pytest.raises(ValidationError):
        workspace(steps={"training": record})


def test_pending_uploads_and_operation_progress_are_not_committed_members():
    execution = dict(run_id="run_C", run_started_at=NOW, owner_token="owner_1", status="running",
                     current_step="training", step_attempt_token="attempt_1")
    pending = dict(run_id="run_C", step_name="training", step_attempt_token="attempt_1",
                   writes_started=True, inputs=inputs(), outputs=[output("training/new.pkl", "7002")])
    value = workspace(execution=execution, pending_step=pending, pending_operation=operation(error="copy interrupted"))
    roundtrip = m.WorkspaceManifest.from_json(value.to_json())
    assert [o.path for o in roundtrip.committed_files()] == ["training/model.pkl"]
    assert roundtrip.pending_step.outputs[0].generation == "7002"
    assert roundtrip.pending_operation.progress[0].target.generation == "3002"
    assert roundtrip.pending_operation.source.object.generation == "77"
    assert roundtrip.pending_operation.expected_targets[0].generation is None
    assert roundtrip.pending_operation.error == "copy interrupted"
    assert roundtrip.steps["training"].status == "succeeded"


@pytest.mark.parametrize("alter", ["run_id", "step_name", "step_attempt_token"])
def test_pending_step_must_match_owned_attempt(alter):
    execution = dict(run_id="run_C", run_started_at=NOW, owner_token="owner", status="running",
                     current_step="training", step_attempt_token="attempt")
    pending = dict(run_id="run_C", step_name="training", step_attempt_token="attempt")
    pending[alter] = "another"
    with pytest.raises(ValidationError):
        workspace(execution=execution, pending_step=pending)


def test_duplicate_ownership_and_pending_claim_of_another_step_rejected():
    with pytest.raises(ValidationError, match="file paths"):
        workspace(steps={"a": step(), "b": step()})
    execution = dict(run_id="run_C", run_started_at=NOW, owner_token="owner", status="running",
                     current_step="other", step_attempt_token="attempt")
    with pytest.raises(ValidationError, match="another step"):
        workspace(execution=execution, pending_step=dict(run_id="run_C", step_name="other",
                  step_attempt_token="attempt", writes_started=True, outputs=[output()]))


@pytest.mark.parametrize("changes", [
    {"included_steps": []}, {"included_steps": ["training", "training"]},
    {"steps": {"training": {**step(), "status": "stale"}}},
    {"steps": {}, "included_steps": []}, {"retired_at": NOW},
    {"committed": False},
])
def test_snapshot_membership_is_committed_and_valid(changes):
    with pytest.raises(ValidationError):
        snapshot(**changes)


def test_preparing_manifests_have_only_pending_content():
    pending = operation()
    saved = snapshot(committed=False, steps={}, included_steps=[], pending_operation=pending)
    published = release(committed=False, files=[], published_at=None, pending_operation=operation(kind="publish"))
    assert not saved.committed_files()
    assert not published.committed_files()
    for value in (saved, published):
        assert type(value).from_json(value.to_json()) == value


def test_restore_keeps_production_identity_and_replay_watermark():
    value = workspace(
        last_run_id=None, last_run_started_at=None, latest_run_id="run_C", latest_run_started_at=NOW,
        restored_from=dict(run_id="run_B", manifest=dict(key="trash/v1/run_B_stamp/manifest.json", generation="22"),
                           operation_id="restore_1", restored_at=NOW),
    )
    restored = m.WorkspaceManifest.from_json(value.to_json())
    assert restored.last_run_id is None
    assert restored.latest_run_id == "run_C"
    assert restored.steps["training"].producer_run_id == "run_B"


@pytest.mark.parametrize("changes", [
    {"target": "running"}, {"kind": "restore"}, {"error": "x" * 513},
    {"expected_targets": []},
])
def test_invalid_pending_operation_rejected(changes):
    with pytest.raises(ValidationError):
        operation(**changes)


def test_operation_fixed_target_and_source_versions_roundtrip():
    value = operation(kind="restore", target="running", retired_at=NOW)
    assert m.PendingOperation.model_validate_json(value.model_dump_json()) == value
    progress = value.progress[0].model_dump()
    progress["source"]["generation"] = "9999"
    with pytest.raises(ValidationError, match="fixed source"):
        operation(progress=[progress])
    with pytest.raises(ValidationError, match="fixed expected target"):
        operation(expected_targets=[dict(key="other/file", generation="100")])
    with pytest.raises(ValidationError):
        operation(source=dict(object=dict(key="running/v1/manifest.json", generation="99"),
                              manifest=workspace(pending_operation=operation())))


def test_operation_keeps_previous_release_and_backup_sources_for_recovery():
    previous = m.FixedManifest(object=dict(key="models/risk/manifest.json", generation="8"), manifest=release())
    progress = dict(source=dict(key="models/risk/model.pkl", generation="4002"),
                    target=dict(key="backup/models/risk/model.pkl", generation="5002"), size=2048, crc32c="AAAAAA==")
    value = operation(kind="publish", previous_targets=[previous],
        expected_targets=[dict(key="backup/models/risk/model.pkl", generation="3000")], progress=[progress])
    assert m.PendingOperation.model_validate_json(value.model_dump_json()) == value
    assert value.previous_targets[0].manifest.files[0].inputs == release().files[0].inputs


def test_release_rollback_provenance_does_not_invent_a_snapshot_run():
    value = release(restored_from=dict(manifest=dict(key="backup/models/risk/manifest.json", generation="5"),
                                      operation_id="rollback_1", restored_at=NOW))
    assert m.ReleaseManifest.from_json(value.to_json()).restored_from.run_id is None


def test_fixed_source_accepts_management_attempt_but_rejects_started_writes():
    execution = dict(run_id="run_B", run_started_at=NOW, owner_token="owner", status="running",
                     current_step="save_model", step_attempt_token="attempt")
    pending = dict(run_id="run_B", step_name="save_model", step_attempt_token="attempt")
    source_object = dict(key="running/v1/manifest.json", generation="77")
    fixed = m.FixedManifest(object=source_object, manifest=workspace(execution=execution, pending_step=pending))
    assert fixed.manifest.pending_step.outputs == []
    with pytest.raises(ValidationError, match="pending artifact writes"):
        m.FixedManifest(object=source_object,
            manifest=workspace(execution=execution, pending_step={**pending, "writes_started": True}))


def test_dry_run_cannot_report_retired_items():
    with pytest.raises(ValidationError, match="Dry-run"):
        m.RetirementResult(keep_latest=3, dry_run=True,
            retired=[dict(run_id="run_A", uri="gs://train/trash/run_A", reason="retired")])


@pytest.mark.parametrize("changes", [{"files": []}, {"published_at": None}, {"files": [release_file(), release_file()]},
                                    {"source_pipeline_version": "other"}])
def test_release_content_integrity(changes):
    with pytest.raises(ValidationError):
        release(**changes)


def test_four_public_results_roundtrip_and_partial_failure():
    info = m.SnapshotInfo(
        pipeline_version="v1", run_id="run_B", location="assets", uri="gs://train/assets/v1/run_B_stamp/",
        registered_at=NOW, included_steps=["training"], sealed=False, committed=True,
        integrity_status="complete", operation_status="idle",
        sources={"training": dict(producer_run_id="run_B", producer_step_revision="training_B")},
    )
    retirement = m.RetirementResult(keep_latest=3, dry_run=False,
        retired=[dict(run_id="run_A", uri="gs://train/trash/v1/run_A_stamp", reason="retired")],
        incomplete=[dict(run_id="run_B", uri=info.uri, reason="source changed", operation_id="op_1")])
    restore = m.RestoreResult(pipeline_version="v1", run_id="run_B", target="running",
        target_uri="gs://train/running/v1/", operation_status="completed", operation_id="op_2")
    released = m.ReleaseInfo(release_uri="gs://release/models/risk/", backup_uri=None,
        source_pipeline_version="v1", files=[release_file()], operation_status="completed", operation_id="op_3")
    for value in (info, retirement, restore, released):
        assert type(value).model_validate_json(value.model_dump_json()) == value
    cause = OSError("connection lost")
    failure = errors.OperationIncomplete("Retirement incomplete", operation_id="op_1", phase="cleaning",
        cause=cause, partial_result=retirement)
    assert failure.phase == "cleaning"
    assert failure.operation_id == "op_1"
    assert failure.cause is failure.__cause__ is cause
    assert failure.partial_result.incomplete[0].run_id == "run_B"


@pytest.mark.parametrize("keep", [0, -1, True, "3", None])
def test_result_retention_count_is_strict(keep):
    with pytest.raises(ValidationError):
        m.RetirementResult(keep_latest=keep, dry_run=True)


@pytest.mark.parametrize("name", ["ExecutionConflict", "InvalidArtifact", "IntegrityError", "SnapshotConflict",
                                 "OperationIncomplete", "SnapshotNotFound", "BackupUnavailable", "InvalidAssetPath"])
def test_domain_exceptions_keep_failure_context(name):
    cause = RuntimeError("storage failure")
    value = getattr(errors, name)("short summary", operation_id="op", phase="copying", cause=cause, partial_result={"done": 1})
    assert isinstance(value, errors.AssetError)
    assert str(value) == "short summary"
    assert value.cause is value.__cause__ is cause
    assert value.partial_result == {"done": 1}


def test_contract_modules_parse_as_python39():
    root = Path(m.__file__).parent
    for name in ("asset_paths.py", "asset_errors.py", "manifest.py"):
        ast.parse((root / name).read_text(encoding="utf-8"), feature_version=(3, 9))

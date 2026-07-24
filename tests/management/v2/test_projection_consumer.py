from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.finalizer import FinalizeContext, finalize_step_outputs
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.projection_consumer import (
    ProjectionConsumerError,
    ProjectionEvent,
    ProjectionKind,
    compute_projection_event_id,
    consume_projection_event,
)
from aigear.management.v2.records.run import RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec
from aigear.management.v2.staging_upload import StagingOutputDescriptor, StepCompletionMessage
from aigear.management.v2.step_lease import acquire_step_lease, compute_attempt_finalize_operation_id

_FP = TypedId.from_bare("aa" * 32)
_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
_CODE_DIGEST = TypedId.from_bare("22" * 32)
_CONFIG_DIGEST = TypedId.from_bare("33" * 32)
_IMAGE_DIGEST = TypedId.from_bare("11" * 32)
_GRAPH_DIGEST = TypedId.from_bare("44" * 32)
_SCHEMA_CONTRACT_DIGEST = TypedId.from_bare("55" * 32)
_RUNTIME_CONTRACT_DIGEST = TypedId.from_bare("66" * 32)


def _layout() -> GcsLayoutV2:
    return GcsLayoutV2(bucket_name="test-bucket", project_name="proj", pipeline_version="v1")


def _run_spec() -> RunSpec:
    return RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=_GRAPH_DIGEST,
        code_digest=_CODE_DIGEST,
        config_digest=_CONFIG_DIGEST,
        producer_image_digest=_IMAGE_DIGEST,
        steps=(
            StepSpec(
                step_name="train",
                outputs=(OutputSlotSpec(output_name="model", role="model", logical_name="weights"),),
            ),
        ),
    )


def _finalize_context() -> FinalizeContext:
    return FinalizeContext(
        environment_id="test-env",
        environment_fingerprint=_FP,
        schema_version="2.0",
        schema_contract_digest=_SCHEMA_CONTRACT_DIGEST,
        runtime_contract_digest=_RUNTIME_CONTRACT_DIGEST,
        policy_version="policy-v1",
        now=_NOW,
    )


def _produce_committed_output(registry, gcs, layout, *, run_id="run-1", step_name="train", output_name="model"):
    registry.create_run(RunRecord(run_id=run_id, status=RunStatus.PENDING))
    registry.update_run_status(run_id, RunStatus.RUNNING)
    registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=StepStatus.READY))

    attempt = acquire_step_lease(
        registry, run_id=run_id, step_name=step_name, output_names=[output_name],
        resolved_input_bindings=(), environment_fingerprint=_FP, schema_version="2.0",
        owner_principal="worker-vm-1@aigear", now=_NOW,
    )
    operation_id = compute_attempt_finalize_operation_id(run_id, step_name, attempt.attempt_no).bare
    staging_object = layout.staging(
        run_id=run_id, step_name=step_name, attempt_no=attempt.attempt_no, operation_id=operation_id,
        output_name=output_name, payload_kind="components", payload_key="primary", file_name="model.bin",
    )
    data = b"model-bytes"
    snapshot = gcs.put_object(staging_object, data, if_generation_match=0)
    descriptor = StagingOutputDescriptor(
        output_name=output_name, staging_object=staging_object, generation=snapshot.generation,
        digest=TypedId.from_bare(hashlib.sha256(data).hexdigest()), size=len(data),
        media_type="application/octet-stream",
    )
    message = StepCompletionMessage(
        run_id=run_id, step_name=step_name, attempt_no=attempt.attempt_no,
        operation_id=operation_id, outputs=(descriptor,),
    )
    outcome = finalize_step_outputs(registry, gcs, layout, message, _run_spec(), _finalize_context())
    return outcome.outputs[0]


# ── asset_manifest ───────────────────────────────────────────────────────────────


def test_consume_asset_manifest_event_writes_manifest_and_marks_ready():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)

    event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST, subject_id=output.label.label_id,
        projection_schema_version="1.0", projection_source_revision=output.label.projection_source_revision,
    )
    projection = consume_projection_event(registry, gcs, layout, event)

    assert projection.status == "ready"
    assert projection.applied_source_revision == output.label.projection_source_revision
    object_name = layout.asset_projection("model", "weights", "run-1")
    snapshot = gcs.get_live_object(object_name)
    assert snapshot is not None
    assert projection.observed_generation == snapshot.generation
    assert projection.observed_content_sha256 == snapshot.sha256

    label = registry.get_label(output.label.label_id)
    assert label.readable_manifest == projection


def test_consume_asset_manifest_event_is_idempotent_on_replay():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST, subject_id=output.label.label_id,
        projection_schema_version="1.0", projection_source_revision=output.label.projection_source_revision,
    )

    first = consume_projection_event(registry, gcs, layout, event)
    second = consume_projection_event(registry, gcs, layout, event)

    assert second == first
    object_name = layout.asset_projection("model", "weights", "run-1")
    # Still exactly one generation: no redundant GCS write on replay.
    assert gcs.get_live_object(object_name).generation == "1"


def test_consume_asset_manifest_event_cas_updates_on_new_desired_revision():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    first_event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST, subject_id=output.label.label_id,
        projection_schema_version="1.0", projection_source_revision=1,
    )
    consume_projection_event(registry, gcs, layout, first_event)

    label = registry.get_label(output.label.label_id)
    registry.put_label(replace(label, projection_source_revision=2))
    second_event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST, subject_id=output.label.label_id,
        projection_schema_version="1.0", projection_source_revision=2,
    )
    projection = consume_projection_event(registry, gcs, layout, second_event)

    assert projection.applied_source_revision == 2
    object_name = layout.asset_projection("model", "weights", "run-1")
    assert gcs.get_live_object(object_name).generation == "2"


def test_consume_asset_manifest_event_behind_desired_revision_is_a_noop():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    label = registry.get_label(output.label.label_id)
    registry.put_label(replace(label, projection_source_revision=2))

    stale_event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST, subject_id=output.label.label_id,
        projection_schema_version="1.0", projection_source_revision=1,
    )
    projection = consume_projection_event(registry, gcs, layout, stale_event)

    assert projection.status == "pending"
    object_name = layout.asset_projection("model", "weights", "run-1")
    assert gcs.get_live_object(object_name) is None


def test_consume_asset_manifest_event_ahead_of_desired_revision_is_rejected():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    ahead_event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST, subject_id=output.label.label_id,
        projection_schema_version="1.0", projection_source_revision=99,
    )
    with pytest.raises(ProjectionConsumerError, match="ahead of"):
        consume_projection_event(registry, gcs, layout, ahead_event)


def test_consume_asset_manifest_event_rejects_unknown_label():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST, subject_id=TypedId.from_bare("ff" * 32),
        projection_schema_version="1.0", projection_source_revision=1,
    )
    with pytest.raises(ProjectionConsumerError, match="no Label found"):
        consume_projection_event(registry, gcs, layout, event)


# ── committed_run_output ─────────────────────────────────────────────────────────


def test_consume_committed_run_output_event_writes_occurrence_json_and_marks_ready():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)

    event = ProjectionEvent(
        kind=ProjectionKind.COMMITTED_RUN_OUTPUT, subject_id=output.occurrence.committed_output_key,
        projection_schema_version="1.0", projection_source_revision=output.occurrence.projection_source_revision,
    )
    projection = consume_projection_event(registry, gcs, layout, event)

    assert projection.status == "ready"
    object_name = layout.run_projection("run-1", "train", "model")
    snapshot = gcs.get_live_object(object_name)
    assert snapshot is not None
    assert projection.observed_generation == snapshot.generation

    occurrence = registry.get_occurrence(output.occurrence.occurrence_id)
    assert occurrence.readable_occurrence == projection


def test_consume_committed_run_output_event_is_idempotent_on_replay():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    event = ProjectionEvent(
        kind=ProjectionKind.COMMITTED_RUN_OUTPUT, subject_id=output.occurrence.committed_output_key,
        projection_schema_version="1.0", projection_source_revision=1,
    )

    first = consume_projection_event(registry, gcs, layout, event)
    second = consume_projection_event(registry, gcs, layout, event)

    assert second == first
    object_name = layout.run_projection("run-1", "train", "model")
    assert gcs.get_live_object(object_name).generation == "1"


def test_consume_committed_run_output_event_rejects_content_conflict():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    object_name = layout.run_projection("run-1", "train", "model")
    gcs.put_object(object_name, b"some other unrelated content", if_generation_match=0)

    event = ProjectionEvent(
        kind=ProjectionKind.COMMITTED_RUN_OUTPUT, subject_id=output.occurrence.committed_output_key,
        projection_schema_version="1.0", projection_source_revision=1,
    )
    with pytest.raises(ProjectionConsumerError, match="consistency conflict"):
        consume_projection_event(registry, gcs, layout, event)


def test_consume_committed_run_output_event_rejects_unknown_output_key():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    event = ProjectionEvent(
        kind=ProjectionKind.COMMITTED_RUN_OUTPUT, subject_id=TypedId.from_bare("ff" * 32),
        projection_schema_version="1.0", projection_source_revision=1,
    )
    with pytest.raises(ProjectionConsumerError, match="no committed Occurrence found"):
        consume_projection_event(registry, gcs, layout, event)


# ── ProjectionEvent / compute_projection_event_id ────────────────────────────────


def test_compute_projection_event_id_is_deterministic():
    subject_id = TypedId.from_bare("aa" * 32)
    first = compute_projection_event_id(ProjectionKind.ASSET_MANIFEST, subject_id, "1.0", 1, 0)
    second = compute_projection_event_id(ProjectionKind.ASSET_MANIFEST, subject_id, "1.0", 1, 0)
    assert first == second


def test_compute_projection_event_id_changes_with_repair_epoch():
    subject_id = TypedId.from_bare("aa" * 32)
    first = compute_projection_event_id(ProjectionKind.ASSET_MANIFEST, subject_id, "1.0", 1, 0)
    second = compute_projection_event_id(ProjectionKind.ASSET_MANIFEST, subject_id, "1.0", 1, 1)
    assert first != second


def test_projection_event_rejects_negative_source_revision():
    with pytest.raises(ProjectionConsumerError, match="non-negative"):
        ProjectionEvent(
            kind=ProjectionKind.ASSET_MANIFEST, subject_id=TypedId.from_bare("aa" * 32),
            projection_schema_version="1.0", projection_source_revision=-1,
        )


def test_projection_event_rejects_non_typed_id_subject():
    with pytest.raises(ProjectionConsumerError, match="TypedId"):
        ProjectionEvent(
            kind=ProjectionKind.ASSET_MANIFEST, subject_id="not-a-typed-id",
            projection_schema_version="1.0", projection_source_revision=1,
        )

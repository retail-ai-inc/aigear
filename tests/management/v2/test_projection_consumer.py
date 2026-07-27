from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.finalizer import FinalizeContext, finalize_step_outputs
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.outbox_worker import drain_projection_outbox
from aigear.management.v2.projection_consumer import (
    ProjectionConsumerError,
    ProjectionEvent,
    ProjectionKind,
    acknowledge_projection_task,
    acquire_projection_task,
    compute_projection_event_id,
    consume_projection_event,
    render_projection_task,
    request_projection_repair,
    verify_projection_completion,
)
from aigear.management.v2.records.run import RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec
from aigear.management.v2.records.outbox import OutboxEventRecord, OutboxStatus
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


def test_projection_ack_loss_allows_fenced_lease_takeover():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST,
        subject_id=output.label.label_id,
        projection_schema_version="1.0",
        projection_source_revision=1,
    )
    first = registry.run_atomic(
        lambda tx: acquire_projection_task(
            tx,
            layout,
            event,
            worker_principal="writer-1@example.com",
            now=_NOW,
            lease_ttl=timedelta(minutes=1),
        )
    )
    first_evidence = render_projection_task(registry, gcs, layout, first)

    second = registry.run_atomic(
        lambda tx: acquire_projection_task(
            tx,
            layout,
            event,
            worker_principal="writer-2@example.com",
            now=_NOW + timedelta(minutes=2),
        )
    )
    assert second.delivery_fencing_token > first.delivery_fencing_token
    with pytest.raises(ProjectionConsumerError, match="stale or unauthorized"):
        registry.run_atomic(
            lambda tx: acknowledge_projection_task(
                tx,
                layout,
                first,
                first_evidence,
                now=_NOW + timedelta(minutes=2),
            )
        )

    second_evidence = render_projection_task(registry, gcs, layout, second)
    projection = registry.run_atomic(
        lambda tx: acknowledge_projection_task(
            tx,
            layout,
            second,
            second_evidence,
            now=_NOW + timedelta(minutes=2),
        )
    )
    assert projection.status == "ready"
    outbox = registry.get_outbox_event(event.event_id)
    assert outbox.status.value == "delivered"
    assert outbox.delivery_attempts == 2


def test_projection_completion_must_match_registry_path_and_exact_generation():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    event = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST,
        subject_id=output.label.label_id,
        projection_schema_version="1.0",
        projection_source_revision=1,
    )
    task = registry.run_atomic(
        lambda tx: acquire_projection_task(
            tx, layout, event, worker_principal="writer-1", now=_NOW
        )
    )
    evidence = render_projection_task(registry, gcs, layout, task)

    with pytest.raises(ProjectionConsumerError, match="Registry-derived path"):
        verify_projection_completion(
            registry,
            gcs,
            layout,
            task,
            replace(evidence, object_name="unrelated/projection.json"),
        )
    with pytest.raises(ProjectionConsumerError, match="digest"):
        verify_projection_completion(
            registry,
            gcs,
            layout,
            task,
            replace(evidence, content_sha256="0" * 64),
        )

    verify_projection_completion(registry, gcs, layout, task, evidence)


def test_bounded_outbox_worker_isolates_poison_event_and_continues_batch():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    asset_object = layout.asset_projection("model", "weights", "run-1")
    gcs.put_object(asset_object, b"foreign-content", if_generation_match=0)

    result = drain_projection_outbox(
        registry,
        gcs,
        layout,
        worker_principal="projection-writer",
        now=_NOW,
        batch_size=10,
    )

    assert result.scanned == 2
    assert result.succeeded == 1
    assert len(result.failed_event_ids) == 1
    failed = registry.get_outbox_event(TypedId.from_typed(result.failed_event_ids[0]))
    assert failed.status == OutboxStatus.DEAD_LETTER
    run_event = ProjectionEvent(
        kind=ProjectionKind.COMMITTED_RUN_OUTPUT,
        subject_id=output.occurrence.committed_output_key,
        projection_schema_version="1.0",
        projection_source_revision=1,
    )
    assert registry.get_outbox_event(run_event.event_id).status == OutboxStatus.DELIVERED


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
    registry.put_outbox_event(
        OutboxEventRecord.pending(
            schema_version="2.0",
            kind=second_event.kind,
            subject_id=second_event.subject_id,
            projection_schema_version=second_event.projection_schema_version,
            projection_source_revision=second_event.projection_source_revision,
        )
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
    assert registry.get_outbox_event(event.event_id).status == OutboxStatus.DEAD_LETTER
    occurrence = registry.get_occurrence(output.occurrence.occurrence_id)
    assert occurrence.readable_occurrence.status == "conflict"


def test_projection_repair_epoch_recreates_deleted_asset_projection():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    original = ProjectionEvent(
        kind=ProjectionKind.ASSET_MANIFEST,
        subject_id=output.label.label_id,
        projection_schema_version="1.0",
        projection_source_revision=1,
    )
    first = consume_projection_event(registry, gcs, layout, original, now=_NOW)
    object_name = layout.asset_projection("model", "weights", "run-1")
    gcs._live.pop(object_name)

    repair = registry.run_atomic(
        lambda tx: request_projection_repair(
            tx,
            layout,
            kind=ProjectionKind.ASSET_MANIFEST,
            subject_id=output.label.label_id,
            expected_source_revision=1,
            expected_repair_epoch=0,
            requested_by="reconciler@test",
            reason="ready object is missing",
            now=_NOW + timedelta(minutes=1),
        )
    )
    repaired = consume_projection_event(
        registry, gcs, layout, repair, now=_NOW + timedelta(minutes=1)
    )

    assert repair.projection_repair_epoch == 1
    assert repaired.status == "ready"
    assert repaired.applied_repair_epoch == 1
    assert repaired.observed_generation != first.observed_generation


def test_immutable_run_projection_repair_refuses_to_overwrite_live_content():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    output = _produce_committed_output(registry, gcs, layout)
    original = ProjectionEvent(
        kind=ProjectionKind.COMMITTED_RUN_OUTPUT,
        subject_id=output.occurrence.committed_output_key,
        projection_schema_version="1.0",
        projection_source_revision=1,
    )
    consume_projection_event(registry, gcs, layout, original, now=_NOW)
    repair = registry.run_atomic(
        lambda tx: request_projection_repair(
            tx,
            layout,
            kind=ProjectionKind.COMMITTED_RUN_OUTPUT,
            subject_id=output.occurrence.committed_output_key,
            expected_source_revision=1,
            expected_repair_epoch=0,
            requested_by="operator@test",
            reason="forced verification",
            now=_NOW + timedelta(minutes=1),
        )
    )

    with pytest.raises(ProjectionConsumerError, match="consistency conflict"):
        consume_projection_event(
            registry, gcs, layout, repair, now=_NOW + timedelta(minutes=1)
        )
    assert registry.get_outbox_event(repair.event_id).status == OutboxStatus.DEAD_LETTER


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

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.finalizer import FinalizeContext, FinalizeError, finalize_step_outputs
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.blob import AvailabilityState
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    compute_committed_output_key,
    compute_metrics_digest,
    compute_occurrence_id,
    compute_resolved_inputs_digest,
)
from aigear.management.v2.records.run import (
    AttemptStatus,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
)
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec
from aigear.management.v2.staging_upload import (
    StagingOutputDescriptor,
    StagingUploadError,
    StepCompletionMessage,
)
from aigear.management.v2.step_lease import acquire_step_lease, compute_attempt_finalize_operation_id

_FP = TypedId.from_bare("aa" * 32)
_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
_CODE_DIGEST = TypedId.from_bare("22" * 32)
_CONFIG_DIGEST = TypedId.from_bare("33" * 32)
_IMAGE_DIGEST = TypedId.from_bare("11" * 32)
_GRAPH_DIGEST = TypedId.from_bare("44" * 32)
_SCHEMA_CONTRACT_DIGEST = TypedId.from_bare("55" * 32)
_RUNTIME_CONTRACT_DIGEST = TypedId.from_bare("66" * 32)


def _run_spec(output_name: str = "model") -> RunSpec:
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
                outputs=(OutputSlotSpec(output_name=output_name, role="model", logical_name="weights"),),
            ),
        ),
    )


def _context() -> FinalizeContext:
    return FinalizeContext(
        environment_id="test-env",
        environment_fingerprint=_FP,
        schema_version="2.0",
        schema_contract_digest=_SCHEMA_CONTRACT_DIGEST,
        runtime_contract_digest=_RUNTIME_CONTRACT_DIGEST,
        policy_version="policy-v1",
        now=_NOW,
    )


def _layout() -> GcsLayoutV2:
    return GcsLayoutV2(bucket_name="test-bucket", project_name="proj", pipeline_version="v1")


def _lease(registry, *, run_id="run-1", step_name="train", output_name="model", now=_NOW, owner="worker-vm-1@aigear"):
    if registry.get_run(run_id) is None:
        registry.create_run(RunRecord(run_id=run_id, status=RunStatus.PENDING))
        registry.update_run_status(run_id, RunStatus.RUNNING)
        registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=StepStatus.READY))
    return acquire_step_lease(
        registry,
        run_id=run_id,
        step_name=step_name,
        output_names=[output_name],
        resolved_input_bindings=(),
        environment_fingerprint=_FP,
        schema_version="2.0",
        owner_principal=owner,
        now=now,
    )


def _stage_output(
    gcs: FakeGcsClient,
    layout: GcsLayoutV2,
    *,
    run_id: str,
    step_name: str,
    attempt_no: int,
    operation_id: str,
    output_name: str,
    data: bytes = b"model-bytes",
) -> StagingOutputDescriptor:
    staging_object = layout.staging(
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt_no,
        operation_id=operation_id,
        output_name=output_name,
        payload_kind="components",
        payload_key="primary",
        file_name="model.bin",
    )
    snapshot = gcs.put_object(staging_object, data, if_generation_match=0)
    digest = TypedId.from_bare(hashlib.sha256(data).hexdigest())
    return StagingOutputDescriptor(
        output_name=output_name,
        staging_object=staging_object,
        generation=snapshot.generation,
        digest=digest,
        size=len(data),
        media_type="application/octet-stream",
    )


def _completion_message(attempt, descriptor, *, run_id="run-1", step_name="train") -> StepCompletionMessage:
    operation_id = compute_attempt_finalize_operation_id(run_id, step_name, attempt.attempt_no).bare
    return StepCompletionMessage(
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        outputs=(descriptor,),
    )


def _lease_and_stage(registry, gcs, layout, *, run_id="run-1", step_name="train", output_name="model", now=_NOW):
    attempt = _lease(registry, run_id=run_id, step_name=step_name, output_name=output_name, now=now)
    operation_id = compute_attempt_finalize_operation_id(run_id, step_name, attempt.attempt_no).bare
    descriptor = _stage_output(
        gcs,
        layout,
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        output_name=output_name,
    )
    message = _completion_message(attempt, descriptor, run_id=run_id, step_name=step_name)
    return attempt, message


def test_single_output_success_path():
    registry = FakeRegistryV2()
    gcs = FakeGcsClient()
    layout = _layout()
    run_spec = _run_spec()

    _attempt, message = _lease_and_stage(registry, gcs, layout)
    outcome = finalize_step_outputs(registry, gcs, layout, message, run_spec, _context())

    assert outcome.run.status == RunStatus.SUCCEEDED
    assert outcome.run.remaining_required_steps == 0
    assert outcome.step.status == StepStatus.SUCCEEDED
    assert outcome.attempt.status == AttemptStatus.SUCCEEDED
    assert len(outcome.outputs) == 1

    output = outcome.outputs[0]
    assert output.occurrence.status == OccurrenceStatus.COMMITTED
    assert output.occurrence.asset_version_id == output.asset_version.asset_version_id
    assert output.asset_version.reference_epoch == 1
    assert output.asset_version.components[0].role == "model"
    assert output.asset_version.components[0].logical_name == "weights"
    assert output.blob.availability_state == AvailabilityState.READY
    assert output.blob.blob_id == output.asset_version.components[0].blob_id
    assert output.label.asset_version_id == output.asset_version.asset_version_id
    assert output.label.display_version == "run-1"


def test_replay_with_same_fingerprint_returns_same_outcome():
    registry = FakeRegistryV2()
    gcs = FakeGcsClient()
    layout = _layout()
    run_spec = _run_spec()

    _attempt, message = _lease_and_stage(registry, gcs, layout)

    first = finalize_step_outputs(registry, gcs, layout, message, run_spec, _context())
    second = finalize_step_outputs(registry, gcs, layout, message, run_spec, _context())

    assert second.outputs[0].occurrence.occurrence_id == first.outputs[0].occurrence.occurrence_id
    assert second.outputs[0].asset_version.asset_version_id == first.outputs[0].asset_version.asset_version_id
    assert second.outputs[0].asset_version.reference_epoch == first.outputs[0].asset_version.reference_epoch
    assert second.run.status == RunStatus.SUCCEEDED


def test_output_already_committed_by_other_attempt_is_rejected():
    registry = FakeRegistryV2()
    gcs = FakeGcsClient()
    layout = _layout()
    run_spec = _run_spec()

    attempt, message = _lease_and_stage(registry, gcs, layout)

    rogue_occurrence_id = compute_occurrence_id("run-1", "train", 99, "model")
    committed_output_key = compute_committed_output_key("run-1", "train", "model")
    registry.put_occurrence(
        OccurrenceRecord(
            schema_version="2.0",
            environment_fingerprint=_FP,
            occurrence_id=rogue_occurrence_id,
            run_id="run-1",
            step_name="train",
            attempt_no=99,
            fencing_token=99,
            output_name="model",
            committed_output_key=committed_output_key,
            resolved_input_bindings=(),
            resolved_inputs_digest=compute_resolved_inputs_digest(()),
            metrics={},
            metrics_digest=compute_metrics_digest({}),
            status=OccurrenceStatus.COMMITTED,
            operation_id="rogue-op",
            asset_version_id=TypedId.from_bare("77" * 32),
            asset_type="model",
            asset_name="weights",
            finalization_attestation_ref=TypedId.from_bare("88" * 32),
        )
    )

    with pytest.raises(FinalizeError, match="already committed"):
        finalize_step_outputs(registry, gcs, layout, message, run_spec, _context())

    # Rejected before any write: the real attempt's own status is untouched.
    unchanged = registry.get_attempt("run-1", "train", attempt.attempt_no)
    assert unchanged.status == AttemptStatus.LEASED


def test_finalize_rejects_stale_attempt_after_takeover():
    registry = FakeRegistryV2()
    gcs = FakeGcsClient()
    layout = _layout()
    run_spec = _run_spec()

    _first_attempt, stale_message = _lease_and_stage(registry, gcs, layout, now=_NOW)

    later = _NOW + timedelta(seconds=200)
    acquire_step_lease(
        registry,
        run_id="run-1",
        step_name="train",
        output_names=["model"],
        resolved_input_bindings=(),
        environment_fingerprint=_FP,
        schema_version="2.0",
        owner_principal="worker-vm-2@aigear",
        now=later,
    )

    with pytest.raises(FinalizeError, match="no longer the current attempt"):
        finalize_step_outputs(registry, gcs, layout, stale_message, run_spec, _context())


def test_finalize_rejects_run_not_running():
    registry = FakeRegistryV2()
    gcs = FakeGcsClient()
    layout = _layout()
    run_spec = _run_spec()

    attempt, message = _lease_and_stage(registry, gcs, layout)
    registry.update_run_status("run-1", RunStatus.CANCELLING)

    with pytest.raises(FinalizeError, match="not running"):
        finalize_step_outputs(registry, gcs, layout, message, run_spec, _context())


def test_finalize_rejects_message_with_unknown_output():
    registry = FakeRegistryV2()
    gcs = FakeGcsClient()
    layout = _layout()
    run_spec = _run_spec()

    attempt, message = _lease_and_stage(registry, gcs, layout)
    bad_descriptor = _stage_output(
        gcs,
        layout,
        run_id="run-1",
        step_name="train",
        attempt_no=attempt.attempt_no,
        operation_id=message.operation_id,
        output_name="unexpected",
        data=b"other-bytes",
    )
    bad_message = StepCompletionMessage(
        run_id="run-1",
        step_name="train",
        attempt_no=attempt.attempt_no,
        operation_id=message.operation_id,
        outputs=(message.outputs[0], bad_descriptor),
    )

    with pytest.raises(StagingUploadError, match="unknown"):
        finalize_step_outputs(registry, gcs, layout, bad_message, run_spec, _context())

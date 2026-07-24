from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.finalizer import FinalizeContext, finalize_step_outputs
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.occurrence import compute_resolved_inputs_digest
from aigear.management.v2.records.run import RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec
from aigear.management.v2.resolve_inputs import ResolveInputsError, resolve_step_inputs
from aigear.management.v2.staging_upload import StagingOutputDescriptor, StepCompletionMessage
from aigear.management.v2.step_lease import acquire_step_lease, compute_attempt_finalize_operation_id

_NOW = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
_FP = TypedId.from_bare("aa" * 32)
_CODE_DIGEST = TypedId.from_bare("22" * 32)
_CONFIG_DIGEST = TypedId.from_bare("33" * 32)
_IMAGE_DIGEST = TypedId.from_bare("11" * 32)
_GRAPH_DIGEST = TypedId.from_bare("44" * 32)
_SCHEMA_CONTRACT_DIGEST = TypedId.from_bare("55" * 32)
_RUNTIME_CONTRACT_DIGEST = TypedId.from_bare("66" * 32)


def _layout() -> GcsLayoutV2:
    return GcsLayoutV2(bucket_name="test-bucket", project_name="proj", pipeline_version="v1")


def _two_step_run_spec() -> RunSpec:
    """A "prep -> train" RunSpec, mirroring test_resolver.py's helper of the same
    name: one Step's committed output is a declared input of another Step in the
    same Run."""
    return RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=_GRAPH_DIGEST,
        code_digest=_CODE_DIGEST,
        config_digest=_CONFIG_DIGEST,
        producer_image_digest=_IMAGE_DIGEST,
        steps=(
            StepSpec(
                step_name="prep",
                outputs=(OutputSlotSpec(output_name="features", role="features", logical_name="features"),),
            ),
            StepSpec(
                step_name="train",
                outputs=(OutputSlotSpec(output_name="model", role="model", logical_name="weights"),),
                dependencies=("prep",),
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


def _produce_committed_output(
    registry, gcs, layout, *, run_id="run-1", step_name="prep", output_name="features",
    data=b"payload-bytes", run_spec=None,
):
    if registry.get_run(run_id) is None:
        registry.create_run(RunRecord(run_id=run_id, status=RunStatus.PENDING))
        registry.update_run_status(run_id, RunStatus.RUNNING)
    if registry.get_step(run_id, step_name) is None:
        registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=StepStatus.READY))

    attempt = acquire_step_lease(
        registry,
        run_id=run_id,
        step_name=step_name,
        output_names=[output_name],
        resolved_input_bindings=(),
        environment_fingerprint=_FP,
        schema_version="2.0",
        owner_principal="worker-vm-1@aigear",
        now=_NOW,
    )
    operation_id = compute_attempt_finalize_operation_id(run_id, step_name, attempt.attempt_no).bare
    staging_object = layout.staging(
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt.attempt_no,
        operation_id=operation_id,
        output_name=output_name,
        payload_kind="components",
        payload_key="primary",
        file_name="data.bin",
    )
    snapshot = gcs.put_object(staging_object, data, if_generation_match=0)
    descriptor = StagingOutputDescriptor(
        output_name=output_name,
        staging_object=staging_object,
        generation=snapshot.generation,
        digest=TypedId.from_bare(hashlib.sha256(data).hexdigest()),
        size=len(data),
        media_type="application/octet-stream",
    )
    message = StepCompletionMessage(
        run_id=run_id, step_name=step_name, attempt_no=attempt.attempt_no,
        operation_id=operation_id, outputs=(descriptor,),
    )
    run_spec = run_spec or _two_step_run_spec()
    outcome = finalize_step_outputs(registry, gcs, layout, message, run_spec, _finalize_context())
    return outcome.outputs[0]


def _blocked_downstream(registry, *, run_id="run-1", step_name="train"):
    registry.create_step(StepRecord(run_id=run_id, step_name=step_name, status=StepStatus.BLOCKED))


def test_resolve_step_inputs_seals_bindings_and_moves_to_ready():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    run_spec = _two_step_run_spec()
    prep_output = _produce_committed_output(
        registry, gcs, layout, step_name="prep", output_name="features", run_spec=run_spec
    )
    _blocked_downstream(registry)

    step = resolve_step_inputs(registry, run_spec, run_id="run-1", step_name="train", now=_NOW)

    assert step.status == StepStatus.READY
    assert len(step.resolved_inputs) == 1
    binding = step.resolved_inputs[0]
    assert binding.binding_name == "prep.features"
    assert binding.asset_version_id == prep_output.asset_version.asset_version_id
    assert binding.occurrence_id == prep_output.occurrence.occurrence_id
    assert step.resolved_inputs_digest == compute_resolved_inputs_digest(step.resolved_inputs)
    assert step.resolved_at == _NOW.isoformat()
    assert step.source_step_revision == 1


def test_resolve_step_inputs_is_idempotent_on_replay():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    run_spec = _two_step_run_spec()
    _produce_committed_output(registry, gcs, layout, step_name="prep", output_name="features", run_spec=run_spec)
    _blocked_downstream(registry)

    first = resolve_step_inputs(registry, run_spec, run_id="run-1", step_name="train", now=_NOW)
    second = resolve_step_inputs(registry, run_spec, run_id="run-1", step_name="train", now=_NOW)

    assert second == first


def test_resolve_step_inputs_rejects_unresolved_dependency():
    registry, _gcs, _layout_ = FakeRegistryV2(), FakeGcsClient(), _layout()
    run_spec = _two_step_run_spec()
    registry.create_run(RunRecord(run_id="run-1", status=RunStatus.RUNNING))
    _blocked_downstream(registry)

    with pytest.raises(ResolveInputsError, match="no committed Occurrence"):
        resolve_step_inputs(registry, run_spec, run_id="run-1", step_name="train", now=_NOW)


def test_resolve_step_inputs_rejects_step_not_blocked():
    registry, gcs, layout = FakeRegistryV2(), FakeGcsClient(), _layout()
    run_spec = _two_step_run_spec()
    _produce_committed_output(registry, gcs, layout, step_name="prep", output_name="features", run_spec=run_spec)
    registry.create_step(StepRecord(run_id="run-1", step_name="train", status=StepStatus.READY))

    with pytest.raises(ResolveInputsError, match="not eligible"):
        resolve_step_inputs(registry, run_spec, run_id="run-1", step_name="train", now=_NOW)


def test_resolve_step_inputs_rejects_unknown_step():
    registry = FakeRegistryV2()
    run_spec = _two_step_run_spec()

    with pytest.raises(ResolveInputsError, match="no Step registered"):
        resolve_step_inputs(registry, run_spec, run_id="run-1", step_name="train", now=_NOW)

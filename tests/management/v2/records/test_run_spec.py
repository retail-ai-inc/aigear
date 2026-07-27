from __future__ import annotations

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import InvalidSegmentError
from aigear.management.v2.records.asset_version import compute_component_key
from aigear.management.v2.records.run_spec import (
    InvalidRunSpecError,
    OutputSlotSpec,
    RunSpec,
    SeedInputBinding,
    StepSpec,
    compute_run_spec_digest,
)

_DIGEST = TypedId.from_bare("aa" * 32)


def _output(output_name="model", role="model", logical_name="model.onnx") -> OutputSlotSpec:
    return OutputSlotSpec(output_name=output_name, role=role, logical_name=logical_name)


def _step(step_name="train", outputs=None, dependencies=()) -> StepSpec:
    return StepSpec(
        step_name=step_name,
        dependencies=dependencies,
        outputs=outputs if outputs is not None else (_output(),),
    )


def _run_spec(steps=None, seed_inputs=(), **overrides) -> RunSpec:
    defaults = dict(
        trigger_principal="scheduler@aigear",
        trigger_source="cloud_scheduler",
        graph_digest=_DIGEST,
        code_digest=_DIGEST,
        config_digest=_DIGEST,
        producer_image_digest=_DIGEST,
        steps=steps if steps is not None else (_step(),),
        seed_inputs=seed_inputs,
    )
    defaults.update(overrides)
    return RunSpec(**defaults)


# ── OutputSlotSpec ───────────────────────────────────────────────────────────


def test_output_slot_component_key_matches_shared_helper():
    output = _output(role="model", logical_name="model.onnx")
    assert output.component_key == compute_component_key("model", "model.onnx")


def test_output_slot_rejects_invalid_segment():
    with pytest.raises(InvalidSegmentError):
        _output(output_name="bad name with spaces")


# ── StepSpec ─────────────────────────────────────────────────────────────────


def test_step_requires_at_least_one_output():
    with pytest.raises(InvalidRunSpecError):
        StepSpec(step_name="train", outputs=())


def test_step_rejects_duplicate_output_name():
    with pytest.raises(InvalidRunSpecError):
        _step(outputs=(_output(output_name="model"), _output(output_name="model", role="other")))


def test_step_rejects_output_name_collision_after_normalization():
    with pytest.raises(InvalidSegmentError):
        _step(
            outputs=(
                _output(output_name="Model"),
                _output(output_name="model", role="other"),
            )
        )


def test_step_allows_same_logical_name_across_different_roles():
    step = _step(
        outputs=(
            _output(role="model", logical_name="artifact.bin"),
            _output(output_name="scaler", role="scaler", logical_name="artifact.bin"),
        )
    )
    assert len(step.outputs) == 2


def test_step_rejects_duplicate_logical_name_within_same_role():
    with pytest.raises(InvalidRunSpecError):
        _step(
            outputs=(
                _output(output_name="a", role="model", logical_name="artifact.bin"),
                _output(output_name="b", role="model", logical_name="artifact.bin"),
            )
        )


def test_step_accepts_valid_dependencies():
    step = _step(dependencies=("extract", "transform"))
    assert step.dependencies == ("extract", "transform")


# ── RunSpec ──────────────────────────────────────────────────────────────────


def test_run_spec_requires_at_least_one_step():
    with pytest.raises(InvalidRunSpecError):
        _run_spec(steps=())


def test_run_spec_rejects_duplicate_step_name():
    with pytest.raises(InvalidRunSpecError):
        _run_spec(steps=(_step(step_name="train"), _step(step_name="train")))


def test_run_spec_rejects_step_name_collision_after_normalization():
    with pytest.raises(InvalidSegmentError):
        _run_spec(steps=(_step(step_name="Train"), _step(step_name="train")))


def test_run_spec_accepts_multiple_distinct_steps():
    spec = _run_spec(steps=(_step(step_name="extract"), _step(step_name="train")))
    assert len(spec.steps) == 2


def test_run_spec_rejects_unknown_dependency():
    with pytest.raises(InvalidRunSpecError, match="unknown step"):
        _run_spec(steps=(_step(step_name="train", dependencies=("missing",)),))


def test_run_spec_rejects_self_dependency():
    with pytest.raises(InvalidRunSpecError, match="depend on itself"):
        _run_spec(steps=(_step(step_name="train", dependencies=("train",)),))


def test_run_spec_rejects_dependency_cycle():
    with pytest.raises(InvalidRunSpecError, match="cycle"):
        _run_spec(
            steps=(
                _step(step_name="a", dependencies=("b",)),
                _step(step_name="b", dependencies=("a",)),
            )
        )


def test_run_spec_rejects_duplicate_seed_input_binding_name():
    binding = SeedInputBinding(binding_name="features", asset_version_id=_DIGEST)
    with pytest.raises(InvalidRunSpecError):
        _run_spec(seed_inputs=(binding, binding))


def test_run_spec_accepts_seed_inputs():
    binding = SeedInputBinding(binding_name="features", asset_version_id=_DIGEST)
    spec = _run_spec(seed_inputs=(binding,))
    assert spec.seed_inputs == (binding,)


def test_run_spec_requires_non_empty_trigger_principal():
    with pytest.raises(InvalidRunSpecError):
        _run_spec(trigger_principal="")


def test_run_spec_requires_typed_id_digests():
    with pytest.raises(InvalidRunSpecError):
        _run_spec(graph_digest="not-a-typed-id")


def test_run_spec_defaults_retry_and_cancel_policy_to_empty_dict():
    spec = _run_spec()
    assert spec.retry_policy == {}
    assert spec.cancel_policy == {}


def test_run_spec_rejects_non_dict_retry_policy():
    with pytest.raises(InvalidRunSpecError):
        _run_spec(retry_policy="not-a-dict")


def test_run_spec_rejects_unknown_retry_policy_key():
    with pytest.raises(InvalidRunSpecError, match="unsupported keys"):
        _run_spec(retry_policy={"forever": True})


def test_run_spec_rejects_unbounded_or_inverted_backoff():
    with pytest.raises(InvalidRunSpecError, match="must be >= initial"):
        _run_spec(
            retry_policy={
                "initial_backoff_seconds": 10,
                "max_backoff_seconds": 5,
            }
        )


def test_run_spec_scheduled_for_defaults_to_none():
    assert _run_spec().scheduled_for is None


def test_run_spec_accepts_explicit_scheduled_for():
    spec = _run_spec(scheduled_for="2026-07-24T00:00:00Z")
    assert spec.scheduled_for == "2026-07-24T00:00:00Z"


# ── compute_run_spec_digest ────────────────────────────────────────────────────


def test_compute_run_spec_digest_is_deterministic():
    spec_a = _run_spec()
    spec_b = _run_spec()
    assert compute_run_spec_digest(spec_a) == compute_run_spec_digest(spec_b)


def test_compute_run_spec_digest_changes_with_steps():
    base = _run_spec()
    changed = _run_spec(steps=(_step(step_name="different"),))
    assert compute_run_spec_digest(base) != compute_run_spec_digest(changed)


def test_compute_run_spec_digest_changes_with_retry_policy():
    base = _run_spec()
    changed = _run_spec(retry_policy={"max_attempts": 3})
    assert compute_run_spec_digest(base) != compute_run_spec_digest(changed)

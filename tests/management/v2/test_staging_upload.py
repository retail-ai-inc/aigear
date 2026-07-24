from __future__ import annotations

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec, StepSpec
from aigear.management.v2.staging_upload import (
    StagingOutputDescriptor,
    StagingUploadError,
    StepCompletionMessage,
    validate_local_output_file,
    validate_step_completion_message,
)

_DIGEST = TypedId.from_bare("aa" * 32)


def _run_spec_with_outputs(*output_names: str) -> RunSpec:
    return RunSpec(
        trigger_principal="scheduler@aigear",
        trigger_source="schedule",
        graph_digest=_DIGEST,
        code_digest=_DIGEST,
        config_digest=_DIGEST,
        producer_image_digest=_DIGEST,
        steps=(
            StepSpec(
                step_name="train",
                outputs=tuple(
                    OutputSlotSpec(output_name=name, role="model", logical_name=name)
                    for name in output_names
                ),
            ),
        ),
    )


def _descriptor(output_name: str) -> StagingOutputDescriptor:
    return StagingOutputDescriptor(
        output_name=output_name,
        staging_object="proj/v1/registry/v2/_staging/run-1/train/1/op-1/model/components/primary/model.bin",
        generation="12345",
        digest=_DIGEST,
        size=1024,
        media_type="application/octet-stream",
    )


# ── validate_local_output_file ────────────────────────────────────────────


def test_accepts_regular_file_within_base_dir(tmp_path):
    base_dir = tmp_path
    output_file = base_dir / "model.bin"
    output_file.write_bytes(b"hello")

    resolved = validate_local_output_file(output_file, base_dir=base_dir)
    assert resolved == output_file.resolve()


def test_rejects_symlink(tmp_path):
    base_dir = tmp_path
    real_file = base_dir / "real.bin"
    real_file.write_bytes(b"hello")
    link = base_dir / "link.bin"
    try:
        link.symlink_to(real_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    with pytest.raises(StagingUploadError):
        validate_local_output_file(link, base_dir=base_dir)


def test_rejects_directory(tmp_path):
    base_dir = tmp_path
    sub_dir = base_dir / "subdir"
    sub_dir.mkdir()

    with pytest.raises(StagingUploadError):
        validate_local_output_file(sub_dir, base_dir=base_dir)


def test_rejects_path_escaping_base_dir(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside_file = tmp_path / "outside.bin"
    outside_file.write_bytes(b"hello")

    with pytest.raises(StagingUploadError):
        validate_local_output_file(outside_file, base_dir=base_dir)


@pytest.mark.parametrize("reserved_name", ["CON", "con.txt", "NUL", "COM1", "lpt1.json"])
def test_rejects_windows_reserved_names(tmp_path, reserved_name):
    base_dir = tmp_path
    output_file = base_dir / reserved_name
    output_file.write_bytes(b"hello")

    with pytest.raises(StagingUploadError):
        validate_local_output_file(output_file, base_dir=base_dir)


def test_accepts_name_that_only_contains_reserved_name_as_substring(tmp_path):
    base_dir = tmp_path
    output_file = base_dir / "console.log"
    output_file.write_bytes(b"hello")

    validate_local_output_file(output_file, base_dir=base_dir)


# ── StagingOutputDescriptor / StepCompletionMessage ───────────────────────


def test_descriptor_accepts_well_formed_fields():
    descriptor = _descriptor("model")
    assert descriptor.output_name == "model"
    assert descriptor.size == 1024


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("staging_object", ""),
        ("generation", ""),
        ("size", -1),
        ("media_type", ""),
    ],
)
def test_descriptor_rejects_invalid_fields(field_name, value):
    kwargs = dict(
        output_name="model",
        staging_object="some/object",
        generation="1",
        digest=_DIGEST,
        size=1024,
        media_type="application/octet-stream",
    )
    kwargs[field_name] = value
    with pytest.raises(StagingUploadError):
        StagingOutputDescriptor(**kwargs)


def test_descriptor_rejects_non_typed_id_digest():
    with pytest.raises(StagingUploadError):
        StagingOutputDescriptor(
            output_name="model",
            staging_object="some/object",
            generation="1",
            digest="not-a-typed-id",
            size=1024,
            media_type="application/octet-stream",
        )


def test_completion_message_accepts_well_formed_outputs():
    message = StepCompletionMessage(
        run_id="run-1",
        step_name="train",
        attempt_no=1,
        operation_id="op-1",
        outputs=(_descriptor("model"), _descriptor("metrics")),
    )
    assert len(message.outputs) == 2


def test_completion_message_rejects_duplicate_output_name():
    with pytest.raises(StagingUploadError):
        StepCompletionMessage(
            run_id="run-1",
            step_name="train",
            attempt_no=1,
            operation_id="op-1",
            outputs=(_descriptor("model"), _descriptor("model")),
        )


def test_completion_message_rejects_empty_outputs():
    with pytest.raises(StagingUploadError):
        StepCompletionMessage(
            run_id="run-1",
            step_name="train",
            attempt_no=1,
            operation_id="op-1",
            outputs=(),
        )


# ── validate_step_completion_message ──────────────────────────────────────


def test_validate_accepts_message_matching_declared_outputs():
    run_spec = _run_spec_with_outputs("model", "metrics")
    message = StepCompletionMessage(
        run_id="run-1",
        step_name="train",
        attempt_no=1,
        operation_id="op-1",
        outputs=(_descriptor("model"), _descriptor("metrics")),
    )
    validate_step_completion_message(message, run_spec)


def test_validate_rejects_missing_output():
    run_spec = _run_spec_with_outputs("model", "metrics")
    message = StepCompletionMessage(
        run_id="run-1",
        step_name="train",
        attempt_no=1,
        operation_id="op-1",
        outputs=(_descriptor("model"),),
    )
    with pytest.raises(StagingUploadError, match="missing"):
        validate_step_completion_message(message, run_spec)


def test_validate_rejects_unknown_output():
    run_spec = _run_spec_with_outputs("model")
    message = StepCompletionMessage(
        run_id="run-1",
        step_name="train",
        attempt_no=1,
        operation_id="op-1",
        outputs=(_descriptor("model"), _descriptor("unexpected")),
    )
    with pytest.raises(StagingUploadError, match="unknown"):
        validate_step_completion_message(message, run_spec)


def test_validate_rejects_unknown_step_name():
    run_spec = _run_spec_with_outputs("model")
    message = StepCompletionMessage(
        run_id="run-1",
        step_name="not-a-step",
        attempt_no=1,
        operation_id="op-1",
        outputs=(_descriptor("model"),),
    )
    with pytest.raises(StagingUploadError, match="no step named"):
        validate_step_completion_message(message, run_spec)

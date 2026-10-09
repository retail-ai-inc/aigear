import pytest

from aigear.deploy.gcp.run_logs import build_step_timeline, extract_log_fields, format_step_timeline


def _entry(event, *, second=0, step="training", **fields):
    return {
        "timestamp": f"2026-10-09T00:00:{second:02d}Z",
        "jsonPayload": {"event": event, "step_name": step, **fields},
    }


def test_cancelled_step_has_reason_and_no_execution_times():
    rows = build_step_timeline([_entry("pipeline_step_cancelled", step="model_service", failed_step="training")])
    assert len(rows) == 1
    assert rows[0].status == "CANCELLED"
    assert rows[0].detail == "Stopped after training failed"
    assert rows[0].started_at is None
    assert rows[0].finished_at is None
    assert "CANCELLED" in "\n".join(format_step_timeline(rows, run_id="run"))


@pytest.mark.parametrize("event,status", [("pipeline_step_started", "RUNNING"), ("pipeline_step_finished", "OK"), ("pipeline_step_failed", "FAILED")])
def test_cancellation_does_not_overwrite_executed_step(event, status):
    rows = build_step_timeline([
        _entry(event),
        _entry("pipeline_step_cancelled", second=1, failed_step="fetch_data"),
    ])
    assert rows[0].status == status


def test_pipeline_exit_failure_preserves_original_python_error():
    rows = build_step_timeline([
        _entry("pipeline_step_failed", log_source="ml_pipeline", error_message="invalid features", duration_ms=250),
        _entry("vm_step_failed", second=1, log_source="cloud_function", exit_code="pipeline_failed", error_message="Traceback\ninvalid features", failure_stage="pipeline_step", command_exit_code=1),
    ])
    assert rows[0].status == "FAILED"
    assert rows[0].detail == "invalid features"
    assert rows[0].duration_ms == 250
    assert "pipeline_step (exit code 1): Traceback\ninvalid features" in rows[0].errors


@pytest.mark.parametrize("event", ["task_validation_failed", "pipeline_command_build_failed"])
def test_pre_vm_validation_failure_is_visible(event):
    rows = build_step_timeline([_entry(event, error="missing docker_image")])
    assert rows[0].status == "FAILED"
    assert rows[0].detail == "missing docker_image"
    assert rows[0].errors == ["missing docker_image"]


def test_failure_details_keep_multiline_output_and_truncation_notice():
    rows = build_step_timeline([_entry(
        "vm_step_failed", exit_code="deploy_failed", failure_stage="kubectl_apply",
        command_exit_code=2, error_message="invalid manifest\nfield replicas is invalid",
        error_output_truncated=True,
    )])
    output = "\n".join(format_step_timeline(rows, run_id="run", show_hint=False))
    assert "=== Failure details ===" in output
    assert "kubectl_apply (exit code 2): invalid manifest" in output
    assert "  field replicas is invalid" in output
    assert "[Output truncated to the last 8192 bytes]" in output
    assert "Hint:" not in output


def test_repeated_error_messages_are_deduplicated():
    rows = build_step_timeline([_entry("pipeline_step_failed", error_message="bad input"), _entry("pipeline_step_failed", second=1, error_message="bad input")])
    assert rows[0].errors == ["bad input"]


def test_failure_without_captured_output_explains_missing_details():
    rows = build_step_timeline([_entry("vm_creation_failed")])
    output = "\n".join(format_step_timeline(rows, run_id="run"))
    assert "No error message was captured" in output
    assert "updated Cloud Function startup script" in output


def test_plain_error_uses_step_name_from_labels():
    rows = build_step_timeline([{
        "timestamp": "2026-10-09T00:00:00Z", "labels": {"step_name": "training"},
        "severity": "ERROR", "textPayload": "model loading failed",
    }])
    assert rows[0].step_name == "training"
    assert rows[0].status == "FAILED"
    assert rows[0].errors == ["model loading failed"]


def test_prefixed_json_error_is_extracted_without_losing_multiline_message():
    assert extract_log_fields({"textPayload": 'stderr: {"event":"vm_step_failed","error_message":"line1\\nline2"} trailing'}) == {
        "event": "vm_step_failed", "error_message": "line1\nline2",
    }


def test_pipeline_start_replaces_vm_start_for_duration():
    rows = build_step_timeline([
        _entry("vm_startup"), _entry("pipeline_step_started", second=5),
        _entry("pipeline_step_finished", second=8),
    ])
    assert rows[0].started_at == "2026-10-09T00:00:05Z"
    assert "3s" in "\n".join(format_step_timeline(rows, run_id="run"))

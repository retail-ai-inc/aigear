from pathlib import Path

_INDEX_JS = (
    Path(__file__).resolve().parents[3] / "src" / "aigear" / "infrastructure" / "gcp" / "function" / "index.js"
)


def test_fatal_error_pubsub_includes_run_context_fields():
    text = _INDEX_JS.read_text(encoding="utf-8")
    assert "buildFatalErrorMessagePrefix" in text
    assert "fatalErrorPrefix" in text
    assert "docker_image: dockerImage" in text


def test_vm_step_failed_uses_structured_task_fields():
    text = _INDEX_JS.read_text(encoding="utf-8")
    assert "taskFromErrorPayload(cronjobInfo)" in text
    assert "event: 'vm_step_failed'" in text
    assert "docker_image: cronjobInfo.docker_image" in text
    assert "delete sanitizedExtra.log_source;" in text


def test_startup_marker_omits_log_source():
    text = _INDEX_JS.read_text(encoding="utf-8")
    marker_start = text.index("const startupMarker = JSON.stringify({")
    marker_block = text[marker_start : marker_start + 400]
    assert "event: 'vm_startup'" in marker_block
    assert "log_source" not in marker_block


def test_final_pubsub_message_preserves_run_context():
    text = _INDEX_JS.read_text(encoding="utf-8")
    completion_start = text.index("const completionMessage = JSON.stringify({")
    completion_block = text[completion_start : completion_start + 500]
    assert "done: true" in completion_block
    assert "run_id: runId" in completion_block
    assert "step_name: stepName" in completion_block
    assert "const publishMessage = nextMessage === MSG.EMPTY_QUEUE ? completionMessage : nextMessage;" in text
    assert "--message '${esc(publishMessage)}'" in text


def test_completion_payload_writes_queryable_pipeline_completed_log():
    text = _INDEX_JS.read_text(encoding="utf-8")
    completion_start = text.index("if (cronjobInfo?.done) {")
    completion_block = text[completion_start : completion_start + 400]
    assert "event: 'pipeline_completed'" in completion_block
    assert "task: taskFromCompletionPayload(cronjobInfo)" in completion_block


def test_write_cloud_function_log_payload_contract():
    text = _INDEX_JS.read_text(encoding="utf-8")
    fn_start = text.index("async function writeCloudFunctionLog(")
    fn_block = text[fn_start : fn_start + 600]
    assert "log_source: 'cloud_function'" in fn_block
    assert "delete sanitizedExtra.log_source;" in fn_block
    assert "delete sanitizedExtra.event;" in fn_block
    assert "delete sanitizedExtra.message;" in fn_block


def test_error_path_no_queryable_console_error():
    text = _INDEX_JS.read_text(encoding="utf-8")
    error_start = text.index("if (cronjobInfo?.error) {")
    error_block = text[error_start : error_start + 500]
    assert "event: 'vm_step_failed'" in error_block
    assert "Pipeline step failed" not in error_block
    assert "console.error" not in error_block


def test_vm_step_failed_includes_failure_layer():
    text = _INDEX_JS.read_text(encoding="utf-8")
    assert "failure_layer: 'infrastructure'" in text
    assert "event: 'pipeline_step_failed'" not in text

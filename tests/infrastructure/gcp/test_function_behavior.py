import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


_NODE = shutil.which("node")
_INDEX = Path(__file__).resolve().parents[3] / "src/aigear/infrastructure/gcp/function/index.js"
_HARNESS = Path(__file__).with_name("function_behavior.mjs")
pytestmark = pytest.mark.skipif(_NODE is None, reason="Cloud Function behavior tests require Node.js")


def _run(operation, **request):
    completed = subprocess.run(
        [_NODE, "--experimental-vm-modules", str(_HARNESS), str(_INDEX)],
        input=json.dumps({"operation": operation, **request}), capture_output=True,
        text=True, encoding="utf-8", timeout=15, check=True,
    )
    return json.loads(completed.stdout)


def _task(step="training", **fields):
    return {"docker_image": "registry/image:latest", "vm_name": "pipeline-vm", "step_name": step,
            "pipeline_version": "v1", "project_name": "demo", "run_id": "run-123",
            "run_started_at_utc": "2026-10-09T00:00:00Z", **fields}


def _cancelled(output):
    return [entry for entry in output["logs"] if entry["event"] == "pipeline_step_cancelled"]


def test_vm_error_cancels_remaining_tasks_without_creating_or_publishing_more_work():
    output = _run("handler", payload={**_task(), "error": True, "exit_code": "pipeline_failed",
                  "failure_stage": "pipeline_step", "command_exit_code": 1,
                  "error_message": "Traceback\nbad features", "error_output_truncated": True,
                  "cancelled_steps": [_task("model_service"), _task("cleanup")]})
    assert output["inserts"] == []
    assert output["publications"] == []
    failure = next(entry for entry in output["logs"] if entry["event"] == "vm_step_failed")
    assert failure["error_message"] == "Traceback\nbad features"
    assert failure["failure_stage"] == "pipeline_step"
    assert failure["command_exit_code"] == 1
    assert failure["error_output_truncated"] is True
    assert [entry["step_name"] for entry in _cancelled(output)] == ["model_service", "cleanup"]
    assert all(entry["failed_step"] == "training" and entry["run_id"] == "run-123" for entry in _cancelled(output))


@pytest.mark.parametrize("invalid", [{"docker_image": ""}, {"vm_name": ""}, {"pipeline_version": "bad;version"}, {"venv": "../escape"}])
def test_invalid_task_stops_queue_and_keeps_cancellation_context(invalid):
    output = _run("handler", payload=[_task(**invalid), {"step_name": "model_service"}])
    assert output["inserts"] == []
    assert len(output["publications"]) == 1
    assert output["publications"][0]["error"] is True
    assert output["publications"][0]["exit_code"] == "task_invalid"
    assert output["publications"][0]["run_id"] == "run-123"
    assert len(_cancelled(output)) == 1
    assert _cancelled(output)[0]["pipeline_version"] == _task(**invalid)["pipeline_version"]
    assert _cancelled(output)[0]["run_id"] == "run-123"


def test_vm_creation_failure_cancels_queue_and_publishes_terminal_error():
    output = _run("handler", payload=[_task(), _task("model_service")], vmError="permission denied")
    assert len(output["inserts"]) == 1
    assert len(_cancelled(output)) == 1
    assert any(entry["event"] == "vm_creation_failed" for entry in output["logs"])
    assert output["publications"][0]["exit_code"] == "vm_create_failed"
    assert output["publications"][0]["run_id"] == "run-123"


def test_success_creates_only_current_vm_and_embeds_next_task_context():
    output = _run("handler", payload=[_task(), _task("model_service")])
    assert len(output["inserts"]) == 1
    assert output["publications"] == []
    assert _cancelled(output) == []
    script = output["inserts"][0]["requestBody"]["metadata"]["items"][0]["value"]
    assert '"step_name":"model_service"' in script
    assert "fatal_error pipeline_failed pipeline_step" in script
    assert "set -euo pipefail" in script


@pytest.mark.parametrize("steps", [None, {}, "invalid", []])
def test_absent_or_invalid_cancellation_list_is_safe(steps):
    assert _run("writeCancelledStepLogs", args=[steps, _task()])["logs"] == []


def test_fatal_error_prefix_preserves_cancelled_steps_and_quotes():
    output = _run("buildFatalErrorMessagePrefix", args=[{
        "runId": "run-123", "stepName": "training", "projectName": "demo's project",
        "cancelledSteps": [_task("model_service")],
    }])
    payload = json.loads(output["result"] + "}")
    assert payload["error"] is True
    assert payload["project_name"] == "demo's project"
    assert payload["cancelled_steps"][0]["run_id"] == "run-123"


@pytest.mark.parametrize("payload", [{"done": True, **_task()}, []])
def test_completion_never_creates_new_work(payload):
    output = _run("handler", payload=payload)
    assert output["inserts"] == []
    assert output["publications"] == []
    assert any(entry["event"] == "pipeline_completed" for entry in output["logs"])


@pytest.mark.parametrize("content,truncated", [(b"command failed", False), (b"x" * 9000, True), (b"", False), (b"x" * 8192, False)])
def test_generated_error_encoder_preserves_exit_code_and_bounds_output(tmp_path, content, truncated):
    script = _run("buildStartupScript", args=[{
        "dockerImage": "registry/image:v1", "gpuFlag": "", "pipelineCommand": "aigear-task workflow --version v1 --step training",
        "yamlPathInImage": "", "nextMessage": "[]", "topicName": "topic", "runId": "run-123",
        "stepName": "training", "pipelineVersion": "v1",
    }])["result"]
    encoder = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    error_log = tmp_path / "command-output"
    error_log.write_bytes(content)
    completed = subprocess.run(
        [sys.executable, "-B", "-c", encoder, '{"error":true,"run_id":"run-123"', "deploy_failed", "kubectl_apply", "2", str(error_log)],
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["command_exit_code"] == 2
    assert payload["failure_stage"] == "kubectl_apply"
    assert payload["error_output_truncated"] is truncated
    assert payload["error_message"] == (content[-8192:].decode() or "Command failed without output")

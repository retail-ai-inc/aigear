from unittest.mock import MagicMock, patch

from aigear.common.lifecycle_log import emit_lifecycle_log, emit_step_result
from aigear.common.run_log_context import RunLogContext


def _ctx(**kwargs):
    defaults = dict(
        run_id="run-1",
        run_started_at_utc="2026-06-01T00:00:00Z",
        pipeline_version="v1",
        step_name="fetch_data",
        project_id="my-project",
    )
    defaults.update(kwargs)
    return RunLogContext(**defaults)


@patch("google.cloud.logging.Client")
def test_emit_lifecycle_log_writes_struct(mock_client_cls):
    mock_logger = MagicMock()
    mock_client_cls.return_value.logger.return_value = mock_logger

    emit_lifecycle_log("pipeline_step_started", _ctx())

    mock_client_cls.assert_called_once_with(project="my-project")
    mock_client_cls.return_value.logger.assert_called_once_with("aigear-task")
    mock_logger.log_struct.assert_called_once()
    payload = mock_logger.log_struct.call_args[0][0]
    assert payload["event"] == "pipeline_step_started"
    assert payload["log_source"] == "ml_pipeline"
    assert mock_logger.log_struct.call_args[1]["severity"] == "INFO"


@patch("google.cloud.logging.Client")
def test_emit_lifecycle_log_pipeline_failed_includes_error(mock_client_cls):
    mock_logger = MagicMock()
    mock_client_cls.return_value.logger.return_value = mock_logger

    emit_lifecycle_log(
        "pipeline_step_failed",
        _ctx(),
        extra={"error_message": "boom", "error_type": "RuntimeError", "log_source": "cloud_function"},
    )

    payload = mock_logger.log_struct.call_args[0][0]
    assert payload["log_source"] == "ml_pipeline"
    assert payload["error_message"] == "boom"
    assert payload["error_type"] == "RuntimeError"
    assert mock_logger.log_struct.call_args[1]["severity"] == "ERROR"


@patch("google.cloud.logging.Client")
def test_emit_lifecycle_log_swallows_permission_denied(mock_client_cls):
    mock_logger = MagicMock()
    mock_logger.log_struct.side_effect = PermissionError("logging denied")
    mock_client_cls.return_value.logger.return_value = mock_logger

    emit_lifecycle_log("pipeline_step_started", _ctx())


@patch("google.cloud.logging.Client")
def test_emit_lifecycle_log_truncates_long_error_message(mock_client_cls):
    mock_logger = MagicMock()
    mock_client_cls.return_value.logger.return_value = mock_logger

    emit_lifecycle_log(
        "pipeline_step_failed",
        _ctx(),
        extra={"error_message": "x" * 3000, "error_type": "ValueError"},
    )

    payload = mock_logger.log_struct.call_args[0][0]
    assert len(payload["error_message"]) == 2048
    assert payload["error_type"] == "ValueError"


def test_emit_lifecycle_log_noop_without_project_id():
    emit_lifecycle_log("pipeline_step_started", _ctx(project_id=None))


def test_emit_step_result_noop_without_project_id():
    emit_step_result(_ctx(project_id=None), {"accuracy": 0.92})


@patch("google.cloud.logging.Client")
def test_emit_step_result_writes_struct(mock_client_cls):
    mock_logger = MagicMock()
    mock_client_cls.return_value.logger.return_value = mock_logger

    emit_step_result(_ctx(step_name="training"), {"accuracy": 0.92, "rows": 1000})

    mock_logger.log_struct.assert_called_once()
    payload = mock_logger.log_struct.call_args[0][0]
    assert payload["event"] == "pipeline_step_result"
    assert payload["step_name"] == "training"
    assert payload["result"] == {"accuracy": 0.92, "rows": 1000}
    assert mock_logger.log_struct.call_args[1]["severity"] == "INFO"

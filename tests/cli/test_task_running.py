from unittest.mock import MagicMock, patch

import pytest

from aigear.cli.task_running import run_workflow


@patch("aigear.cli.task_running.AigearConfig")
@patch("aigear.cli.task_running.RunLogContext")
@patch("aigear.cli.task_running.Logging")
@patch("aigear.cli.task_running.PipelinesConfig")
def test_run_workflow_installs_context_before_validation(
    mock_pipelines, mock_logging, mock_ctx_cls, mock_aigear_config
):
    mock_ctx_cls.install_from_env.return_value = MagicMock()
    mock_pipelines.get_version_config.return_value = None
    task_logger = MagicMock()
    mock_logging.return_value.for_task.return_value = task_logger

    assert run_workflow("missing_version", "fetch_data") is False

    mock_ctx_cls.install_from_env.assert_called_once()
    task_logger.error.assert_called_once()
    mock_logging.return_value.console_logging.assert_not_called()
    mock_ctx_cls.clear.assert_called_once()


@patch("aigear.cli.task_running.AigearConfig")
@patch("aigear.cli.task_running.RunLogContext")
@patch("aigear.cli.task_running.Logging")
@patch("aigear.cli.task_running.PipelinesConfig")
def test_run_workflow_clears_context_on_early_return(
    mock_pipelines, mock_logging, mock_ctx_cls, mock_aigear_config
):
    mock_ctx_cls.install_from_env.return_value = None
    mock_pipelines.get_version_config.return_value = {"fetch_data": {}}

    assert run_workflow("v1", "fetch_data") is False

    mock_ctx_cls.clear.assert_called_once()


@patch("aigear.cli.task_running.time")
@patch("aigear.cli.task_running.emit_lifecycle_log")
@patch("aigear.cli.task_running.LoadModule")
@patch("aigear.cli.task_running.AigearConfig")
@patch("aigear.cli.task_running.RunLogContext")
@patch("aigear.cli.task_running.Logging")
@patch("aigear.cli.task_running.PipelinesConfig")
def test_run_workflow_emits_pipeline_step_started(
    mock_pipelines,
    mock_logging,
    mock_ctx_cls,
    mock_aigear_config,
    mock_load_module,
    mock_emit_lifecycle,
    mock_time,
):
    ctx = MagicMock()
    mock_ctx_cls.install_from_env.return_value = ctx
    mock_pipelines.get_version_config.return_value = {
        "fetch_data": {"pipeline_step": "pkg.mod:run"},
    }
    mock_logging.return_value.for_task.return_value = MagicMock()
    mock_load_module.return_value.load_module.return_value = MagicMock()
    mock_time.monotonic.side_effect = [100.0, 102.5]

    assert run_workflow("v1", "fetch_data") is True

    mock_emit_lifecycle.assert_any_call("pipeline_step_started", ctx)
    mock_ctx_cls.clear.assert_called_once()


@patch("aigear.cli.task_running.time")
@patch("aigear.cli.task_running.emit_lifecycle_log")
@patch("aigear.cli.task_running.LoadModule")
@patch("aigear.cli.task_running.AigearConfig")
@patch("aigear.cli.task_running.RunLogContext")
@patch("aigear.cli.task_running.Logging")
@patch("aigear.cli.task_running.PipelinesConfig")
def test_run_workflow_emits_pipeline_step_finished_with_duration_ms(
    mock_pipelines,
    mock_logging,
    mock_ctx_cls,
    mock_aigear_config,
    mock_load_module,
    mock_emit_lifecycle,
    mock_time,
):
    ctx = MagicMock()
    mock_ctx_cls.install_from_env.return_value = ctx
    mock_pipelines.get_version_config.return_value = {
        "fetch_data": {"pipeline_step": "pkg.mod:run"},
    }
    mock_logging.return_value.for_task.return_value = MagicMock()
    mock_load_module.return_value.load_module.return_value = MagicMock()
    mock_time.monotonic.side_effect = [100.0, 102.5]

    assert run_workflow("v1", "fetch_data") is True

    mock_emit_lifecycle.assert_any_call(
        "pipeline_step_finished",
        ctx,
        extra={"duration_ms": 2500},
    )
    mock_ctx_cls.clear.assert_called_once()


@patch("aigear.cli.task_running.time")
@patch("aigear.cli.task_running.emit_lifecycle_log")
@patch("aigear.cli.task_running.LoadModule")
@patch("aigear.cli.task_running.AigearConfig")
@patch("aigear.cli.task_running.RunLogContext")
@patch("aigear.cli.task_running.Logging")
@patch("aigear.cli.task_running.PipelinesConfig")
def test_run_workflow_emits_pipeline_step_failed_with_error_fields(
    mock_pipelines,
    mock_logging,
    mock_ctx_cls,
    mock_aigear_config,
    mock_load_module,
    mock_emit_lifecycle,
    mock_time,
):
    ctx = MagicMock()
    mock_ctx_cls.install_from_env.return_value = ctx
    mock_pipelines.get_version_config.return_value = {
        "fetch_data": {"pipeline_step": "pkg.mod:run"},
    }
    task_logger = MagicMock()
    mock_logging.return_value.for_task.return_value = task_logger
    mock_load_module.return_value.load_module.return_value.side_effect = RuntimeError(
        "step blew up"
    )
    mock_time.monotonic.side_effect = [50.0, 50.5]

    assert run_workflow("v1", "fetch_data") is False

    mock_emit_lifecycle.assert_any_call(
        "pipeline_step_failed",
        ctx,
        extra={
            "error_message": "step blew up",
            "error_type": "RuntimeError",
            "failure_layer": "pipeline",
            "duration_ms": 500,
        },
    )
    task_logger.error.assert_called_once()
    mock_ctx_cls.clear.assert_called_once()


@pytest.mark.parametrize("succeeded", [True, False])
def test_task_run_propagates_workflow_outcome(succeeded):
    from aigear.cli import task_running

    with patch("sys.argv", ["aigear-task", "workflow", "--version", "v1", "--step", "training"]), patch.object(
        task_running, "run_workflow", return_value=succeeded
    ) as workflow:
        if succeeded:
            task_running.task_run()
        else:
            with pytest.raises(SystemExit) as exc:
                task_running.task_run()
            assert exc.value.code == 1
    workflow.assert_called_once_with("v1", "training")


@pytest.mark.parametrize("load_error", [ImportError("missing module"), AttributeError("missing function")])
def test_workflow_import_failure_returns_false_and_clears_context(load_error):
    from aigear.cli import task_running

    with patch.object(task_running, "AigearConfig"), patch.object(
        task_running, "PipelinesConfig"
    ) as pipelines, patch.object(task_running, "RunLogContext") as context, patch.object(
        task_running, "Logging"
    ), patch.object(task_running, "LoadModule") as loader:
        context.install_from_env.return_value = None
        pipelines.get_version_config.return_value = {"training": {"pipeline_step": "pkg.module.run"}}
        loader.return_value.load_module.side_effect = load_error
        assert task_running.run_workflow("v1", "training") is False
        context.clear.assert_called_once()

from unittest.mock import MagicMock, patch

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

    run_workflow("missing_version", "fetch_data")

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

    run_workflow("v1", "fetch_data")

    mock_ctx_cls.clear.assert_called_once()

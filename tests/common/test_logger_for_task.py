from unittest.mock import MagicMock, patch

from aigear.common.logger import Logging
from aigear.common.run_log_context import RunLogContext


def test_for_task_uses_console_without_run_env(monkeypatch):
    monkeypatch.delenv("AIGEAR_RUN_ID", raising=False)
    RunLogContext.clear()
    logger = Logging(log_name="test.local").for_task()
    assert getattr(logger, "_cloud_patched", False) is False


def test_for_task_patches_cloud_when_gcp_logging_enabled(monkeypatch):
    monkeypatch.setenv("AIGEAR_RUN_ID", "rid")
    monkeypatch.setenv("AIGEAR_RUN_STARTED_AT_UTC", "2026-05-21T00:00:00Z")
    monkeypatch.setenv("AIGEAR_PIPELINE_VERSION", "v1")
    RunLogContext.install_from_env(gcp_logging=True, project_id="my-proj")

    with patch("google.cloud.logging.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_cloud_logger = MagicMock()
        mock_client.logger.return_value = mock_cloud_logger
        mock_client_cls.return_value = mock_client

        logger = Logging(log_name="test.vm").for_task()
        logger.info("hello")

    mock_cloud_logger.log_struct.assert_called_once()
    payload = mock_cloud_logger.log_struct.call_args[0][0]
    assert payload["run_id"] == "rid"
    assert payload["log_source"] == "ml_pipeline"
    RunLogContext.clear()

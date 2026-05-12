import importlib
from unittest.mock import patch, MagicMock


def test_create_flag_calls_create_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--create", "--version=v1", "--step_names=preprocess,train"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.create_scheduler") as mock_create:
            gcp_scheduler.gcp_scheduler()
            mock_create.assert_called_once_with("v1", ["preprocess", "train"], "staging")


def test_update_flag_calls_update_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--update", "--version=v1", "--step_names=train"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.update_scheduler") as mock_update:
            gcp_scheduler.gcp_scheduler()
            mock_update.assert_called_once_with("v1", ["train"], "staging")


def test_delete_flag_calls_delete_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--delete", "--version=v1"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.delete_scheduler") as mock_delete:
            gcp_scheduler.gcp_scheduler()
            mock_delete.assert_called_once_with("v1")


def test_status_flag_calls_status_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--status", "--version=v1"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.status_scheduler") as mock_status:
            gcp_scheduler.gcp_scheduler()
            mock_status.assert_called_once_with("v1")


def test_run_flag_calls_run_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--run", "--version=v1"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.run_scheduler") as mock_run:
            gcp_scheduler.gcp_scheduler()
            mock_run.assert_called_once_with("v1")


def test_pause_flag_calls_pause_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--pause", "--version=v1"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.pause_scheduler") as mock_pause:
            gcp_scheduler.gcp_scheduler()
            mock_pause.assert_called_once_with("v1")


def test_resume_flag_calls_resume_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--resume", "--version=v1"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.resume_scheduler") as mock_resume:
            gcp_scheduler.gcp_scheduler()
            mock_resume.assert_called_once_with("v1")


def test_list_flag_calls_list_scheduler():
    with patch("sys.argv", ["aigear-scheduler", "--list", "--version=v1"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.list_scheduler") as mock_list:
            gcp_scheduler.gcp_scheduler()
            mock_list.assert_called_once_with("v1")


def test_missing_version_prints_error(capsys):
    with patch("sys.argv", ["aigear-scheduler", "--delete"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        gcp_scheduler.gcp_scheduler()
        captured = capsys.readouterr()
        assert "Missing required argument" in captured.out


def test_create_missing_step_names_prints_error(capsys):
    with patch("sys.argv", ["aigear-scheduler", "--create", "--version=v1"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        gcp_scheduler.gcp_scheduler()
        captured = capsys.readouterr()
        assert "--step_names" in captured.out


def test_create_with_env_production_passes_env():
    with patch("sys.argv", ["aigear-scheduler", "--create", "--version=v1", "--step_names=train", "--env=production"]):
        from aigear.cli import gcp_scheduler
        importlib.reload(gcp_scheduler)

        with patch("aigear.cli.gcp_scheduler.create_scheduler") as mock_create:
            gcp_scheduler.gcp_scheduler()
            mock_create.assert_called_once_with("v1", ["train"], "production")

import importlib
from unittest.mock import patch, MagicMock
import pytest


def test_update_flag_calls_infra_update():
    with patch("sys.argv", ["aigear-infra", "--update"]):
        from aigear.cli import gcp_cli
        importlib.reload(gcp_cli)

        with patch("aigear.cli.gcp_cli.Infra") as mock_infra_cls:
            mock_infra = MagicMock()
            mock_infra_cls.return_value = mock_infra
            mock_infra.update.return_value = True

            gcp_cli.gcp_infra()

            mock_infra.update.assert_called_once()
            mock_infra.create.assert_not_called()
            mock_infra.delete.assert_not_called()


def test_create_flag_does_not_call_update():
    with patch("sys.argv", ["aigear-infra", "--create"]):
        from aigear.cli import gcp_cli
        importlib.reload(gcp_cli)

        with patch("aigear.cli.gcp_cli.Infra") as mock_infra_cls:
            mock_infra = MagicMock()
            mock_infra_cls.return_value = mock_infra
            mock_infra.create.return_value = True

            gcp_cli.gcp_infra()

            mock_infra.create.assert_called_once()
            mock_infra.update.assert_not_called()
            mock_infra.delete.assert_not_called()


def test_delete_flag_calls_infra_delete():
    with patch("sys.argv", ["aigear-infra", "--delete"]):
        from aigear.cli import gcp_cli
        importlib.reload(gcp_cli)

        with patch("aigear.cli.gcp_cli.Infra") as mock_infra_cls:
            mock_infra = MagicMock()
            mock_infra_cls.return_value = mock_infra
            mock_infra.delete.return_value = True

            gcp_cli.gcp_infra()

            mock_infra.delete.assert_called_once()
            mock_infra.create.assert_not_called()
            mock_infra.update.assert_not_called()


def test_status_flag_calls_infra_status():
    with patch("sys.argv", ["aigear-infra", "--status"]):
        from aigear.cli import gcp_cli
        importlib.reload(gcp_cli)

        with patch("aigear.cli.gcp_cli.Infra") as mock_infra_cls:
            mock_infra = MagicMock()
            mock_infra_cls.return_value = mock_infra

            gcp_cli.gcp_infra()

            mock_infra.status.assert_called_once()
            mock_infra.create.assert_not_called()
            mock_infra.update.assert_not_called()
            mock_infra.delete.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
@pytest.mark.parametrize("failure", [False, RuntimeError("preflight failed")])
def test_failed_operations_exit_with_one(operation, failure):
    from aigear.cli import gcp_cli

    with patch("sys.argv", ["aigear-infra", f"--{operation}"]):
        with patch.object(gcp_cli, "Infra") as infra_cls:
            action = getattr(infra_cls.return_value, operation)
            if failure is False:
                action.return_value = False
            else:
                action.side_effect = failure
            with pytest.raises(SystemExit) as exc:
                gcp_cli.gcp_infra()
    assert exc.value.code == 1


def test_configuration_runtime_error_exits_with_one():
    from aigear.cli import gcp_cli

    with patch("sys.argv", ["aigear-infra", "--create"]):
        with patch.object(gcp_cli, "Infra", side_effect=RuntimeError("invalid config")):
            with pytest.raises(SystemExit) as exc:
                gcp_cli.gcp_infra()
    assert exc.value.code == 1

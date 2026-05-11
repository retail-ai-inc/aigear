import importlib
import sys
from unittest.mock import patch, MagicMock


def test_update_flag_calls_infra_update():
    with patch("sys.argv", ["aigear-gcp-infra", "--update"]):
        from aigear.cli import gcp_cli
        importlib.reload(gcp_cli)

        with patch("aigear.cli.gcp_cli.Infra") as mock_infra_cls:
            mock_infra = MagicMock()
            mock_infra_cls.return_value = mock_infra

            gcp_cli.gcp_infra()

            mock_infra.update.assert_called_once()
            mock_infra.create.assert_not_called()
            mock_infra.delete.assert_not_called()


def test_create_flag_does_not_call_update():
    with patch("sys.argv", ["aigear-gcp-infra", "--create"]):
        from aigear.cli import gcp_cli
        importlib.reload(gcp_cli)

        with patch("aigear.cli.gcp_cli.Infra") as mock_infra_cls:
            mock_infra = MagicMock()
            mock_infra_cls.return_value = mock_infra

            gcp_cli.gcp_infra()

            mock_infra.create.assert_called_once()
            mock_infra.update.assert_not_called()

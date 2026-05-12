import importlib
from unittest.mock import patch

import pytest


# ── --generate ────────────────────────────────────────────────────────────────


def test_generate_calls_generative_env_schema():
    with patch("sys.argv", ["aigear-env-schema", "--generate"]):
        import aigear.cli.env_schema as cli_mod

        importlib.reload(cli_mod)
        with patch("aigear.cli.env_schema.EnvConfig.generative_env_schema") as mock_fn:
            cli_mod.env_schema()
    mock_fn.assert_called_once_with(forced_generate=False)


def test_generate_force_passes_forced_generate_true():
    with patch("sys.argv", ["aigear-env-schema", "--generate", "--force"]):
        import aigear.cli.env_schema as cli_mod

        importlib.reload(cli_mod)
        with patch("aigear.cli.env_schema.EnvConfig.generative_env_schema") as mock_fn:
            cli_mod.env_schema()
    mock_fn.assert_called_once_with(forced_generate=True)


# ── --delete ──────────────────────────────────────────────────────────────────


def test_delete_calls_delete_env_schema():
    with patch("sys.argv", ["aigear-env-schema", "--delete"]):
        import aigear.cli.env_schema as cli_mod

        importlib.reload(cli_mod)
        with patch("aigear.cli.env_schema.EnvConfig.delete_env_schema") as mock_fn:
            cli_mod.env_schema()
    mock_fn.assert_called_once()


def test_delete_does_not_call_generate():
    with patch("sys.argv", ["aigear-env-schema", "--delete"]):
        import aigear.cli.env_schema as cli_mod

        importlib.reload(cli_mod)
        with patch("aigear.cli.env_schema.EnvConfig.delete_env_schema"):
            with patch(
                "aigear.cli.env_schema.EnvConfig.generative_env_schema"
            ) as mock_gen:
                cli_mod.env_schema()
    mock_gen.assert_not_called()


# ── --show ────────────────────────────────────────────────────────────────────


def test_show_calls_show_env_schema():
    with patch("sys.argv", ["aigear-env-schema", "--show"]):
        import aigear.cli.env_schema as cli_mod

        importlib.reload(cli_mod)
        with patch("aigear.cli.env_schema.EnvConfig.show_env_schema") as mock_fn:
            cli_mod.env_schema()
    mock_fn.assert_called_once()


def test_show_does_not_call_generate():
    with patch("sys.argv", ["aigear-env-schema", "--show"]):
        import aigear.cli.env_schema as cli_mod

        importlib.reload(cli_mod)
        with patch("aigear.cli.env_schema.EnvConfig.show_env_schema"):
            with patch(
                "aigear.cli.env_schema.EnvConfig.generative_env_schema"
            ) as mock_gen:
                cli_mod.env_schema()
    mock_gen.assert_not_called()


# ── no action ─────────────────────────────────────────────────────────────────


def test_no_action_exits():
    with patch("sys.argv", ["aigear-env-schema"]):
        import aigear.cli.env_schema as cli_mod

        importlib.reload(cli_mod)
        with pytest.raises(SystemExit):
            cli_mod.env_schema()

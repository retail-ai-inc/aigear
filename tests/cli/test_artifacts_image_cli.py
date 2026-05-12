import importlib
from unittest.mock import patch

import pytest


# ── --create ──────────────────────────────────────────────────────────────────

def test_create_dispatches_to_create_artifacts_image():
    with patch("sys.argv", ["cmd", "--create", "--dockerfile_path", "Dockerfile.pl"]):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with patch("aigear.cli.artifacts_image.create_artifacts_image", return_value=True) as mock_fn:
            cli_mod.docker_image()
    mock_fn.assert_called_once_with(
        dockerfile_path="Dockerfile.pl",
        build_context=".",
        is_service=False,
        is_build=True,
        is_push=False,
    )


def test_create_push_sets_is_push_true():
    with patch("sys.argv", ["cmd", "--create", "--dockerfile_path", "Dockerfile.pl", "--push"]):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with patch("aigear.cli.artifacts_image.create_artifacts_image", return_value=True) as mock_fn:
            cli_mod.docker_image()
    assert mock_fn.call_args.kwargs["is_push"] is True


# ── --delete ──────────────────────────────────────────────────────────────────

def test_delete_dispatches_to_delete_artifacts_image():
    with patch("sys.argv", ["cmd", "--delete"]):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with patch("aigear.cli.artifacts_image.delete_artifacts_image", return_value=True) as mock_fn:
            cli_mod.docker_image()
    mock_fn.assert_called_once_with(is_service=False, is_push=False)


def test_delete_push_sets_is_push_true():
    with patch("sys.argv", ["cmd", "--delete", "--push"]):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with patch("aigear.cli.artifacts_image.delete_artifacts_image", return_value=True) as mock_fn:
            cli_mod.docker_image()
    assert mock_fn.call_args.kwargs["is_push"] is True


# ── --retag ───────────────────────────────────────────────────────────────────

def test_retag_dispatches_with_src_and_target():
    argv = ["cmd", "--retag", "--src_tag", "v1.0", "--target_tag", "latest"]
    with patch("sys.argv", argv):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with patch("aigear.cli.artifacts_image.retag_artifacts_image", return_value=True) as mock_fn:
            cli_mod.docker_image()
    mock_fn.assert_called_once_with(
        src_tag="v1.0", target_tag="latest", is_service=False, is_push=False
    )


def test_retag_without_src_tag_exits():
    with patch("sys.argv", ["cmd", "--retag", "--target_tag", "latest"]):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with pytest.raises(SystemExit):
            cli_mod.docker_image()


def test_retag_without_target_tag_exits():
    with patch("sys.argv", ["cmd", "--retag", "--src_tag", "v1.0"]):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with pytest.raises(SystemExit):
            cli_mod.docker_image()


# ── no action ─────────────────────────────────────────────────────────────────

def test_no_action_exits():
    with patch("sys.argv", ["cmd"]):
        import aigear.cli.artifacts_image as cli_mod
        importlib.reload(cli_mod)
        with pytest.raises(SystemExit):
            cli_mod.docker_image()

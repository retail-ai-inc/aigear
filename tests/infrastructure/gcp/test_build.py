import pytest
from unittest.mock import patch

from aigear.infrastructure.gcp.build import CloudBuild, _escape_pattern


def _make_build(event="push", **kwargs):
    defaults = dict(
        project_id="my-project",
        region="asia-northeast1",
        trigger_name="my-trigger",
        event=event,
        branch_pattern="main",
    )
    defaults.update(kwargs)
    return CloudBuild(**defaults)


# ── _escape_pattern ───────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.build.platform.system", return_value="Linux")
def test_escape_pattern_on_non_windows_returns_unchanged(mock_sys):
    assert _escape_pattern("^main$") == "^main$"


@patch("aigear.infrastructure.gcp.build.platform.system", return_value="Windows")
def test_escape_pattern_on_windows_doubles_caret(mock_sys):
    assert _escape_pattern("^main$") == "^^main$"


@patch("aigear.infrastructure.gcp.build.platform.system", return_value="Windows")
def test_escape_pattern_on_windows_no_caret_returns_unchanged(mock_sys):
    assert _escape_pattern("main.*") == "main.*"


# ── CloudBuild._event_args ────────────────────────────────────────────────────

def test_event_args_push_returns_branch_pattern():
    cb = _make_build(event="push", branch_pattern="main")
    args = cb._event_args()
    assert any("--branch-pattern=" in a for a in args)


def test_event_args_tag_returns_tag_pattern():
    cb = _make_build(event="tag", tag_pattern="v.*")
    args = cb._event_args()
    assert any("--tag-pattern=" in a for a in args)


def test_event_args_pull_request_returns_pr_pattern():
    cb = _make_build(event="pull_request", branch_pattern="main")
    args = cb._event_args()
    assert any("--pull-request-pattern=" in a for a in args)


def test_event_args_manual_returns_empty():
    cb = _make_build(event="manual")
    assert cb._event_args() == []


def test_event_args_pubsub_returns_empty():
    cb = _make_build(event="pubsub")
    assert cb._event_args() == []


# ── CloudBuild.describe ───────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.build.run_sh")
def test_describe_returns_true_when_trigger_found(mock_run_sh):
    mock_run_sh.return_value = "name: my-trigger\nregion: asia-northeast1"
    cb = _make_build()
    assert cb.describe() is True


@patch("aigear.infrastructure.gcp.build.run_sh")
def test_describe_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    cb = _make_build()
    assert cb.describe() is False


@patch("aigear.infrastructure.gcp.build.run_sh")
def test_describe_returns_false_when_trigger_name_missing_from_output(mock_run_sh):
    mock_run_sh.return_value = "some unrelated output"
    cb = _make_build()
    assert cb.describe() is False


# ── CloudBuild.create ─────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.build.run_sh")
def test_create_uses_github_subcommand_for_push_event(mock_run_sh):
    mock_run_sh.return_value = "created"
    cb = _make_build(event="push", repo_owner="owner", repo_name="repo")
    cb.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "github" in cmd


@patch("aigear.infrastructure.gcp.build.run_sh")
def test_create_includes_repo_owner_and_name_for_github_event(mock_run_sh):
    mock_run_sh.return_value = "created"
    cb = _make_build(event="push", repo_owner="owner", repo_name="repo")
    cb.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "--repo-owner=owner" in cmd
    assert "--repo-name=repo" in cmd


@patch("aigear.infrastructure.gcp.build.run_sh")
def test_create_uses_pubsub_subcommand_for_pubsub_event(mock_run_sh):
    mock_run_sh.return_value = "created"
    cb = _make_build(event="pubsub")
    cb.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "pubsub" in cmd


@patch("aigear.infrastructure.gcp.build.run_sh")
def test_create_raises_on_error_in_output(mock_run_sh):
    mock_run_sh.return_value = "ERROR: something went wrong"
    cb = _make_build()
    with pytest.raises(RuntimeError):
        cb.create()


@patch("aigear.infrastructure.gcp.build.run_sh")
def test_create_includes_substitutions_when_set(mock_run_sh):
    mock_run_sh.return_value = "created"
    cb = _make_build(substitutions="_ENV=staging")
    cb.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "--substitutions=_ENV=staging" in cmd


# ── CloudBuild.update ─────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.build.run_sh")
def test_update_raises_on_error_in_output(mock_run_sh):
    mock_run_sh.return_value = "ERROR: trigger not found"
    cb = _make_build()
    with pytest.raises(RuntimeError):
        cb.update()


@patch("aigear.infrastructure.gcp.build.run_sh")
def test_update_includes_trigger_name_and_region(mock_run_sh):
    mock_run_sh.return_value = "updated"
    cb = _make_build()
    cb.update()
    cmd = mock_run_sh.call_args[0][0]
    assert "my-trigger" in cmd
    assert "--region=asia-northeast1" in cmd

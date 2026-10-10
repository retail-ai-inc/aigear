from unittest.mock import patch
import pytest

from aigear.infrastructure.gcp.function import CloudFunction


def _make_function():
    return CloudFunction(
        function_name="my-fn",
        region="asia-northeast1",
        entry_point="handler",
        topic_name="my-topic",
        project_id="my-project",
        service_account="sa@my-project.iam.gserviceaccount.com",
    )


@patch("aigear.infrastructure.gcp.function.run_sh")
def test_ensure_deploys_when_missing(mock_run_sh):
    fn = _make_function()
    with patch.object(fn, "describe", return_value=False), patch.object(
        fn, "deploy"
    ) as mock_deploy, patch.object(
        fn, "add_permissions_to_cloud_function"
    ) as mock_perms:
        fn.ensure("invoker@my-project.iam.gserviceaccount.com")
    mock_deploy.assert_called_once()
    mock_perms.assert_called_once_with(sa_email="invoker@my-project.iam.gserviceaccount.com")


@patch("aigear.infrastructure.gcp.function.run_sh")
def test_ensure_skips_deploy_when_exists(mock_run_sh):
    fn = _make_function()
    with patch.object(fn, "describe", return_value=True), patch.object(
        fn, "deploy"
    ) as mock_deploy, patch.object(fn, "add_permissions_to_cloud_function") as mock_perms:
        fn.ensure("invoker@my-project.iam.gserviceaccount.com")
    mock_deploy.assert_not_called()
    mock_perms.assert_called_once()


@pytest.mark.parametrize("wait,timeout", [(False, 30), (True, 600)])
@patch("aigear.infrastructure.gcp.function.run_sh")
def test_delete_waits_only_when_requested(mock_run_sh, wait, timeout):
    _make_function().delete(wait=wait)
    command = mock_run_sh.call_args.args[0]
    assert ("--async" in command) is (not wait)
    assert mock_run_sh.call_args.kwargs == {"check": True, "timeout": timeout}


@patch("aigear.infrastructure.gcp.function.run_sh", side_effect=RuntimeError("execution timeout"))
def test_delete_propagates_timeout(mock_run_sh):
    with pytest.raises(RuntimeError, match="timeout"):
        _make_function().delete(wait=True)


@patch("aigear.infrastructure.gcp.function.run_sh")
def test_delete_defaults_to_async(mock_run_sh):
    _make_function().delete()
    assert "--async" in mock_run_sh.call_args.args[0]


def test_function_source_render_refreshes_stale_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fn = _make_function()
    fn.project_name = "demo"
    source = tmp_path / "cloud_function/index.js"
    fn._function_path()
    text = source.read_text(encoding="utf-8")
    assert "{{PROJECTID}}" not in text
    assert "{{VENVBASEDIR}}" not in text
    assert "projectId: 'my-project'" in text
    assert "projectName: 'demo'" in text
    source.write_text("stale source", encoding="utf-8")
    fn._function_path()
    assert source.read_text(encoding="utf-8") == text
    assert (tmp_path / "cloud_function/package.json").exists()

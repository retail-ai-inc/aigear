from unittest.mock import patch

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

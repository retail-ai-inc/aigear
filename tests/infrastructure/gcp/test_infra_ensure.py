from unittest.mock import MagicMock, patch
import pytest
import json

from aigear.infrastructure.gcp.infra import Infra
from aigear.infrastructure.gcp import infra as infra_module


def _make_infra():
    """Return an Infra instance with all GCP dependencies mocked."""
    infra = Infra.__new__(Infra)
    cfg = MagicMock()
    cfg.gcp.iam.account_name = "my-sa"
    cfg.gcp.bucket.bucket_name = "my-bucket"
    cfg.gcp.bucket.bucket_name_for_release = "my-release-bucket"
    cfg.gcp.artifacts.repository_name = "my-repo"
    cfg.gcp.artifacts.image_tag = "latest"
    cfg.gcp.pub_sub.topic_name = "my-topic"
    cfg.gcp.kms.keyring_name = "my-keyring"
    cfg.gcp.kms.key_name = "my-key"
    cfg.gcp.cloud_build.trigger_name = "my-trigger"
    cfg.gcp.cloud_function.function_name = "my-function"
    cfg.gcp.kubernetes.cluster_name = "my-cluster"
    infra.aigear_config = cfg
    infra.project_id = "my-project"
    infra.location = "asia-northeast1"
    infra.environment = "staging"
    infra.service_account = "my-sa@my-project.iam.gserviceaccount.com"
    infra.service_accounts = MagicMock()
    infra.service_accounts.sa_email = infra.service_account
    infra.model_bucket = MagicMock()
    infra.release_model_bucket = MagicMock()
    infra.artifacts = MagicMock()
    infra.pubsub = MagicMock()
    infra.cloud_kms = MagicMock()
    infra.cloud_build = MagicMock()
    infra.cloud_function = MagicMock()
    infra.eventarc_trigger = MagicMock()
    infra.eventarc_trigger.trigger_name = "my-function-pubsub"
    infra.kubernetes_cluster = MagicMock()
    return infra


# ── _ensure_service_account ───────────────────────────────────────────────────


def test_ensure_service_account_creates_when_not_exists():
    infra = _make_infra()
    infra.service_accounts.describe.return_value = False
    infra._ensure_service_account()
    infra.service_accounts.create.assert_called_once()
    infra.service_accounts.add_iam_policy_binding.assert_called_once()


def test_ensure_service_account_skips_create_when_exists():
    infra = _make_infra()
    infra.service_accounts.describe.return_value = True
    infra._ensure_service_account()
    infra.service_accounts.create.assert_not_called()
    infra.service_accounts.add_iam_policy_binding.assert_called_once()


# ── _ensure_model_bucket ──────────────────────────────────────────────────────


def test_ensure_model_bucket_creates_and_grants_permissions_when_not_exists():
    infra = _make_infra()
    infra.model_bucket.describe.return_value = False
    infra._ensure_model_bucket()
    infra.model_bucket.create.assert_called_once()
    infra.model_bucket.add_permissions_to_gcs.assert_called_once_with(
        sa_email=infra.service_accounts.sa_email
    )


def test_ensure_model_bucket_skips_when_exists():
    infra = _make_infra()
    infra.model_bucket.describe.return_value = True
    infra._ensure_model_bucket()
    infra.model_bucket.create.assert_not_called()
    infra.model_bucket.add_permissions_to_gcs.assert_called_once_with(
        sa_email=infra.service_accounts.sa_email
    )


# ── _ensure_release_bucket ────────────────────────────────────────────────────


def test_ensure_release_bucket_creates_when_not_exists():
    infra = _make_infra()
    infra.release_model_bucket.describe.return_value = False
    infra._ensure_release_bucket()
    infra.release_model_bucket.create.assert_called_once()
    infra.release_model_bucket.add_permissions_to_gcs.assert_called_once_with(
        sa_email=infra.service_accounts.sa_email
    )


def test_ensure_release_bucket_skips_when_exists():
    infra = _make_infra()
    infra.release_model_bucket.describe.return_value = True
    infra._ensure_release_bucket()
    infra.release_model_bucket.create.assert_not_called()
    infra.release_model_bucket.add_permissions_to_gcs.assert_called_once_with(
        sa_email=infra.service_accounts.sa_email
    )


# ── _ensure_artifacts ─────────────────────────────────────────────────────────


def test_ensure_artifacts_creates_when_not_exists():
    infra = _make_infra()
    infra.artifacts.describe.return_value = False
    infra._ensure_artifacts()
    infra.artifacts.create.assert_called_once()


def test_ensure_artifacts_skips_when_exists():
    infra = _make_infra()
    infra.artifacts.describe.return_value = True
    infra._ensure_artifacts()
    infra.artifacts.create.assert_not_called()


# ── _ensure_pubsub ────────────────────────────────────────────────────────────


def test_ensure_pubsub_creates_and_grants_permissions_when_not_exists():
    infra = _make_infra()
    infra.pubsub.describe.return_value = False
    infra._ensure_pubsub()
    infra.pubsub.create.assert_called_once()
    infra.pubsub.add_permissions_to_pubsub.assert_called_once()


def test_ensure_pubsub_skips_when_exists():
    infra = _make_infra()
    infra.pubsub.describe.return_value = True
    infra._ensure_pubsub()
    infra.pubsub.create.assert_not_called()


# ── _ensure_cloud_build ───────────────────────────────────────────────────────


def test_ensure_cloud_build_creates_when_not_exists():
    infra = _make_infra()
    infra.cloud_build.describe.return_value = False
    infra._ensure_cloud_build()
    infra.cloud_build.create.assert_called_once()


def test_ensure_cloud_build_skips_when_exists():
    infra = _make_infra()
    infra.cloud_build.describe.return_value = True
    infra._ensure_cloud_build()
    infra.cloud_build.create.assert_not_called()


# ── _ensure_kubernetes_cluster ────────────────────────────────────────────────


def test_ensure_kubernetes_creates_when_not_exists():
    infra = _make_infra()
    infra.kubernetes_cluster.describe.return_value = False
    infra._ensure_kubernetes_cluster()
    infra.kubernetes_cluster.create.assert_called_once()


def test_ensure_kubernetes_skips_when_exists():
    infra = _make_infra()
    infra.kubernetes_cluster.describe.return_value = True
    infra._ensure_kubernetes_cluster()
    infra.kubernetes_cluster.create.assert_not_called()


# ── _ensure_kms ───────────────────────────────────────────────────────────────


def test_ensure_kms_creates_keyring_and_key_when_neither_exists():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = False
    infra.cloud_kms.describe_key.return_value = False
    infra._ensure_kms()
    infra.cloud_kms.create_keyring.assert_called_once()
    infra.cloud_kms.create_key.assert_called_once()
    infra.cloud_kms.add_permissions.assert_called_once()


def test_ensure_kms_skips_keyring_when_exists_but_enables_disabled_key_version():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = True
    infra.cloud_kms.describe_key.return_value = True
    infra.cloud_kms.describe_enabled_key_version.return_value = False
    infra._ensure_kms()
    infra.cloud_kms.create_keyring.assert_not_called()
    infra.cloud_kms.create_key.assert_not_called()
    infra.cloud_kms.enable_primary_key_version.assert_called_once()


def test_ensure_kms_skips_all_when_everything_exists():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = True
    infra.cloud_kms.describe_key.return_value = True
    infra.cloud_kms.describe_enabled_key_version.return_value = True
    infra._ensure_kms()
    infra.cloud_kms.create_keyring.assert_not_called()
    infra.cloud_kms.create_key.assert_not_called()
    infra.cloud_kms.enable_primary_key_version.assert_not_called()


# ── _delete_* methods ─────────────────────────────────────────────────────────


def test_delete_model_bucket_calls_delete_when_exists():
    infra = _make_infra()
    infra.model_bucket.describe.return_value = True
    infra._delete_model_bucket()
    infra.model_bucket.delete.assert_called_once()


def test_delete_model_bucket_skips_when_not_exists():
    infra = _make_infra()
    infra.model_bucket.describe.return_value = False
    infra._delete_model_bucket()
    infra.model_bucket.delete.assert_not_called()


def test_delete_release_bucket_calls_delete_when_exists():
    infra = _make_infra()
    infra.release_model_bucket.describe.return_value = True
    infra._delete_release_bucket()
    infra.release_model_bucket.delete.assert_called_once()


def test_delete_release_bucket_skips_when_not_exists():
    infra = _make_infra()
    infra.release_model_bucket.describe.return_value = False
    infra._delete_release_bucket()
    infra.release_model_bucket.delete.assert_not_called()


def test_delete_cloud_build_calls_delete_when_exists():
    infra = _make_infra()
    infra.cloud_build.describe.return_value = True
    infra._delete_cloud_build()
    infra.cloud_build.delete.assert_called_once()


def test_delete_cloud_build_skips_when_not_exists():
    infra = _make_infra()
    infra.cloud_build.describe.return_value = False
    infra._delete_cloud_build()
    infra.cloud_build.delete.assert_not_called()


def test_delete_kubernetes_calls_delete_when_exists():
    infra = _make_infra()
    infra.kubernetes_cluster.describe.return_value = True
    infra._delete_kubernetes_cluster()
    infra.kubernetes_cluster.delete.assert_called_once_with(wait=True)


def test_delete_kubernetes_skips_when_not_exists():
    infra = _make_infra()
    infra.kubernetes_cluster.describe.return_value = False
    infra._delete_kubernetes_cluster()
    infra.kubernetes_cluster.delete.assert_not_called()


def test_delete_service_account_calls_delete_when_exists():
    infra = _make_infra()
    infra.service_accounts.describe.return_value = True
    infra._delete_service_account()
    infra.service_accounts.delete.assert_called_once()


def test_delete_service_account_skips_when_not_exists():
    infra = _make_infra()
    infra.service_accounts.describe.return_value = False
    infra._delete_service_account()
    infra.service_accounts.delete.assert_not_called()


def test_delete_pubsub_calls_delete_when_exists():
    infra = _make_infra()
    infra.pubsub.describe.return_value = True
    infra._delete_pubsub()
    infra.pubsub.delete.assert_called_once()


def test_delete_pubsub_skips_when_not_exists():
    infra = _make_infra()
    infra.pubsub.describe.return_value = False
    infra._delete_pubsub()
    infra.pubsub.delete.assert_not_called()


# ── _status_check ─────────────────────────────────────────────────────────────


def test_status_check_returns_exists_when_check_fn_returns_true():
    infra = _make_infra()
    _, config_on, status = infra._status_check("My Resource", True, lambda: True)
    assert config_on is True
    assert status == "EXISTS"


def test_status_check_returns_not_found_when_check_fn_returns_false():
    infra = _make_infra()
    _, config_on, status = infra._status_check("My Resource", True, lambda: False)
    assert status == "NOT_FOUND"


def test_status_check_returns_none_state_when_config_off():
    infra = _make_infra()
    _, config_on, status = infra._status_check("My Resource", False, lambda: True)
    assert config_on is False
    assert status is None


def test_status_check_returns_error_string_on_exception():
    infra = _make_infra()

    def _raise():
        raise RuntimeError("boom")

    _, _, status = infra._status_check("My Resource", True, _raise)
    assert "ERROR" in status


def test_status_check_passes_through_string_result():
    infra = _make_infra()
    _, _, status = infra._status_check("My Resource", True, lambda: "EXISTS [key ✅]")
    assert status == "EXISTS [key ✅]"


# ── _status_kms ───────────────────────────────────────────────────────────────


def test_status_kms_returns_not_found_when_no_keyring():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = False
    result = infra._status_kms()
    assert "NOT_FOUND" in result


def test_status_kms_returns_partial_when_keyring_only():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = True
    infra.cloud_kms.describe_key.return_value = False
    result = infra._status_kms()
    assert result.startswith("PARTIAL")


def test_status_kms_returns_exists_with_enabled_when_all_present():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = True
    infra.cloud_kms.describe_key.return_value = True
    infra.cloud_kms.describe_enabled_key_version.return_value = True
    result = infra._status_kms()
    assert "EXISTS" in result
    assert "ENABLED" in result


def test_status_kms_returns_disabled_when_no_enabled_version():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = True
    infra.cloud_kms.describe_key.return_value = True
    infra.cloud_kms.describe_enabled_key_version.return_value = False
    result = infra._status_kms()
    assert "DISABLED" in result


# ── _build_substitutions ──────────────────────────────────────────────────────


def test_build_substitutions_contains_expected_keys():
    infra = _make_infra()
    with patch(
        "aigear.infrastructure.gcp.infra.get_image_name", return_value="my-image"
    ):
        result = infra._build_substitutions()
    assert "_ENVIRONMENT=staging" in result
    assert "_KMS_KEYRING=my-keyring" in result
    assert "_KMS_KEY=my-key" in result
    assert "_REPOSITORY=my-repo" in result
    assert "_IMAGE_TAG=latest" in result


def _make_operation_infra(*enabled):
    infra = _make_infra()
    for resource in (
        "iam", "bucket", "artifacts", "pub_sub", "kms", "cloud_build",
        "pre_vm_image", "kubernetes", "cloud_function",
    ):
        getattr(infra.aigear_config.gcp, resource).on = resource in enabled
    infra._preflight_check = MagicMock()
    infra.service_accounts.describe.return_value = True
    return infra


def test_ensure_cloud_function_passes_service_account():
    infra = _make_infra()
    infra._ensure_cloud_function()
    infra.cloud_function.ensure.assert_called_once_with(infra.service_account)


@pytest.mark.parametrize("result,success", [(False, False), (True, True), (None, True), (0, True)])
def test_step_honors_explicit_false(result, success, caplog):
    infra = _make_infra()
    caplog.set_level("INFO")
    assert infra._step("Resource", lambda: result) is success
    output = caplog.text
    assert ("Resource SUCCESS" in output) is success
    assert ("Resource FAILED" in output) is (not success)


def test_parallel_collects_false_results_and_exceptions():
    infra = _make_infra()
    failed = []
    infra._run_parallel({
        "false": lambda: False,
        "exception": MagicMock(side_effect=RuntimeError("denied")),
        "success": lambda: None,
    }, failed)
    assert set(failed) == {"false", "exception"}


@pytest.mark.parametrize("method", ["create", "update", "delete"])
def test_operations_return_true_when_enabled_steps_succeed(method):
    infra = _make_operation_infra("iam", "bucket", "cloud_function", "pub_sub", "kubernetes")
    assert getattr(infra, method)() is True


def test_create_returns_false_when_service_account_missing():
    infra = _make_operation_infra("cloud_function")
    infra.service_accounts.describe.return_value = False
    assert infra.create() is False
    infra.cloud_function.ensure.assert_not_called()


def test_create_returns_false_when_service_account_verification_fails(caplog):
    infra = _make_operation_infra("cloud_function")
    infra.service_accounts.describe.side_effect = RuntimeError("PERMISSION_DENIED")
    assert infra.create() is False
    infra.cloud_function.ensure.assert_not_called()
    assert "setup or verification failed" in caplog.text
    assert "not found" not in caplog.text


def test_create_blocks_eventarc_when_function_deploy_fails():
    infra = _make_operation_infra("cloud_function", "pub_sub")
    infra.cloud_function.ensure.side_effect = RuntimeError("deployment failed")
    assert infra.create() is False
    infra.eventarc_trigger.ensure.assert_not_called()


def test_update_reports_false_subscription_tuning_result():
    infra = _make_operation_infra("cloud_function", "pub_sub", "cloud_build")
    infra.eventarc_trigger.tune_push_subscriptions.return_value = False
    assert infra.update() is False
    infra.cloud_build.update.assert_called_once()


@pytest.mark.parametrize("failure", [False, RuntimeError("PERMISSION_DENIED")])
def test_delete_blocks_dependents_after_eventarc_failure(failure, caplog):
    infra = _make_operation_infra("iam", "cloud_function", "pub_sub", "bucket", "kubernetes")
    if failure is False:
        infra.eventarc_trigger.delete_if_exists.return_value = False
    else:
        infra.eventarc_trigger.delete_if_exists.side_effect = failure
    assert infra.delete() is False
    infra.cloud_function.describe.assert_not_called()
    infra.cloud_function.delete.assert_not_called()
    infra.pubsub.delete.assert_not_called()
    infra.model_bucket.delete.assert_called_once()
    infra.release_model_bucket.delete.assert_called_once()
    infra.kubernetes_cluster.delete.assert_called_once_with(wait=True)
    infra.service_accounts.delete.assert_not_called()
    assert "service account retained" in caplog.text


@pytest.mark.parametrize("resource,config", [
    ("cloud_function", "cloud_function"),
    ("kubernetes_cluster", "kubernetes"),
    ("model_bucket", "bucket"),
])
def test_delete_retains_account_after_failure_or_timeout(resource, config):
    infra = _make_operation_infra("iam", config, "artifacts")
    getattr(infra, resource).delete.side_effect = RuntimeError("execution timeout")
    assert infra.delete() is False
    infra.artifacts.delete.assert_called_once()
    infra.service_accounts.delete.assert_not_called()


def test_delete_retains_account_when_existence_query_fails():
    infra = _make_operation_infra("iam", "bucket")
    infra.model_bucket.describe.side_effect = RuntimeError("PERMISSION_DENIED")
    assert infra.delete() is False
    infra.model_bucket.delete.assert_not_called()
    infra.release_model_bucket.delete.assert_called_once()
    infra.service_accounts.delete.assert_not_called()


def test_delete_account_only_after_all_enabled_deletions_finish():
    infra = _make_operation_infra("iam", "cloud_function", "pub_sub", "kubernetes")
    order = MagicMock()
    order.attach_mock(infra.eventarc_trigger.delete_if_exists, "eventarc")
    order.attach_mock(infra.cloud_function.delete, "function")
    order.attach_mock(infra.pubsub.delete, "topic")
    order.attach_mock(infra.kubernetes_cluster.delete, "cluster")
    order.attach_mock(infra.service_accounts.delete, "account")
    assert infra.delete() is True
    calls = [entry[0] for entry in order.mock_calls]
    assert calls[0:2] == ["eventarc", "function"]
    assert set(calls[2:-1]) == {"topic", "cluster"}
    assert calls[-1] == "account"
    infra.cloud_function.delete.assert_called_once_with(wait=True)
    infra.kubernetes_cluster.delete.assert_called_once_with(wait=True)


def test_delete_treats_missing_resources_as_success():
    infra = _make_operation_infra("iam", "cloud_function", "pub_sub", "kubernetes")
    infra.eventarc_trigger.delete_if_exists.return_value = True
    for resource in (infra.cloud_function, infra.pubsub, infra.kubernetes_cluster):
        resource.describe.return_value = False
    assert infra.delete() is True
    infra.cloud_function.delete.assert_not_called()
    infra.pubsub.delete.assert_not_called()
    infra.kubernetes_cluster.delete.assert_not_called()
    infra.service_accounts.delete.assert_called_once()


def test_disabled_resources_are_not_deleted():
    infra = _make_operation_infra("iam")
    assert infra.delete() is True
    infra.eventarc_trigger.delete_if_exists.assert_not_called()
    infra.cloud_function.describe.assert_not_called()
    infra.model_bucket.delete.assert_not_called()
    infra.service_accounts.delete.assert_called_once()


def _preflight_command(auth_responses, project_responses, failure=None):
    auth = iter(auth_responses)
    projects = iter(project_responses)

    def run(command, **kwargs):
        assert kwargs["check"] is True
        if failure and command[1:3] == failure[0]:
            raise RuntimeError(failure[1])
        if command[1:3] == ["auth", "list"]:
            return json.dumps(next(auth))
        if command[1:3] == ["config", "get-value"]:
            return next(projects)
        if command[1:3] == ["auth", "login"]:
            assert kwargs["timeout"] == 300
        return ""

    return run


def test_preflight_rechecks_account_and_project_after_changes():
    infra = _make_infra()
    active = [{"account": "owner@example.com", "status": "ACTIVE"}]
    with patch("aigear.infrastructure.gcp.infra.run_sh", side_effect=_preflight_command(
        [[], active], ["old-project", "my-project"],
    )) as run:
        infra._preflight_check()
    assert sum(c.args[0][1:3] == ["auth", "list"] for c in run.call_args_list) == 2
    assert sum(c.args[0][1:3] == ["config", "get-value"] for c in run.call_args_list) == 2


@pytest.mark.parametrize("auth,projects,failure,message", [
    ([[]], ["my-project"], (["auth", "login"], "login cancelled"), "cancelled"),
    ([[], []], ["my-project"], None, "No active"),
    ([[{"account": "owner", "status": "ACTIVE"}]], ["old-project"],
     (["config", "set"], "project switch denied"), "denied"),
    ([[{"account": "owner", "status": "ACTIVE"}]], ["old-project", "old-project"],
     None, "switch failed"),
    ([[]], ["my-project"], (["auth", "list"], "execution timeout"), "timeout"),
])
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_preflight_failure_stops_resource_operations(auth, projects, failure, message, operation):
    infra = _make_infra()
    with patch("aigear.infrastructure.gcp.infra.run_sh", side_effect=_preflight_command(
        auth, projects, failure,
    )):
        with pytest.raises(RuntimeError, match=message):
            getattr(infra, operation)()
    infra.service_accounts.describe.assert_not_called()
    infra.cloud_function.ensure.assert_not_called()
    infra.cloud_build.update.assert_not_called()
    infra.eventarc_trigger.delete_if_exists.assert_not_called()


def test_status_counts_missing_kms_key_as_partial(capsys, caplog):
    caplog.set_level("INFO")
    infra = _make_operation_infra("kms")
    infra.cloud_kms.describe_keyring.return_value = True
    infra.cloud_kms.describe_key.return_value = False
    infra.status()
    output = capsys.readouterr().out
    assert "PARTIAL" in output
    assert "0 exist" in caplog.text
    assert "1 partial" in caplog.text


def test_status_reports_kms_query_errors(capsys, caplog):
    caplog.set_level("INFO")
    infra = _make_operation_infra("kms")
    infra.cloud_kms.describe_keyring.side_effect = RuntimeError("PERMISSION_DENIED")
    infra.status()
    output = capsys.readouterr().out
    assert "ERROR: PERMISSION_DENIED" in output
    assert "1 error" in caplog.text


RESOURCE_QUERIES = [
    pytest.param("Bucket", ("bucket", "region", "project"), "describe", id="bucket"),
    pytest.param("Artifacts", ("repo", "region", "project"), "describe", id="artifacts"),
    pytest.param("CloudBuild", ("project", "region", "trigger"), "describe", id="build"),
    pytest.param("CloudFunction", ("fn", "region", "handler", "topic", "project", "sa"), "describe", id="function"),
    pytest.param("KubernetesCluster", ("cluster", "region", 1, 1, 3, "project"), "describe", id="gke"),
    pytest.param("ServiceAccounts", ("project", "sa"), "describe", id="iam"),
    pytest.param("CloudKMS", ("project", "region", "ring", "key"), "describe_keyring", id="keyring"),
    pytest.param("CloudKMS", ("project", "region", "ring", "key"), "describe_key", id="key"),
    pytest.param("PubSub", ("topic", "project"), "describe", id="pubsub"),
    pytest.param("EventarcPubSubTrigger", ("trigger", "region", "project", "fn", "region", "topic", "sa"), "describe", id="eventarc"),
]


@pytest.mark.parametrize("resource_class,args,method", RESOURCE_QUERIES)
@pytest.mark.parametrize("error", ["NOT_FOUND", "PERMISSION_DENIED", "execution timeout"])
def test_resource_queries_only_treat_not_found_as_missing(resource_class, args, method, error):
    resource = getattr(infra_module, resource_class)(*args)
    with patch(f"{type(resource).__module__}.run_sh", side_effect=RuntimeError(error)) as run:
        if error == "NOT_FOUND":
            assert getattr(resource, method)() is False
        else:
            with pytest.raises(RuntimeError, match=error):
                getattr(resource, method)()
    assert run.call_args.kwargs["check"] is True


@pytest.mark.parametrize("resource_class,args,method", RESOURCE_QUERIES)
def test_resource_deletions_propagate_command_failure(resource_class, args, method):
    resource = getattr(infra_module, resource_class)(*args)
    with patch(f"{type(resource).__module__}.run_sh", side_effect=RuntimeError("PERMISSION_DENIED")) as run:
        with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
            resource.delete()
    assert run.call_args.kwargs["check"] is True

from unittest.mock import MagicMock, patch

from aigear.infrastructure.gcp.infra import Infra


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
    infra.model_bucket = MagicMock()
    infra.release_model_bucket = MagicMock()
    infra.artifacts = MagicMock()
    infra.pubsub = MagicMock()
    infra.cloud_kms = MagicMock()
    infra.cloud_build = MagicMock()
    infra.cloud_function = MagicMock()
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
    infra.service_accounts.add_iam_policy_binding.assert_not_called()


# ── _ensure_model_bucket ──────────────────────────────────────────────────────

def test_ensure_model_bucket_creates_and_grants_permissions_when_not_exists():
    infra = _make_infra()
    infra.model_bucket.describe.return_value = False
    infra._ensure_model_bucket()
    infra.model_bucket.create.assert_called_once()
    infra.model_bucket.add_permissions_to_gcs.assert_called_once()


def test_ensure_model_bucket_skips_when_exists():
    infra = _make_infra()
    infra.model_bucket.describe.return_value = True
    infra._ensure_model_bucket()
    infra.model_bucket.create.assert_not_called()


# ── _ensure_release_bucket ────────────────────────────────────────────────────

def test_ensure_release_bucket_creates_when_not_exists():
    infra = _make_infra()
    infra.release_model_bucket.describe.return_value = False
    infra._ensure_release_bucket()
    infra.release_model_bucket.create.assert_called_once()
    infra.release_model_bucket.add_permissions_to_gcs.assert_called_once()


def test_ensure_release_bucket_skips_when_exists():
    infra = _make_infra()
    infra.release_model_bucket.describe.return_value = True
    infra._ensure_release_bucket()
    infra.release_model_bucket.create.assert_not_called()


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
    infra.kubernetes_cluster.delete.assert_called_once()


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


def test_status_kms_returns_exists_but_no_key_when_keyring_only():
    infra = _make_infra()
    infra.cloud_kms.describe_keyring.return_value = True
    infra.cloud_kms.describe_key.return_value = False
    result = infra._status_kms()
    assert "EXISTS" in result


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
    with patch("aigear.infrastructure.gcp.infra.get_image_name", return_value="my-image"):
        result = infra._build_substitutions()
    assert "_ENVIRONMENT=staging" in result
    assert "_KMS_KEYRING=my-keyring" in result
    assert "_KMS_KEY=my-key" in result
    assert "_REPOSITORY=my-repo" in result
    assert "_IMAGE_TAG=latest" in result

from unittest.mock import patch

from aigear.infrastructure.gcp.iam import ServiceAccounts


def _make_sa(**kwargs):
    defaults = dict(project_id="my-project", account_name="my-sa")
    defaults.update(kwargs)
    return ServiceAccounts(**defaults)


def test_sa_email_is_constructed_correctly():
    sa = _make_sa()
    assert sa.sa_email == "my-sa@my-project.iam.gserviceaccount.com"


# ── ServiceAccounts.create ────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_create_builds_correct_base_command(mock_run_sh):
    sa = _make_sa()
    sa.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "gcloud" in cmd
    assert "iam" in cmd
    assert "service-accounts" in cmd
    assert "create" in cmd
    assert "my-sa" in cmd
    assert "--project=my-project" in cmd


@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_create_appends_description_when_set(mock_run_sh):
    sa = _make_sa(description="Test SA")
    sa.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "--description=Test SA" in cmd


@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_create_appends_display_name_when_set(mock_run_sh):
    sa = _make_sa(display_name="My SA")
    sa.create()
    cmd = mock_run_sh.call_args[0][0]
    assert "--display-name=My SA" in cmd


@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_create_omits_description_when_not_set(mock_run_sh):
    sa = _make_sa()
    sa.create()
    cmd = mock_run_sh.call_args[0][0]
    assert not any("--description" in arg for arg in cmd)


# ── ServiceAccounts.describe ──────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_describe_returns_true_when_sa_exists(mock_run_sh):
    mock_run_sh.return_value = "name: projects/my-project/serviceAccounts/my-sa@..."
    sa = _make_sa()
    assert sa.describe() is True


@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_describe_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    sa = _make_sa()
    assert sa.describe() is False


@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_describe_returns_false_when_permission_denied(mock_run_sh):
    mock_run_sh.return_value = "ERROR: PERMISSION_DENIED"
    sa = _make_sa()
    assert sa.describe() is False


@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_describe_returns_false_when_output_empty(mock_run_sh):
    mock_run_sh.return_value = ""
    sa = _make_sa()
    assert sa.describe() is False


# ── ServiceAccounts.delete ────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.iam.run_sh")
def test_delete_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    sa = _make_sa()
    sa.delete()
    cmd = mock_run_sh.call_args[0][0]
    assert "delete" in cmd
    assert "my-sa@my-project.iam.gserviceaccount.com" in cmd
    assert "--quiet" in cmd
    assert "--project=my-project" in cmd

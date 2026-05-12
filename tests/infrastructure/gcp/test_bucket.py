from unittest.mock import patch

from aigear.infrastructure.gcp.bucket import Bucket


def _make_bucket():
    return Bucket(
        bucket_name="my-bucket",
        location="asia-northeast1",
        project_id="my-project",
    )


def test_bucket_gs_uri_has_gs_prefix():
    bucket = _make_bucket()
    assert bucket.bucket_gs == "gs://my-bucket"


# ── Bucket.create ─────────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.bucket.run_sh")
def test_create_builds_correct_command(mock_run_sh):
    bucket = _make_bucket()
    bucket.create()
    mock_run_sh.assert_called_once_with(
        [
            "gcloud", "storage", "buckets", "create", "gs://my-bucket",
            "--location=asia-northeast1",
            "--uniform-bucket-level-access",
            "--project=my-project",
        ],
        check=True,
    )


# ── Bucket.describe ───────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.bucket.run_sh")
def test_describe_returns_true_when_bucket_found(mock_run_sh):
    mock_run_sh.return_value = "gs://my-bucket  created: 2024-01-01"
    bucket = _make_bucket()
    assert bucket.describe() is True


@patch("aigear.infrastructure.gcp.bucket.run_sh")
def test_describe_returns_false_when_not_found_error(mock_run_sh):
    mock_run_sh.return_value = "ERROR: BucketNotFoundException: NOT_FOUND"
    bucket = _make_bucket()
    assert bucket.describe() is False


@patch("aigear.infrastructure.gcp.bucket.run_sh")
def test_describe_returns_false_when_bucket_gs_missing_from_output(mock_run_sh):
    mock_run_sh.return_value = "some other output"
    bucket = _make_bucket()
    assert bucket.describe() is False


@patch("aigear.infrastructure.gcp.bucket.run_sh")
def test_describe_returns_false_when_error_in_output(mock_run_sh):
    # bucket_gs is in output but so is ERROR → False
    mock_run_sh.return_value = "gs://my-bucket ERROR: something bad"
    bucket = _make_bucket()
    assert bucket.describe() is False


# ── Bucket.add_permissions_to_gcs ────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.bucket.run_sh")
def test_add_permissions_builds_correct_command(mock_run_sh):
    bucket = _make_bucket()
    bucket.add_permissions_to_gcs(sa_email="sa@my-project.iam.gserviceaccount.com")
    mock_run_sh.assert_called_once_with(
        [
            "gcloud", "storage", "buckets", "add-iam-policy-binding", "gs://my-bucket",
            "--member=serviceAccount:sa@my-project.iam.gserviceaccount.com",
            "--role=roles/storage.admin",
            "--project=my-project",
        ],
        check=True,
    )


# ── Bucket.delete ─────────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.bucket.run_sh")
def test_delete_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    bucket = _make_bucket()
    bucket.delete()
    mock_run_sh.assert_called_once_with(
        [
            "gcloud", "storage", "rm", "-r", "gs://my-bucket",
            "--project=my-project",
        ]
    )

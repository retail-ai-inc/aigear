from unittest.mock import patch

from aigear.infrastructure.gcp.pub_sub import PubSub


def _make_pubsub():
    return PubSub(topic_name="my-topic", project_id="my-project")


# ── PubSub.create ─────────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_create_builds_correct_command(mock_run_sh):
    ps = _make_pubsub()
    ps.create()
    mock_run_sh.assert_called_once_with(
        ["gcloud", "pubsub", "topics", "create", "my-topic", "--project=my-project"],
        check=True,
    )


# ── PubSub.describe ───────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_describe_returns_true_when_topic_exists(mock_run_sh):
    mock_run_sh.return_value = "name: projects/my-project/topics/my-topic"
    ps = _make_pubsub()
    assert ps.describe() is True


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_describe_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    ps = _make_pubsub()
    assert ps.describe() is False


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_describe_returns_false_when_output_empty(mock_run_sh):
    mock_run_sh.return_value = ""
    ps = _make_pubsub()
    assert ps.describe() is False


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_describe_returns_false_when_name_not_in_output(mock_run_sh):
    mock_run_sh.return_value = "some unrelated output"
    ps = _make_pubsub()
    assert ps.describe() is False


# ── PubSub.add_permissions_to_pubsub ─────────────────────────────────────────

@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_add_permissions_grants_publisher_and_subscriber(mock_run_sh):
    ps = _make_pubsub()
    ps.add_permissions_to_pubsub(sa_email="sa@my-project.iam.gserviceaccount.com")
    assert mock_run_sh.call_count == 2
    all_calls = " ".join(str(c) for c in mock_run_sh.call_args_list)
    assert "roles/pubsub.publisher" in all_calls
    assert "roles/pubsub.subscriber" in all_calls


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_add_permissions_uses_full_topic_path(mock_run_sh):
    ps = _make_pubsub()
    ps.add_permissions_to_pubsub(sa_email="sa@proj.iam.gserviceaccount.com")
    all_calls = " ".join(str(c) for c in mock_run_sh.call_args_list)
    assert "projects/my-project/topics/my-topic" in all_calls


# ── PubSub.delete ─────────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_delete_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    ps = _make_pubsub()
    ps.delete()
    cmd = mock_run_sh.call_args[0][0]
    assert "pubsub" in cmd
    assert "topics" in cmd
    assert "delete" in cmd
    assert "my-topic" in cmd
    assert "--project=my-project" in cmd

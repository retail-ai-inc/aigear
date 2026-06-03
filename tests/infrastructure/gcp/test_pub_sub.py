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


# ── PubSub.describe_subscription / subscription_status ──────────────────────


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_describe_subscription_uses_gcloud_describe(mock_run_sh):
    mock_run_sh.return_value = "projects/my-project/topics/my-topic\n"
    ps = _make_pubsub()
    sub = "projects/my-project/subscriptions/my-sub"
    assert ps.describe_subscription(sub) == "projects/my-project/topics/my-topic"
    cmd = mock_run_sh.call_args[0][0]
    assert cmd[:4] == ["gcloud", "pubsub", "subscriptions", "describe"]
    assert cmd[4] == "my-sub"
    assert "--format=value(topic)" in cmd


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_describe_subscription_returns_none_when_missing(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    ps = _make_pubsub()
    assert ps.describe_subscription("projects/my-project/subscriptions/gone") is None


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_subscription_status_healthy(mock_run_sh):
    mock_run_sh.return_value = "projects/my-project/topics/my-topic\n"
    ps = _make_pubsub()
    assert (
        ps.subscription_status("projects/my-project/subscriptions/sub")
        == "healthy"
    )


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_subscription_status_orphan(mock_run_sh):
    mock_run_sh.return_value = "projects/my-project/topics/other-topic\n"
    ps = _make_pubsub()
    assert (
        ps.subscription_status("projects/my-project/subscriptions/sub")
        == "orphan"
    )


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_subscription_status_missing(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    ps = _make_pubsub()
    assert (
        ps.subscription_status("projects/my-project/subscriptions/sub")
        == "missing"
    )


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_find_orphan_subscriptions(mock_run_sh):
    mock_run_sh.side_effect = [
        "projects/my-project/topics/deleted-topic\n",
        "projects/my-project/topics/my-topic\n",
    ]
    ps = _make_pubsub()
    orphans = ps.find_orphan_subscriptions(
        [
            "projects/my-project/subscriptions/orphan-sub",
            "projects/my-project/subscriptions/good-sub",
        ]
    )
    assert orphans == ["projects/my-project/subscriptions/orphan-sub"]


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_tune_push_subscription_updates_ack_and_retry(mock_run_sh):
    ps = _make_pubsub()
    sub = "projects/my-project/subscriptions/eventarc-sub"
    ps.tune_push_subscription(sub, ack_deadline_sec=300, min_retry_delay_sec=60)
    cmd = mock_run_sh.call_args[0][0]
    assert cmd[:4] == ["gcloud", "pubsub", "subscriptions", "update"]
    assert cmd[4] == "eventarc-sub"
    assert "--ack-deadline=300" in cmd
    assert "--min-retry-delay=60s" in cmd


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_find_all_healthy_subscriptions_dedupes(mock_run_sh):
    mock_run_sh.side_effect = [
        "projects/my-project/topics/my-topic\n",
        "projects/my-project/topics/my-topic\n",
    ]
    ps = _make_pubsub()
    sub = "projects/my-project/subscriptions/eventarc-sub"
    healthy = ps.find_all_healthy_subscriptions([sub, sub])
    assert healthy == [sub]


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_ensure_push_subscription_tuned_skips_when_already_ok(mock_run_sh):
    mock_run_sh.return_value = "300\t60s\n"
    ps = _make_pubsub()
    sub = "projects/my-project/subscriptions/eventarc-sub"
    assert ps.ensure_push_subscription_tuned(sub) is True
    assert mock_run_sh.call_count == 1
    assert mock_run_sh.call_args[0][0][3] == "describe"


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_ensure_push_subscription_tuned_updates_short_ack(mock_run_sh):
    mock_run_sh.side_effect = ["10\t10s\n", ""]
    ps = _make_pubsub()
    sub = "projects/my-project/subscriptions/eventarc-sub"
    assert ps.ensure_push_subscription_tuned(sub) is True
    update_cmd = mock_run_sh.call_args[0][0]
    assert update_cmd[3] == "update"
    assert "--ack-deadline=300" in update_cmd


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


# ── PubSub.list_subscriptions / has_healthy_subscription ──────────────────────


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_list_subscriptions_uses_uri(mock_run_sh):
    mock_run_sh.return_value = "projects/my-project/subscriptions/eventarc-sub"
    ps = _make_pubsub()
    assert ps.list_subscriptions() == ["projects/my-project/subscriptions/eventarc-sub"]
    assert "--uri" in mock_run_sh.call_args[0][0]


@patch("aigear.infrastructure.gcp.pub_sub.run_sh")
def test_has_healthy_subscription_via_describe(mock_run_sh):
    mock_run_sh.return_value = "projects/my-project/topics/my-topic\n"
    ps = _make_pubsub()
    assert ps.has_healthy_subscription(
        ["projects/my-project/subscriptions/eventarc-sub"]
    ) is True


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

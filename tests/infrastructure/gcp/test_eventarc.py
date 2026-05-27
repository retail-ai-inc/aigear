from unittest.mock import patch

from aigear.infrastructure.gcp.eventarc import EventarcPubSubTrigger, pubsub_trigger_name


def _make_trigger():
    return EventarcPubSubTrigger(
        trigger_name="my-fn-pubsub",
        location="asia-northeast1",
        project_id="my-project",
        function_name="my-fn",
        function_region="asia-northeast1",
        topic_name="my-topic",
        trigger_service_account="sa@my-project.iam.gserviceaccount.com",
    )


def test_pubsub_trigger_name_suffix():
    assert pubsub_trigger_name("my-fn") == "my-fn-pubsub"


def test_transport_topic_path():
    trigger = _make_trigger()
    assert trigger.transport_topic == "projects/my-project/topics/my-topic"


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_describe_returns_true_when_trigger_exists(mock_run_sh):
    mock_run_sh.return_value = (
        "name: projects/my-project/locations/asia-northeast1/triggers/my-fn-pubsub\n"
    )
    assert _make_trigger().describe() is True


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_describe_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    assert _make_trigger().describe() is False


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_create_matches_official_gcloud_flags(mock_run_sh):
    trigger = _make_trigger()
    trigger.create()
    cmd = mock_run_sh.call_args[0][0]
    assert cmd[:4] == ["gcloud", "eventarc", "triggers", "create"]
    assert "my-fn-pubsub" in cmd
    assert "--destination-run-service=my-fn" in cmd
    assert "--destination-run-region=asia-northeast1" in cmd
    assert (
        '--event-filters=type=google.cloud.pubsub.topic.v1.messagePublished' in cmd
    )
    assert "--transport-topic=projects/my-project/topics/my-topic" in cmd
    assert (
        "--service-account=sa@my-project.iam.gserviceaccount.com" in cmd
    )


@patch("aigear.infrastructure.gcp.pub_sub.PubSub.has_subscriptions", return_value=True)
@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_ensure_creates_trigger_when_missing(mock_run_sh, _mock_subs):
    trigger = _make_trigger()
    with patch.object(trigger, "describe", side_effect=[False, True]):
        trigger.ensure()
    assert mock_run_sh.call_count >= 2


@patch("aigear.infrastructure.gcp.pub_sub.PubSub.has_subscriptions", return_value=False)
@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_ensure_raises_when_no_subscription(mock_run_sh, _mock_subs):
    trigger = _make_trigger()
    with patch.object(trigger, "describe", return_value=True):
        try:
            trigger.ensure()
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "no subscription" in str(exc).lower()


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_delete_if_exists_skips_when_missing(mock_run_sh):
    trigger = _make_trigger()
    with patch.object(trigger, "describe", return_value=False):
        trigger.delete_if_exists()
    mock_run_sh.assert_not_called()


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_delete_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    _make_trigger().delete()
    cmd = mock_run_sh.call_args[0][0]
    assert cmd == [
        "gcloud",
        "eventarc",
        "triggers",
        "delete",
        "my-fn-pubsub",
        "--location=asia-northeast1",
        "--project=my-project",
        "--quiet",
    ]

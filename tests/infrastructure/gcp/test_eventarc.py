from unittest.mock import patch

from aigear.infrastructure.gcp.eventarc import (
    EventarcPubSubTrigger,
    pubsub_trigger_name,
)


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
def test_describe_transport_parses_subscription(mock_run_sh):
    mock_run_sh.return_value = (
        "my-fn-pubsub\tprojects/my-project/subscriptions/eventarc-sub\n"
    )
    exists, sub = _make_trigger()._describe_transport()
    assert exists is True
    assert sub == "projects/my-project/subscriptions/eventarc-sub"


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
    assert "--event-filters=type=google.cloud.pubsub.topic.v1.messagePublished" in cmd
    assert "--transport-topic=projects/my-project/topics/my-topic" in cmd
    assert "--service-account=sa@my-project.iam.gserviceaccount.com" in cmd


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_ensure_fast_path_when_subscription_healthy(mock_run_sh):
    trigger = _make_trigger()
    transport_sub = "projects/my-project/subscriptions/eventarc-sub"
    with patch.object(trigger, "create") as mock_create:
        with patch.object(
            trigger, "_describe_transport", return_value=(True, transport_sub)
        ):
            with patch.object(trigger, "_is_ready", return_value=True):
                with patch.object(trigger, "_tune_push_subscription") as mock_tune:
                    with patch(
                        "aigear.infrastructure.gcp.pub_sub.PubSub.find_healthy_subscription",
                        return_value=transport_sub,
                    ):
                        trigger.ensure()
    mock_create.assert_not_called()
    mock_tune.assert_called_once()


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_ensure_creates_trigger_when_missing(mock_run_sh):
    trigger = _make_trigger()
    with patch.object(trigger, "_wait_for_ready", return_value=True):
        with patch.object(trigger, "_describe_transport", return_value=(False, None)):
            trigger.ensure()
    assert mock_run_sh.call_count >= 2


@patch("aigear.infrastructure.gcp.eventarc.time.sleep")
@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_ensure_raises_when_no_healthy_subscription(mock_run_sh, _mock_sleep):
    trigger = _make_trigger()
    with patch.object(trigger, "_delete_orphan_subscriptions"):
        with patch.object(trigger, "_describe_transport", return_value=(False, None)):
            with patch.object(trigger, "_wait_for_ready", return_value=False):
                try:
                    trigger.ensure()
                    assert False, "expected RuntimeError"
                except RuntimeError as exc:
                    assert "healthy" in str(exc).lower()


@patch("aigear.infrastructure.gcp.eventarc.time.sleep")
@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_ensure_recreates_orphan_trigger(mock_run_sh, _mock_sleep):
    trigger = _make_trigger()
    transport_sub = "projects/my-project/subscriptions/eventarc-sub"
    with patch.object(trigger, "_grant_event_receiver") as mock_grant:
        with patch.object(trigger, "delete", return_value=True) as mock_delete:
            with patch.object(trigger, "create") as mock_create:
                with patch.object(trigger, "_wait_for_ready", return_value=True):
                    with patch.object(
                        trigger,
                        "_describe_transport",
                        return_value=(True, transport_sub),
                    ):
                        with patch.object(trigger, "_is_ready", return_value=False):
                            with patch.object(
                                trigger, "_delete_orphan_subscriptions"
                            ) as mock_cleanup:
                                trigger.ensure()
    mock_cleanup.assert_called_once()
    mock_delete.assert_called_once()
    mock_create.assert_called_once()
    mock_grant.assert_not_called()


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_delete_if_exists_skips_when_missing(mock_run_sh):
    trigger = _make_trigger()
    with patch.object(trigger, "describe", return_value=False):
        trigger.delete_if_exists()
    mock_run_sh.assert_not_called()


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_delete_builds_correct_command(mock_run_sh):
    mock_run_sh.return_value = ""
    assert _make_trigger().delete() is True
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

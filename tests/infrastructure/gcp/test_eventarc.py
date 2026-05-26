from unittest.mock import patch

from aigear.infrastructure.gcp.eventarc import EventarcTrigger, trigger_name_for_function


def _make_trigger():
    return EventarcTrigger(
        trigger_name="my-fn-pubsub",
        function_name="my_fn",
        region="asia-northeast1",
        topic_name="my-topic",
        project_id="my-project",
        trigger_service_account="sa@my-project.iam.gserviceaccount.com",
    )


def test_trigger_name_for_function_normalizes_underscores():
    assert trigger_name_for_function("test_sklearn_pipeline_run") == (
        "test-sklearn-pipeline-run-pubsub"
    )


def test_trigger_name_for_function_from_class():
    trigger = EventarcTrigger.from_function(
        function_name="My_Function",
        region="asia-northeast1",
        topic_name="my-topic",
        project_id="my-project",
        trigger_service_account="sa@my-project.iam.gserviceaccount.com",
    )
    assert trigger.trigger_name == "my-function-pubsub"
    assert trigger.transport_topic == "projects/my-project/topics/my-topic"


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_describe_returns_true_when_trigger_exists(mock_run_sh):
    mock_run_sh.return_value = (
        "name: projects/my-project/locations/asia-northeast1/triggers/my-fn-pubsub"
    )
    assert _make_trigger().describe() is True


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_describe_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    assert _make_trigger().describe() is False


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_create_builds_expected_command(mock_run_sh):
    trigger = _make_trigger()
    trigger.create()
    cmd = mock_run_sh.call_args[0][0]
    assert cmd[0:3] == ["gcloud", "eventarc", "triggers"]
    assert "create" in cmd
    assert "my-fn-pubsub" in cmd
    assert "--destination-run-service=my_fn" in cmd
    assert (
        "--transport-topic=projects/my-project/topics/my-topic" in cmd
    )
    assert (
        "--event-filters=type=google.cloud.pubsub.topic.v1.messagePublished"
        in cmd
    )
    mock_run_sh.assert_called_once()
    assert mock_run_sh.call_args[1]["check"] is True


@patch("aigear.infrastructure.gcp.eventarc.run_sh")
def test_add_permissions_grants_event_receiver(mock_run_sh):
    trigger = _make_trigger()
    trigger.add_permissions(sa_email="sa@my-project.iam.gserviceaccount.com")
    cmd = " ".join(mock_run_sh.call_args[0][0])
    assert "add-iam-policy-binding" in cmd
    assert "roles/eventarc.eventReceiver" in cmd

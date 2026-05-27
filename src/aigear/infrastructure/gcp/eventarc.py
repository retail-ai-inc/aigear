from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


def pubsub_trigger_name(function_name: str) -> str:
    """Stable Eventarc trigger id for a Cloud Function Pub/Sub binding."""
    return f"{function_name}-pubsub"


class EventarcPubSubTrigger:
    """
    Eventarc Pub/Sub trigger for a Cloud Run (Gen2) function.

    See: https://cloud.google.com/run/docs/triggering/pubsub-triggers#gcloud
    """

    def __init__(
        self,
        trigger_name: str,
        location: str,
        project_id: str,
        function_name: str,
        function_region: str,
        topic_name: str,
        trigger_service_account: str,
    ):
        self.trigger_name = trigger_name
        self.location = location
        self.project_id = project_id
        self.function_name = function_name
        self.function_region = function_region
        self.topic_name = topic_name
        self.trigger_service_account = trigger_service_account

    @property
    def transport_topic(self) -> str:
        return f"projects/{self.project_id}/topics/{self.topic_name}"

    def describe(self) -> bool:
        command = [
            "gcloud",
            "eventarc",
            "triggers",
            "describe",
            self.trigger_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "ERROR" in event:
            if "NOT_FOUND" not in event:
                logger.error(
                    f"Unexpected error describing Eventarc trigger "
                    f"({self.trigger_name}): {event}"
                )
            return False
        return "name:" in event

    def create(self):
        command = [
            "gcloud",
            "eventarc",
            "triggers",
            "create",
            self.trigger_name,
            f"--location={self.location}",
            f"--destination-run-service={self.function_name}",
            f"--destination-run-region={self.function_region}",
            '--event-filters=type=google.cloud.pubsub.topic.v1.messagePublished',
            f"--transport-topic={self.transport_topic}",
            f"--service-account={self.trigger_service_account}",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def delete(self):
        command = [
            "gcloud",
            "eventarc",
            "triggers",
            "delete",
            self.trigger_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
            "--quiet",
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(
                f"Failed to delete Eventarc trigger ({self.trigger_name}): {event}"
            )
        else:
            logger.info(f"Eventarc trigger '{self.trigger_name}' deleted.")

    def _grant_event_receiver(self):
        command = [
            "gcloud",
            "projects",
            "add-iam-policy-binding",
            self.project_id,
            f"--member=serviceAccount:{self.trigger_service_account}",
            "--role=roles/eventarc.eventReceiver",
            "--condition=None",
        ]
        run_sh(command, check=True)
        logger.info("✅ Successfully granted: roles/eventarc.eventReceiver")

    def ensure(self):
        """Create Pub/Sub Eventarc trigger if missing; verify topic has a subscription."""
        from aigear.infrastructure.gcp.pub_sub import PubSub

        if not self.describe():
            logger.info(
                f"Creating Eventarc trigger ({self.trigger_name}) for topic "
                f"({self.topic_name})..."
            )
            self._grant_event_receiver()
            self.create()
        if not PubSub(self.topic_name, self.project_id).has_subscriptions():
            raise RuntimeError(
                f"Pub/Sub topic ({self.topic_name}) has no subscription after "
                f"Eventarc trigger ({self.trigger_name}) setup."
            )

    def delete_if_exists(self):
        if self.describe():
            self.delete()
        else:
            logger.info(
                f"Eventarc trigger ({self.trigger_name}) not found. Skipping."
            )

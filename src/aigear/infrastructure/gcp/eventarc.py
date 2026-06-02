import time

from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()

# Align with master --trigger-topic: long enough for VM insert + function return.
PUSH_ACK_DEADLINE_SEC = 300
PUSH_MIN_RETRY_DELAY_SEC = 60


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

    @staticmethod
    def _parse_gcloud_values(event: str) -> list[str]:
        text = event.strip()
        if not text:
            return []
        if "\t" in text:
            return [p.strip() for p in text.split("\t")]
        return [p.strip() for p in text.splitlines() if p.strip()]

    def _describe_transport(self) -> tuple[bool, str | None]:
        """Whether the trigger exists and its transport subscription path."""
        event = run_sh(
            [
                "gcloud",
                "eventarc",
                "triggers",
                "describe",
                self.trigger_name,
                f"--location={self.location}",
                f"--project={self.project_id}",
                "--format=value(name,transport.pubsub.subscription)",
            ]
        )
        if "ERROR" in event:
            if "NOT_FOUND" not in event:
                logger.error(
                    f"Unexpected error describing Eventarc trigger "
                    f"({self.trigger_name}): {event}"
                )
            return False, None
        parts = self._parse_gcloud_values(event)
        if not parts:
            return False, None
        transport_sub = (
            parts[1] if len(parts) > 1 and parts[1].startswith("projects/") else None
        )
        return True, transport_sub

    def describe(self) -> bool:
        return self._describe_transport()[0]

    def _subscription_candidates(self, pubsub, transport_sub: str | None) -> list[str]:
        """Subscriptions to inspect via pubsub subscriptions describe."""
        candidates: list[str] = []
        if transport_sub:
            candidates.append(transport_sub)
        candidates.extend(pubsub.list_subscriptions())
        prefix = f"eventarc-{self.location}-{self.trigger_name}-sub-"
        event = run_sh(
            [
                "gcloud",
                "pubsub",
                "subscriptions",
                "list",
                f"--project={self.project_id}",
                f"--filter=name:{prefix}",
                "--uri",
            ]
        )
        for line in event.splitlines():
            sub = line.strip()
            if sub.startswith("projects/") and sub not in candidates:
                candidates.append(sub)
        return candidates

    def _is_ready(self, pubsub, transport_sub: str | None) -> bool:
        return (
            pubsub.find_healthy_subscription(
                self._subscription_candidates(pubsub, transport_sub)
            )
            is not None
        )

    def _tune_push_subscription(self, pubsub, transport_sub: str | None):
        healthy = pubsub.find_healthy_subscription(
            self._subscription_candidates(pubsub, transport_sub)
        )
        if healthy:
            pubsub.tune_push_subscription(
                healthy,
                ack_deadline_sec=PUSH_ACK_DEADLINE_SEC,
                min_retry_delay_sec=PUSH_MIN_RETRY_DELAY_SEC,
            )

    def _delete_orphan_subscriptions(self, pubsub, transport_sub: str | None):
        candidates = self._subscription_candidates(pubsub, transport_sub)
        for sub in pubsub.find_orphan_subscriptions(candidates):
            bound = pubsub.describe_subscription(sub)
            logger.warning(
                f"Deleting orphan subscription ({sub.rsplit('/', 1)[-1]}): "
                f"bound to {bound or 'deleted/unknown topic'}, "
                f"expected {pubsub.topic_path}"
            )
            pubsub.delete_subscription(sub)
        if transport_sub and pubsub.subscription_status(transport_sub) == "missing":
            logger.info(
                f"Trigger subscription ({transport_sub.rsplit('/', 1)[-1]}) "
                f"does not exist."
            )

    def _wait_for_ready(
        self,
        pubsub,
        transport_sub: str | None,
        retries: int = 20,
        interval: float = 1.0,
    ) -> bool:
        for i in range(retries + 1):
            _, current_sub = self._describe_transport()
            check_sub = current_sub or transport_sub
            if self._is_ready(pubsub, check_sub):
                healthy = pubsub.find_healthy_subscription(
                    self._subscription_candidates(pubsub, check_sub)
                )
                logger.info(
                    f"Eventarc subscription ready: {healthy.rsplit('/', 1)[-1]}"
                )
                return True
            if i == retries:
                break
            if i == 0 or (i + 1) % 5 == 0:
                logger.info(
                    f"Waiting for healthy subscription on topic ({self.topic_name}) "
                    f"... ({i + 1}/{retries})"
                )
            time.sleep(interval)
        return False

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
            "--event-filters=type=google.cloud.pubsub.topic.v1.messagePublished",
            f"--transport-topic={self.transport_topic}",
            f"--service-account={self.trigger_service_account}",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def delete(self) -> bool:
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
            return False
        logger.info(f"Eventarc trigger '{self.trigger_name}' deleted.")
        return True

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

        pubsub = PubSub(self.topic_name, self.project_id)
        trigger_exists, transport_sub = self._describe_transport()

        if trigger_exists and self._is_ready(pubsub, transport_sub):
            healthy = pubsub.find_healthy_subscription(
                self._subscription_candidates(pubsub, transport_sub)
            )
            self._tune_push_subscription(pubsub, transport_sub)
            logger.info(
                f"Eventarc trigger ({self.trigger_name}) already ready "
                f"(subscription {healthy.rsplit('/', 1)[-1]} on {self.topic_name})."
            )
            return

        recreated = False
        if trigger_exists:
            logger.warning(
                f"Orphaned Eventarc trigger ({self.trigger_name}): no healthy "
                f"subscription on topic ({self.topic_name}). Cleaning up..."
            )
            self._delete_orphan_subscriptions(pubsub, transport_sub)
            if not self.delete():
                raise RuntimeError(
                    f"Failed to delete orphaned Eventarc trigger ({self.trigger_name})."
                )
            recreated = True
            trigger_exists = False

        if not trigger_exists:
            if not recreated:
                self._delete_orphan_subscriptions(pubsub, None)
            logger.info(
                f"Creating Eventarc trigger ({self.trigger_name}) for topic "
                f"({self.topic_name})..."
            )
            if not recreated:
                self._grant_event_receiver()
            self.create()
            if not self._wait_for_ready(pubsub, None):
                raise RuntimeError(
                    f"No healthy Pub/Sub subscription on topic ({self.topic_name}) "
                    f"after Eventarc trigger ({self.trigger_name}) setup."
                )
            self._tune_push_subscription(pubsub, None)

    def delete_if_exists(self):
        if self.describe():
            self.delete()
        else:
            logger.info(f"Eventarc trigger ({self.trigger_name}) not found. Skipping.")

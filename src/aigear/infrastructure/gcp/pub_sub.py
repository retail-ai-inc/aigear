from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class PubSub:
    def __init__(
        self,
        topic_name: str,
        project_id: str,
    ):
        self.topic_name = topic_name
        self.project_id = project_id

    @property
    def topic_path(self) -> str:
        return f"projects/{self.project_id}/topics/{self.topic_name}"

    def describe_subscription(self, subscription: str) -> str | None:
        """
        Describe a subscription (gcloud pubsub subscriptions describe).

        Returns the topic the subscription is bound to, or None if the
        subscription does not exist (NOT_FOUND).
        """
        sub_id = subscription.rsplit("/", 1)[-1]
        event = run_sh(
            [
                "gcloud",
                "pubsub",
                "subscriptions",
                "describe",
                sub_id,
                f"--project={self.project_id}",
                "--format=value(topic)",
            ]
        )
        if "ERROR" in event:
            if "NOT_FOUND" in event:
                return None
            logger.error(
                f"Unexpected error describing subscription ({sub_id}): {event}"
            )
            return None
        topic = event.strip()
        return topic if topic.startswith("projects/") else None

    def subscription_status(self, subscription: str) -> str:
        """
        Classify a subscription relative to this topic.

        Returns:
            healthy  - subscription exists and is bound to this topic
            orphan   - subscription exists but is bound to another/deleted topic
            missing  - subscription does not exist
        """
        topic = self.describe_subscription(subscription)
        if topic is None:
            return "missing"
        if topic == self.topic_path:
            return "healthy"
        return "orphan"

    def delete_subscription(self, subscription: str):
        sub_id = subscription.rsplit("/", 1)[-1]
        event = run_sh(
            [
                "gcloud",
                "pubsub",
                "subscriptions",
                "delete",
                sub_id,
                f"--project={self.project_id}",
                "--quiet",
            ]
        )
        if "ERROR" in event:
            logger.warning(
                f"Could not delete Pub/Sub subscription ({sub_id}): {event.strip()}"
            )
        else:
            logger.info(f"Deleted Pub/Sub subscription ({sub_id}).")

    def find_healthy_subscription(self, candidates: list[str]) -> str | None:
        """Return the first candidate subscription that is healthy on this topic."""
        for sub in candidates:
            if sub and self.subscription_status(sub) == "healthy":
                return sub
        return None

    def find_orphan_subscriptions(self, candidates: list[str]) -> list[str]:
        """Return subscriptions that exist but are not bound to this topic."""
        return [
            sub
            for sub in candidates
            if sub and self.subscription_status(sub) == "orphan"
        ]

    def tune_push_subscription(
        self,
        subscription: str,
        *,
        ack_deadline_sec: int = 300,
        min_retry_delay_sec: int = 60,
    ):
        """
        Extend push ack deadline and backoff (reduces Pub/Sub redelivery).

        Eventarc defaults to a short ack deadline (~10s); VM insert often needs longer.
        """
        sub_id = subscription.rsplit("/", 1)[-1]
        run_sh(
            [
                "gcloud",
                "pubsub",
                "subscriptions",
                "update",
                sub_id,
                f"--project={self.project_id}",
                f"--ack-deadline={ack_deadline_sec}",
                f"--min-retry-delay={min_retry_delay_sec}s",
            ],
            check=True,
        )
        logger.info(
            f"Pub/Sub subscription ({sub_id}): ack-deadline={ack_deadline_sec}s, "
            f"min-retry-delay={min_retry_delay_sec}s"
        )

    def create(self):
        command = [
            "gcloud",
            "pubsub",
            "topics",
            "create",
            self.topic_name,
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def add_permissions_to_pubsub(self, sa_email):
        topic = f"projects/{self.project_id}/topics/{self.topic_name}"
        for role in ["roles/pubsub.publisher", "roles/pubsub.subscriber"]:
            command = [
                "gcloud",
                "pubsub",
                "topics",
                "add-iam-policy-binding",
                topic,
                f"--member=serviceAccount:{sa_email}",
                f"--role={role}",
                f"--project={self.project_id}",
            ]
            run_sh(command, check=True)
            logger.info(f"✅ Successfully granted: {role}")

    def describe(self):
        is_exist = False
        command = [
            "gcloud",
            "pubsub",
            "topics",
            "describe",
            self.topic_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "name: projects" in event:
            is_exist = True
        elif "ERROR" in event and "NOT_FOUND" not in event:
            logger.error(
                f"Unexpected error describing topic ({self.topic_name}): {event}"
            )
        return is_exist

    def list_subscriptions(self) -> list[str]:
        event = run_sh(
            [
                "gcloud",
                "pubsub",
                "topics",
                "list-subscriptions",
                self.topic_name,
                f"--project={self.project_id}",
                "--uri",
            ]
        )
        subs: list[str] = []
        for line in event.splitlines():
            stripped = line.strip()
            if stripped.startswith("projects/"):
                subs.append(stripped)
            elif stripped.startswith("name:"):
                path = stripped.split(":", 1)[1].strip()
                if path.startswith("projects/"):
                    subs.append(path)
        return subs

    def has_healthy_subscription(self, extra_candidates: list[str] | None = None) -> bool:
        candidates = list(extra_candidates or []) + self.list_subscriptions()
        return self.find_healthy_subscription(candidates) is not None

    def _delete_subscriptions(self):
        for sub in self.list_subscriptions():
            self.delete_subscription(sub)

    def delete(self):
        self._delete_subscriptions()
        command = [
            "gcloud",
            "pubsub",
            "topics",
            "delete",
            self.topic_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def list(self):
        command = [
            "gcloud",
            "pubsub",
            "topics",
            "list",
            f"--filter=name.scope(topic):{self.topic_name}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def publish(self, message):
        command = [
            "gcloud",
            "pubsub",
            "topics",
            "publish",
            self.topic_name,
            f"--message={message}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

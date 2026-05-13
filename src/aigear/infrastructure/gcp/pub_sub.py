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

    def _delete_subscriptions(self):
        event = run_sh(
            [
                "gcloud",
                "pubsub",
                "topics",
                "list-subscriptions",
                self.topic_name,
                f"--project={self.project_id}",
            ]
        )
        subscriptions = [line.strip() for line in event.splitlines() if line.strip().startswith("projects/")]
        for sub in subscriptions:
            result = run_sh(
                ["gcloud", "pubsub", "subscriptions", "delete", sub, f"--project={self.project_id}"]
            )
            if "ERROR" in result:
                logger.error(f"Failed to delete subscription '{sub}': {result}")
            else:
                logger.info(f"Subscription '{sub}' deleted.")

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

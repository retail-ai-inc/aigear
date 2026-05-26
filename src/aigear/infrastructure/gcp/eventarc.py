import re

from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()

_PUBSUB_EVENT = "type=google.cloud.pubsub.topic.v1.messagePublished"


def trigger_name_for_function(function_name: str) -> str:
    """Derive a stable Eventarc trigger ID from the Cloud Function name."""
    base = re.sub(r"[^a-z0-9-]", "-", function_name.lower())
    base = re.sub(r"-+", "-", base).strip("-")
    if not base or not base[0].isalpha():
        base = f"f-{base}" if base else "f"
    suffix = "-pubsub"
    max_base = 63 - len(suffix)
    return f"{base[:max_base]}{suffix}"


class EventarcTrigger:
    def __init__(
        self,
        trigger_name: str,
        function_name: str,
        region: str,
        topic_name: str,
        project_id: str,
        trigger_service_account: str,
    ):
        self.trigger_name = trigger_name
        self.function_name = function_name
        self.region = region
        self.topic_name = topic_name
        self.project_id = project_id
        self.trigger_service_account = trigger_service_account
        self.transport_topic = f"projects/{project_id}/topics/{topic_name}"

    @classmethod
    def from_function(
        cls,
        function_name: str,
        region: str,
        topic_name: str,
        project_id: str,
        trigger_service_account: str,
    ) -> "EventarcTrigger":
        return cls(
            trigger_name=trigger_name_for_function(function_name),
            function_name=function_name,
            region=region,
            topic_name=topic_name,
            project_id=project_id,
            trigger_service_account=trigger_service_account,
        )

    def describe(self) -> bool:
        command = [
            "gcloud",
            "eventarc",
            "triggers",
            "describe",
            self.trigger_name,
            f"--location={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if f"projects/{self.project_id}/locations/{self.region}/triggers/{self.trigger_name}" in event:
            return True
        if "name: projects" in event and self.trigger_name in event:
            return True
        if "ERROR" in event and "NOT_FOUND" not in event:
            logger.error(
                f"Unexpected error describing Eventarc trigger ({self.trigger_name}): {event}"
            )
        return False

    def create(self):
        command = [
            "gcloud",
            "eventarc",
            "triggers",
            "create",
            self.trigger_name,
            f"--location={self.region}",
            f"--destination-run-service={self.function_name}",
            f"--destination-run-region={self.region}",
            f"--event-filters={_PUBSUB_EVENT}",
            f"--transport-topic={self.transport_topic}",
            f"--service-account={self.trigger_service_account}",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def add_permissions(self, sa_email: str):
        command = [
            "gcloud",
            "projects",
            "add-iam-policy-binding",
            self.project_id,
            f"--member=serviceAccount:{sa_email}",
            "--role=roles/eventarc.eventReceiver",
        ]
        run_sh(command, check=True)
        logger.info(f"✅ eventarc.eventReceiver granted for {sa_email}")

    def delete(self):
        command = [
            "gcloud",
            "eventarc",
            "triggers",
            "delete",
            self.trigger_name,
            f"--location={self.region}",
            f"--project={self.project_id}",
            "--quiet",
        ]
        event = run_sh(command)
        if "ERROR" in event and "NOT_FOUND" not in event:
            logger.error(
                f"Failed to delete Eventarc trigger ({self.trigger_name}): {event}"
            )
        else:
            logger.info(f"Eventarc trigger '{self.trigger_name}' deleted.")

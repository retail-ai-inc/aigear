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
            "gcloud", "pubsub", "topics", "create",
            self.topic_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "pubsub", "topics", "describe",
            self.topic_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "name: projects" in event:
            is_exist = True
            logger.info(f"Find resources: {event}")
        elif "NOT_FOUND" in event:
            logger.info(f"NOT_FOUND: Resource not found (resource={self.topic_name})")
        else:
            logger.info(event)
        return is_exist

    def delete(self):
        command = [
            "gcloud", "pubsub", "topics", "delete",
            self.topic_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def list(self):
        command = [
            "gcloud", "pubsub", "topics", "list",
            f"--filter=name.scope(topic):{self.topic_name}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def publish(self, message):
        command = [
            "gcloud", "pubsub", "topics", "publish", self.topic_name,
            f"--message={message}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)


if __name__ == "__main__":
    project_id = ""
    topic_name = "ml-test-pubsub"
    pubsub = PubSub(
        topic_name=topic_name,
        project_id=project_id,
    )
    topic_exist = pubsub.describe()
    print("topic_name: ", topic_exist)
    if not topic_exist:
        pubsub.create()

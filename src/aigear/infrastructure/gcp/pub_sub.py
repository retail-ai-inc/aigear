from aigear import aigear_logger
from aigear.common import run_sh


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
        aigear_logger.info(event)

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
            aigear_logger.info(f"Find resources: {event}")
        elif "NOT_FOUND" in event:
            aigear_logger.info(f"NOT_FOUND: Resource not found (resource={self.topic_name})")
        else:
            aigear_logger.info(event)
        return is_exist

    def delete(self):
        command = [
            "gcloud", "pubsub", "topics", "delete",
            self.topic_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)

    def list(self):
        command = [
            "gcloud", "pubsub", "topics", "list",
            f"--filter=name.scope(topic):{self.topic_name}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)

    def publish(self, message):
        command = [
            "gcloud", "pubsub", "topics", "publish", self.topic_name,
            f"--message={message}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)


class Subscriptions:
    def __init__(
        self,
        sub_name: str,
        topic_name: str,
        project_id: str,
    ):
        self.sub_name = sub_name
        self.topic_name = topic_name
        self.project_id = project_id

    def create(self):
        command = [
            "gcloud", "pubsub", "subscriptions", "create", self.sub_name,
            f"--topic={self.topic_name}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "pubsub", "subscriptions", "describe", self.sub_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "name: projects" in event:
            is_exist = True
            aigear_logger.info(f"Find resources: {event}")
        elif "NOT_FOUND" in event:
            aigear_logger.info(f"NOT_FOUND: Resource not found (resource={self.sub_name})")
        else:
            aigear_logger.info(event)
        return is_exist

    def delete(self):
        command = [
            "gcloud", "pubsub", "subscriptions", "delete", self.sub_name,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)

    def list(self):
        command = [
            "gcloud", "pubsub", "subscriptions", "list",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)

    def pull(self):
        command = [
            "gcloud", "pubsub", "subscriptions", "pull", self.sub_name,
            "--format=json(ackId,message.attributes,message.data.decode(\"base64\").decode(\"utf-8\"),"
            "message.messageId,message.publishTime)",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(event)

if __name__=="__main__":
    project_id = "ssc-ape-staging"
    topic_name = "ml-test-pubsub"
    sub_name = f"{topic_name}-subscriptions"
    pubsub = PubSub(
        topic_name=topic_name,
        project_id=project_id,
    )
    topic_exist = pubsub.describe()
    print("topic_name: ", topic_exist)
    if not topic_exist:
        pubsub.create()

    subscriptions = Subscriptions(
        sub_name=sub_name,
        topic_name=topic_name,
        project_id=project_id
    )
    sub_exist = subscriptions.describe()
    print("subscriptions: ", sub_exist)
    if not sub_exist:
        subscriptions.create()

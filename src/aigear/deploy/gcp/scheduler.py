import json
from aigear import aigear_logger
from aigear.common import run_sh


class Scheduler:
    def __init__(
        self,
        name: str,
        location: str,
        schedule: str,
        topic_name: str,
        message: any,
        time_zone: str = "Etc/UTC",
    ):
        self.name = name
        self.location = location
        self.schedule = schedule
        self.topic_name = topic_name
        self.message = message
        self.time_zone = time_zone

    def create(self):
        message_body = json.dumps(self.message)
        command = [
            "gcloud", "scheduler", "jobs", "create", "pubsub",
            self.name,
            "--location", self.location,
            "--schedule", self.schedule,
            "--topic", self.topic_name,
            "--message-body", message_body,
            "--time-zone", self.time_zone,
        ]
        event = run_sh(command)
        aigear_logger.info(event)
        if "ERROR" in event:
            aigear_logger.info("Error occurred while creating cloud function.")

    def delete(self):
        command = [
            "gcloud", "scheduler", "jobs", "delete",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command, "yes\n")
        aigear_logger.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "scheduler", "jobs", "describe",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        aigear_logger.info(event)
        if "ENABLED" in event:
            is_exist = True
        return is_exist

    def list(self):
        command = [
            "gcloud", "scheduler", "jobs", "list",
            "--location", self.location,
            f"--filter={self.name}",
        ]
        event = run_sh(command)
        aigear_logger.info(f"\n{event}")

    def run(self):
        command = [
            "gcloud", "scheduler", "jobs", "run",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        if event:
            aigear_logger.info(event)
        else:
            aigear_logger.info("Running successfully, executing job.")

    def pause(self):
        command = [
            "gcloud", "scheduler", "jobs", "pause",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        aigear_logger.info(event)

    def resume(self):
        command = [
            "gcloud", "scheduler", "jobs", "resume",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        aigear_logger.info(event)

    @staticmethod
    def update(
        name,
        location,
        schedule,
        topic_name,
        message,
    ):
        message_body = json.dumps(message)
        command = [
            "gcloud", "scheduler", "jobs", "update", "pubsub",
            name,
            "--location", location,
            "--schedule", schedule,
            "--topic", topic_name,
            "--message-body", message_body,
        ]
        event = run_sh(command)
        aigear_logger.info(event)
        if "ERROR" in event:
            aigear_logger.info("Error occurred while creating cloud function.")


if __name__ == "__main__":
    message = [
        {
            "vm_name": "coopiwate-ape3-fetch-data-vm",
            "disk_size_gb": "20",
            "docker_image": "asia-northeast1-docker.pkg.dev/ssc-ape-staging/medovik/ape3",
            "spec": "e2-standard-2",
            "on_host_maintenance": "MIGRATE",
            "pipeline_version": "coopiwate_ape3",
            "pipeline_step": "ape3.coopiwate.data.fetch_data"
        },
        {
            "vm_name": "coopiwate-ape3-preprocessing-vm",
            "disk_size_gb": "40",
            "docker_image": "asia-northeast1-docker.pkg.dev/ssc-ape-staging/medovik/ape3",
            "spec": "e2-highmem-8",
            "on_host_maintenance": "MIGRATE",
            "pipeline_version": "coopiwate_ape3",
            "pipeline_step": "ape3.coopiwate.feature.preprocessing"
        }
    ]
    scheduler = Scheduler(
        name="ml_test",
        location="asia-northeast1",
        schedule="45 21 * * 0",
        topic_name="medovik-pipelines-pubsub",
        message=message,
        time_zone="Asia/Tokyo",
    )
    is_exist = scheduler.describe()
    print(is_exist)
    if not is_exist:
        scheduler.create()

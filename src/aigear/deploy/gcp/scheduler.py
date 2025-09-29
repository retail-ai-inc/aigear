import json
from aigear.common import run_sh
from aigear.common import create_stage_logger, PipelineStage


# Use deployment stage logger
deployment_logger = create_stage_logger(
    stage=PipelineStage.DEPLOYMENT,
    module_name=__name__,
    cpu_count=2,
    memory_limit="2GB",
    enable_cloud_logging=True
)

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
        with deployment_logger.stage_context() as logger:
            logger.info(f"Creating scheduler job: {self.name}")
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
            logger.info(f"Scheduler creation result: {event}")
            if "ERROR" in event:
                logger.error("Error occurred while creating scheduler job.")

    def delete(self):
        with deployment_logger.stage_context() as logger:
            logger.warning(f"Deleting scheduler job: {self.name}")
            command = [
                "gcloud", "scheduler", "jobs", "delete",
                self.name,
                "--location", self.location,
            ]
            event = run_sh(command, "yes\n")
            logger.info(f"Scheduler deletion result: {event}")

    def describe(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Checking scheduler job: {self.name}")
            is_exist = False
            command = [
                "gcloud", "scheduler", "jobs", "describe",
                self.name,
                "--location", self.location,
            ]
            event = run_sh(command)
            logger.info(f"Scheduler describe result: {event}")
            if "ENABLED" in event:
                is_exist = True
                logger.info(f"Scheduler job {self.name} is enabled")
            else:
                logger.warning(f"Scheduler job {self.name} is not enabled or doesn't exist")
            return is_exist

    def list(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Listing scheduler jobs for: {self.name}")
            command = [
                "gcloud", "scheduler", "jobs", "list",
                "--location", self.location,
                f"--filter={self.name}",
            ]
            event = run_sh(command)
            logger.info(f"Scheduler list result:\n{event}")

    def run(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Running scheduler job: {self.name}")
            command = [
                "gcloud", "scheduler", "jobs", "run",
                self.name,
                "--location", self.location,
            ]
            event = run_sh(command)
            if event:
                logger.info(f"Scheduler run result: {event}")
            else:
                logger.info("Running successfully, executing job.")

    def pause(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Pausing scheduler job: {self.name}")
            command = [
                "gcloud", "scheduler", "jobs", "pause",
                self.name,
                "--location", self.location,
            ]
            event = run_sh(command)
            logger.info(f"Scheduler pause result: {event}")

    def resume(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Resuming scheduler job: {self.name}")
            command = [
                "gcloud", "scheduler", "jobs", "resume",
                self.name,
                "--location", self.location,
            ]
            event = run_sh(command)
            logger.info(f"Scheduler resume result: {event}")

    @staticmethod
    def update(
        name,
        location,
        schedule,
        topic_name,
        message,
    ):
        # Static methods require the creation of a separate logger instance
        with deployment_logger.stage_context() as logger:
            logger.info(f"Updating scheduler job: {name}")
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
            logger.info(f"Scheduler update result: {event}")
            if "ERROR" in event:
                logger.error("Error occurred while updating scheduler job.")


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
    # if not is_exist:
    #     scheduler.create()

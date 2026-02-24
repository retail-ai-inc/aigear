import json
from aigear.common import run_sh
from aigear.common.logger import Logging
from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.deploy.gcp.artifacts_image import get_artifacts_image


logger = Logging(log_name=__name__).console_logging()

class Scheduler:
    def __init__(
        self,
        name: str,
        location: str,
        schedule: str,
        topic_name: str,
        message: list[dict[str, str]],
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
        logger.info(event)
        if "ERROR" in event:
            logger.info("Error occurred while creating cloud function.")

    def delete(self):
        command = [
            "gcloud", "scheduler", "jobs", "delete",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command, "yes\n")
        logger.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "scheduler", "jobs", "describe",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        logger.info(event)
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
        logger.info(f"\n{event}")

    def run(self):
        command = [
            "gcloud", "scheduler", "jobs", "run",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        if event:
            logger.info(event)
        else:
            logger.info("Running successfully, executing job.")

    def pause(self):
        command = [
            "gcloud", "scheduler", "jobs", "pause",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        logger.info(event)

    def resume(self):
        command = [
            "gcloud", "scheduler", "jobs", "resume",
            self.name,
            "--location", self.location,
        ]
        event = run_sh(command)
        logger.info(event)

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
        logger.info(event)
        if "ERROR" in event:
            logger.info("Error occurred while creating cloud function.")

def create_scheduler(pipeline_version, step_names):
    aigear_config = AigearConfig.get_config()
    pipelines_config = PipelinesConfig.get_config()
    pipeline_config = pipelines_config.get(pipeline_version, {})

    scheduler_messages = []
    for step_name in step_names:
        step_config = pipeline_config.get(step_name, {})
        resources = step_config.get("resources", {})
        if "docker_image" not in resources:
            artifacts_image = get_artifacts_image(aigear_config)
            resources["docker_image"] = artifacts_image
        task_run_parameters = step_config.get("task_run_parameters", {})
        message = {**resources, **task_run_parameters}
        scheduler_messages.append(message)

    scheduler_config = pipeline_config.get("scheduler", {})
    scheduler_name = scheduler_config.get("name")
    scheduler_location = scheduler_config.get("location")
    scheduler_schedule = scheduler_config.get("schedule")
    scheduler_time_zone = scheduler_config.get("time_zone")
    scheduler = Scheduler(
        name=scheduler_name,
        location=scheduler_location,
        schedule=scheduler_schedule,
        topic_name=aigear_config.gcp.pub_sub.topic_name,
        message=scheduler_messages,
        time_zone=scheduler_time_zone,
    )
    is_exist = scheduler.describe()
    if not is_exist:
        scheduler.create()

if __name__ == "__main__":
    message_json = [
        {
            "vm_name": "",
            "disk_size_gb": "20",
            "spec": "e2-standard-2",
            "on_host_maintenance": "MIGRATE",
            "pipeline_version": "",
            "pipeline_step": "xxx.xxx.xxx"
        },
        {
            "vm_name": "",
            "disk_size_gb": "40",
            "spec": "e2-highmem-8",
            "on_host_maintenance": "MIGRATE",
            "pipeline_version": "",
            "pipeline_step": "xxx.xxx.xxx"
        }
    ]
    scheduler = Scheduler(
        name="ml_test",
        location="",
        schedule="45 21 * * 0",
        topic_name="pipelines-pubsub",
        message=message_json,
        time_zone="Asia/Tokyo",
    )
    exist = scheduler.describe()
    print(exist)
    # if not is_exist:
    #     scheduler.create()

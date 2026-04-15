import json

from aigear.common import run_sh
from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.common.constant import ENV_STAGING
from aigear.common.image import get_image_path
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()



class Scheduler:
    def __init__(
        self,
        name: str,
        location: str,
        project_id: str,
        schedule: str,
        topic_name: str,
        message: any,
        time_zone: str = "Etc/UTC",
    ):
        self.name = name
        self.location = location
        self.project_id = project_id
        self.schedule = schedule
        self.topic_name = topic_name
        self.message = message
        self.time_zone = time_zone

    def create(self):
        message_body = json.dumps(self.message)
        command = [
            "gcloud", "scheduler", "jobs", "create", "pubsub",
            self.name,
            "--schedule", self.schedule,
            "--topic", self.topic_name,
            "--message-body", message_body,
            "--time-zone", self.time_zone,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(f"Failed to create scheduler job '{self.name}': {event}")
        else:
            logger.info(f"Scheduler job '{self.name}' created successfully. (schedule: {self.schedule}, timezone: {self.time_zone})")

    def delete(self):
        command = [
            "gcloud", "scheduler", "jobs", "delete",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command, "yes\n")
        if "ERROR" in event:
            logger.error(f"Failed to delete scheduler job '{self.name}': {event}")
        else:
            logger.info(f"Scheduler job '{self.name}' deleted.")

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "scheduler", "jobs", "describe",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        if "ENABLED" in event:
            is_exist = True
            schedule = next((line.split(": ", 1)[1] for line in event.splitlines() if line.startswith("schedule:")), "?")
            timezone = next((line.split(": ", 1)[1] for line in event.splitlines() if line.startswith("timeZone:")), "?")
            logger.info(f"Scheduler job '{self.name}' exists. (schedule: {schedule}, timezone: {timezone})")
        elif "NOT_FOUND" in event:
            logger.info(f"Scheduler job '{self.name}' not found.")
        else:
            logger.error(f"Unexpected response describing scheduler job '{self.name}': {event}")
        return is_exist

    def list(self):
        command = [
            "gcloud", "scheduler", "jobs", "list",
            "--location", self.location,
            f"--filter={self.name}",
            "--project", self.project_id,
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")

    def run(self):
        command = [
            "gcloud", "scheduler", "jobs", "run",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        if event and "ERROR" in event:
            logger.error(f"Failed to run scheduler job '{self.name}': {event}")
        else:
            logger.info(f"Scheduler job '{self.name}' triggered successfully.")

    def pause(self):
        command = [
            "gcloud", "scheduler", "jobs", "pause",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(f"Failed to pause scheduler job '{self.name}': {event}")
        else:
            logger.info(f"Scheduler job '{self.name}' paused.")

    def resume(self):
        command = [
            "gcloud", "scheduler", "jobs", "resume",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(f"Failed to resume scheduler job '{self.name}': {event}")
        else:
            logger.info(f"Scheduler job '{self.name}' resumed.")

    @staticmethod
    def update(
        name,
        project_id,
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
            "--project", project_id,
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(f"Failed to update scheduler job '{name}': {event}")
        else:
            logger.info(f"Scheduler job '{name}' updated successfully. (schedule: {schedule})")


def _build_step_message(
    step_config: dict,
    pipeline_version: str,
    docker_image: str,
    gke_cluster: str,
    gke_zone: str,
    venv: str | None = None,
    env: str | None = None,
    step_name: str | None = None,
) -> dict:
    """
    Build a single task message for the Pub/Sub payload.

    Field priority:
      - Base: resources block (vm_name, spec, gpu, disk_size_gb, ...)
      - Always added: docker_image, pipeline_version
      - Conditionally added: step_name (workflow steps only, used by aigear-task workflow --step)
      - Conditionally added: model_class_path (if present in step_config)
      - Conditionally added: gke_cluster, gke_zone, env (only when model_class_path is present)
      - Conditionally added: venv (per-step virtual environment)
    """
    message = dict(step_config.get("resources", {}))

    message["docker_image"]      = docker_image
    message["pipeline_version"]  = pipeline_version

    # step_name is used by the VM to run: aigear-task workflow --step <step_name>
    # Only set for workflow steps (not model_service)
    if step_name:
        message["step_name"] = step_name

    # model_class_path is only present for model_service steps
    if "model_class_path" in step_config:
        message["model_class_path"] = step_config["model_class_path"]

    # GKE fields and env are only needed for steps that perform a model deploy
    if "model_class_path" in step_config:
        message["gke_cluster"] = gke_cluster
        message["gke_zone"]    = gke_zone
        if env:
            message["env"] = env

    if venv:
        message["venv"] = venv

    return message


def create_scheduler(
    pipeline_version: str, 
    step_names: list[str], 
    env: str = ENV_STAGING
):
    aigear_config   = AigearConfig.get_config()
    pipeline_config = PipelinesConfig.get_version_config(pipeline_version)


    pl_image = get_image_path(is_service=False)
    ms_image = get_image_path(is_service=True)
    # GKE info comes from the global aigear config
    kubernetes_config = aigear_config.gcp.kubernetes
    gke_cluster = kubernetes_config.cluster_name
    gke_zone    = aigear_config.gcp.location

    scheduler_config  = pipeline_config.get("scheduler", {})
    venv_pl = pipeline_config.get("venv_pl")
    venv_ms = pipeline_config.get("model_service", {}).get("venv_ms")

    scheduler_messages = []
    for step_name in step_names:
        step_config = pipeline_config.get(step_name, {})

        # model_service uses ms image, all other steps use pl image
        is_model_service  = step_name == "model_service"
        step_docker_image = ms_image if is_model_service else pl_image
        step_venv         = venv_ms if is_model_service else venv_pl
        env = env if is_model_service else None
        step_name= None if is_model_service else step_name

        message = _build_step_message(
            step_config      = step_config,
            pipeline_version = pipeline_version,
            docker_image     = step_docker_image,
            gke_cluster      = gke_cluster,
            gke_zone         = gke_zone,
            venv             = step_venv,
            env              = env,
            step_name        = step_name,
        )
        scheduler_messages.append(message)
    scheduler = Scheduler(
        name       = scheduler_config.get("name"),
        location   = aigear_config.gcp.location,
        project_id = aigear_config.gcp.gcp_project_id,
        schedule   = scheduler_config.get("schedule"),
        topic_name = aigear_config.gcp.pub_sub.topic_name,
        message    = scheduler_messages,
        time_zone  = scheduler_config.get("time_zone", "Etc/UTC"),
    )

    is_exist = scheduler.describe()
    if not is_exist:
        scheduler.create()

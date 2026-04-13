import json

from aigear.common import run_sh
from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.common.image import get_image_path
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()

# Step-level fields that are lifted directly from step_config into the message.
# Any field listed here will be included in the message if present in the step config.
_STEP_MESSAGE_FIELDS = (
    "pipeline_step",
    "model_class_path",
)


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
        logger.info(event)
        if "ERROR" in event:
            logger.info("Error occurred while creating cloud function.")

    def delete(self):
        command = [
            "gcloud", "scheduler", "jobs", "delete",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command, "yes\n")
        logger.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "scheduler", "jobs", "describe",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
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
        if event:
            logger.info(event)
        else:
            logger.info("Running successfully, executing job.")

    def pause(self):
        command = [
            "gcloud", "scheduler", "jobs", "pause",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        logger.info(event)

    def resume(self):
        command = [
            "gcloud", "scheduler", "jobs", "resume",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        logger.info(event)

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
        logger.info(event)
        if "ERROR" in event:
            logger.info("Error occurred while creating cloud function.")


def _build_step_message(
    step_config: dict,
    pipeline_version: str,
    docker_image: str,
    gke_cluster: str,
    gke_zone: str,
    venv: str | None = None,
) -> dict:
    """
    Build a single task message for the Pub/Sub payload.

    Field priority:
      - Base: resources block (vm_name, spec, gpu, disk_size_gb, ...)
      - Always added: docker_image, pipeline_version
      - Conditionally added: pipeline_step, model_class_path (if present in step_config)
      - Conditionally added: gke_cluster, gke_zone (only when model_class_path is present)
      - Conditionally added: venv (from scheduler config, applies to all steps in the pipeline)
    """
    message = dict(step_config.get("resources", {}))

    message["docker_image"]      = docker_image
    message["pipeline_version"]  = pipeline_version

    # Lift pipeline_step / model_class_path directly from step config
    for field in _STEP_MESSAGE_FIELDS:
        if field in step_config:
            message[field] = step_config[field]

    # GKE fields are only needed for steps that perform a model deploy
    if "model_class_path" in step_config:
        message["gke_cluster"] = gke_cluster
        message["gke_zone"]    = gke_zone

    # venv is a scheduler-level setting that applies to all steps in the pipeline
    if venv:
        message["venv"] = venv

    return message


def create_scheduler(pipeline_version: str, step_names: list[str]):
    aigear_config   = AigearConfig.get_config()
    pipeline_config = PipelinesConfig.get_version_config(pipeline_version)


    pl_image = get_image_path(is_service=False)
    ms_image = get_image_path(is_service=True)
    # GKE info comes from the global aigear config
    kubernetes_config = aigear_config.gcp.kubernetes
    gke_cluster = kubernetes_config.cluster_name
    gke_zone    = aigear_config.gcp.location

    scheduler_config  = pipeline_config.get("scheduler", {})
    venv_pl = pipeline_config.get("venv_pl")   # venv for pipeline training steps

    scheduler_messages = []
    for step_name in step_names:
        step_config = pipeline_config.get(step_name, {})

        # model_service uses ms image, all other steps use pl image
        is_model_service  = step_name == "model_service"
        step_docker_image = ms_image if is_model_service else pl_image
        step_venv         = None if is_model_service else venv_pl

        message = _build_step_message(
            step_config      = step_config,
            pipeline_version = pipeline_version,
            docker_image     = step_docker_image,
            gke_cluster      = gke_cluster,
            gke_zone         = gke_zone,
            venv             = step_venv,
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

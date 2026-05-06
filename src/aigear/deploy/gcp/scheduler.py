import json

from aigear.common import run_sh
from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.common.constant import ENV_STAGING
from aigear.common.image import get_image_path
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()



class Scheduler:
    """Wraps gcloud scheduler jobs commands; each instance represents one Cloud Scheduler job."""

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
        """Create a Pub/Sub Cloud Scheduler job."""
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

    def update(self):
        """Update an existing Pub/Sub Cloud Scheduler job (schedule, message body, etc.)."""
        message_body = json.dumps(self.message)
        command = [
            "gcloud", "scheduler", "jobs", "update", "pubsub",
            self.name,
            "--location", self.location,
            "--schedule", self.schedule,
            "--topic", self.topic_name,
            "--message-body", message_body,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(f"Failed to update scheduler job '{self.name}': {event}")
        else:
            logger.info(f"Scheduler job '{self.name}' updated successfully. (schedule: {self.schedule})")

    def delete(self):
        """Delete the Cloud Scheduler job, auto-confirming the prompt."""
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

    def describe(self) -> tuple[bool, str]:
        """Return (exists, state). exists=False when job is not found or an error occurs."""
        command = [
            "gcloud", "scheduler", "jobs", "describe",
            self.name,
            "--location", self.location,
            "--project", self.project_id,
        ]
        event = run_sh(command)
        if "NOT_FOUND" in event:
            return False, "NOT_FOUND"
        if "ERROR" in event:
            logger.error(f"Unexpected response describing scheduler job '{self.name}': {event}")
            return False, "ERROR"
        state = next((line.split(": ", 1)[1] for line in event.splitlines() if line.startswith("state:")), "UNKNOWN")
        return True, state

    def list(self):
        """List Cloud Scheduler jobs filtered by this job's name."""
        command = [
            "gcloud", "scheduler", "jobs", "list",
            "--location", self.location,
            f"--filter={self.name}",
            "--project", self.project_id,
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")

    def run(self):
        """Manually trigger the Cloud Scheduler job immediately."""
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
        """Pause the Cloud Scheduler job, stopping automatic execution."""
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
        """Resume a paused Cloud Scheduler job, re-enabling automatic execution."""
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


def _make_scheduler(pipeline_version: str, message: any = None) -> Scheduler:
    """Build a Scheduler instance from the pipeline version config. message may be None for delete/status/run."""
    aigear_config    = AigearConfig.get_config()
    pipeline_config  = PipelinesConfig.get_version_config(pipeline_version)
    scheduler_config = pipeline_config.get("scheduler", {})
    return Scheduler(
        name       = scheduler_config.get("name"),
        location   = aigear_config.gcp.location,
        project_id = aigear_config.gcp.gcp_project_id,
        schedule   = scheduler_config.get("schedule"),
        topic_name = aigear_config.gcp.pub_sub.topic_name,
        message    = message,
        time_zone  = scheduler_config.get("time_zone", "Etc/UTC"),
    )


def _build_messages(
    pipeline_version: str,
    step_names: list[str],
    env: str = ENV_STAGING,
) -> list[dict]:
    """Build the list of Pub/Sub message bodies for each pipeline step (used by create/update)."""
    aigear_config   = AigearConfig.get_config()
    pipeline_config = PipelinesConfig.get_version_config(pipeline_version)

    pl_image = get_image_path(is_service=False)
    ms_image = get_image_path(is_service=True)

    kubernetes_config = aigear_config.gcp.kubernetes
    gke_cluster = kubernetes_config.cluster_name
    gke_zone    = aigear_config.gcp.location

    venv_pl = pipeline_config.get("venv_pl")
    venv_ms = pipeline_config.get("model_service", {}).get("venv_ms")
    ms_config = pipeline_config.get("model_service", {})

    invalid = [s for s in step_names if s not in pipeline_config]
    if invalid:
        valid = [k for k in pipeline_config if not k.startswith("venv_") and k != "scheduler"]
        raise ValueError(
            f"Unknown step name(s) for '{pipeline_version}': {invalid}. "
            f"Valid steps: {','.join(valid)}"
        )

    messages = []
    for step_name in step_names:
        step_config      = pipeline_config[step_name]
        is_model_service = step_name == "model_service"

        if is_model_service and not ms_config.get("release", False):
            logger.warning(
                f"Skipping 'model_service' step for '{pipeline_version}': "
                f"'model_service.release' is not enabled in env.json."
            )
            continue

        step_docker_image = ms_image if is_model_service else pl_image
        step_venv         = venv_ms if is_model_service else venv_pl
        step_env          = env if is_model_service else None
        resolved_step_name = None if is_model_service else step_name

        message = _build_step_message(
            step_config      = step_config,
            pipeline_version = pipeline_version,
            docker_image     = step_docker_image,
            gke_cluster      = gke_cluster,
            gke_zone         = gke_zone,
            venv             = step_venv,
            env              = step_env,
            step_name        = resolved_step_name,
        )
        messages.append(message)
    return messages


def create_scheduler(
    pipeline_version: str,
    step_names: list[str],
    env: str = ENV_STAGING,
):
    """Create a Cloud Scheduler job; skips if a job with the same name already exists."""
    messages  = _build_messages(pipeline_version, step_names, env)
    scheduler = _make_scheduler(pipeline_version, messages)
    exists, state = scheduler.describe()
    if exists:
        logger.info(f"Scheduler job '{scheduler.name}' already exists (state: {state}), skipping create.")
        return
    scheduler.create()


def update_scheduler(
    pipeline_version: str,
    step_names: list[str],
    env: str = ENV_STAGING,
):
    """Update an existing Cloud Scheduler job (schedule and message body)."""
    messages  = _build_messages(pipeline_version, step_names, env)
    scheduler = _make_scheduler(pipeline_version, messages)
    scheduler.update()


def delete_scheduler(pipeline_version: str):
    """Delete the Cloud Scheduler job for the given pipeline version."""
    scheduler = _make_scheduler(pipeline_version)
    scheduler.delete()


def status_scheduler(pipeline_version: str):
    """Print the status of the Cloud Scheduler job for the given pipeline version."""
    scheduler = _make_scheduler(pipeline_version)
    exists, state = scheduler.describe()
    if not exists:
        logger.info(f"Scheduler job '{scheduler.name}' does not exist.")
    elif state == "ENABLED":
        logger.info(f"Scheduler job '{scheduler.name}' is ENABLED (running on schedule).")
    elif state == "PAUSED":
        logger.info(f"Scheduler job '{scheduler.name}' is PAUSED (not running).")
    else:
        logger.info(f"Scheduler job '{scheduler.name}' state: {state}.")


def run_scheduler(pipeline_version: str):
    """Manually trigger the Cloud Scheduler job for the given pipeline version."""
    scheduler = _make_scheduler(pipeline_version)
    scheduler.run()


def pause_scheduler(pipeline_version: str):
    """Pause the Cloud Scheduler job for the given pipeline version."""
    scheduler = _make_scheduler(pipeline_version)
    scheduler.pause()


def resume_scheduler(pipeline_version: str):
    """Resume the paused Cloud Scheduler job for the given pipeline version."""
    scheduler = _make_scheduler(pipeline_version)
    scheduler.resume()


def list_scheduler(pipeline_version: str):
    """List Cloud Scheduler jobs filtered by the job name for the given pipeline version."""
    scheduler = _make_scheduler(pipeline_version)
    scheduler.list()

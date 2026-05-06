from pathlib import Path

from aigear.common import run_sh
from aigear.common.config import AigearConfig
from aigear.common.logger import Logging
from aigear.deploy.common.kubectl_command import kubectl_apply, kubectl_delete, kubectl_status

logger = Logging(log_name=__name__).console_logging()


def switch_gcp_context(cluster_name: str, project_id: str, region: str) -> None:
    command = [
        "gcloud", "container", "clusters", "get-credentials",
        cluster_name,
        f"--region={region}",
        f"--project={project_id}",
    ]
    event = run_sh(command)
    logger.info(event)


def _switch_context() -> None:
    aigear_config = AigearConfig.get_config()
    switch_gcp_context(
        cluster_name=aigear_config.gcp.kubernetes.cluster_name,
        project_id=aigear_config.gcp.gcp_project_id,
        region=aigear_config.gcp.location,
    )


def _apply_grpc(helm_path: Path, action: str) -> None:
    _switch_context()
    event = kubectl_apply(helm_path)
    if "error" in event.lower():
        logger.info(f"Error: {event}.")
    else:
        logger.info(f"{action} completed.")


def deploy_gcp_grpc(helm_path: Path) -> None:
    _apply_grpc(helm_path, "Deployment")


def update_gcp_grpc(helm_path: Path) -> None:
    _apply_grpc(helm_path, "Update")


def delete_gcp_grpc(helm_path: Path) -> None:
    _switch_context()
    kubectl_delete(helm_path)


def status_gcp_grpc(helm_path: Path) -> None:
    _switch_context()
    kubectl_status(helm_path)

from pathlib import Path

from aigear.common import run_sh
from aigear.common.logger import Logging
from aigear.deploy.common.kubectl_command import (
    kubectl_apply,
    kubectl_delete,
    kubectl_status,
)

logger = Logging(log_name=__name__).console_logging()


def switch_local_context() -> None:
    command = ["kubectl", "config", "use-context", "docker-desktop"]
    event = run_sh(command)
    logger.info(event)


def _apply_grpc(helm_path: Path, action: str) -> None:
    switch_local_context()
    event = kubectl_apply(helm_path)
    if "error" in event.lower():
        logger.info(f"Error: {event}.")
    else:
        logger.info(f"{action} completed.")


def deploy_local_grpc(helm_path: Path) -> None:
    _apply_grpc(helm_path, "Deployment")


def update_local_grpc(helm_path: Path) -> None:
    _apply_grpc(helm_path, "Update")


def delete_local_grpc(helm_path: Path) -> None:
    switch_local_context()
    kubectl_delete(helm_path)


def status_local_grpc(helm_path: Path) -> None:
    switch_local_context()
    kubectl_status(helm_path)

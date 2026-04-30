from pathlib import Path
from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()

def helm_deploy(helm_path: Path):
    command = [
        "kubectl", "apply", "-f", helm_path.as_posix()
    ]
    event = run_sh(command)
    logger.info(event)
    return event


def helm_deployment_delete(
        helm_path: Path
):
    command = [
        "kubectl", "delete", "-f", helm_path.as_posix(), "--wait=false"
    ]
    event = run_sh(command)
    logger.info(event)


def helm_deployment_status(helm_path: Path):
    command = [
        "kubectl", "get", "-f", helm_path.as_posix()
    ]
    event = run_sh(command)
    for line in event.splitlines():
        logger.info(line)
    return event

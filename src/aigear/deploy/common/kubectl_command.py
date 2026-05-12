from pathlib import Path
from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


def kubectl_apply(helm_path: Path) -> str:
    command = ["kubectl", "apply", "-f", helm_path.as_posix()]
    event = run_sh(command)
    logger.info(event)
    return event


def kubectl_delete(helm_path: Path) -> None:
    command = ["kubectl", "delete", "-f", helm_path.as_posix(), "--wait=false"]
    event = run_sh(command)
    logger.info(event)


def kubectl_status(helm_path: Path) -> str:
    command = ["kubectl", "get", "-f", helm_path.as_posix()]
    event = run_sh(command)
    for line in event.splitlines():
        logger.info(line)
    return event

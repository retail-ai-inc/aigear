from aigear.common import run_sh
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()


class Artifacts:
    def __init__(
        self,
        repository_name: str,
        location: str,
        project_id: str,
        repository_format: str = "docker",
    ):
        self.repository_name = repository_name
        self.location = location
        self.project_id = project_id
        self.repository_format = repository_format

    def create(self):
        command = [
            "gcloud",
            "artifacts",
            "repositories",
            "create",
            self.repository_name,
            f"--location={self.location}",
            f"--repository-format={self.repository_format}",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def describe(self):
        is_exist = False
        command = [
            "gcloud",
            "artifacts",
            "repositories",
            "describe",
            self.repository_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "ERROR" not in event:
            is_exist = True
        elif "NOT_FOUND" not in event:
            logger.error(
                f"Unexpected error describing artifact registry ({self.repository_name}): {event}"
            )
        return is_exist

    def delete(self):
        command = [
            "gcloud",
            "artifacts",
            "repositories",
            "delete",
            self.repository_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
            "--quiet",
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(
                f"Failed to delete artifact registry ({self.repository_name}): {event}"
            )

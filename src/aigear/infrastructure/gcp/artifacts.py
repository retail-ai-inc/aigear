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
        command = [
            "gcloud",
            "artifacts",
            "repositories",
            "describe",
            self.repository_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        try:
            event = run_sh(command, check=True)
        except RuntimeError as exc:
            if "NOT_FOUND" in str(exc):
                return False
            raise
        if not event.strip():
            raise RuntimeError("Empty Artifact Registry describe output.")
        return True

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
        event = run_sh(command, check=True)
        logger.info(event)

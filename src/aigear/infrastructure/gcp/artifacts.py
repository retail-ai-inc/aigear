from aigear.common import run_sh
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()

class Artifacts:
    def __init__(
        self,
        repository_name: str,
        location: str,
        project_id: str,
        repository_format: str="docker"
    ):
        self.repository_name=repository_name
        self.location=location
        self.project_id=project_id
        self.repository_format=repository_format

    def create(self):
        command = [
            "gcloud", "artifacts", "repositories", "create",
            self.repository_name,
            f"--location={self.location}",
            f"--repository-format={self.repository_format}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "artifacts", "repositories", "describe",
            self.repository_name,
            f"--location={self.location}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)
        if "ERROR" not in event:
            is_exist = True
        return is_exist


if __name__ == "__main__":
    project_id = "ssc-ape-staging"
    repository_name = "test"
    location = "asia-northeast1"
    repositories = Artifacts(
        repository_name=repository_name,
        location=location,
        project_id=project_id,
    )
    repository_exist = repositories.describe()
    print("repository: ", repository_exist)
    
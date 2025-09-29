from aigear.common import run_sh
from aigear.common.stage_logger import create_stage_logger, PipelineStage


# Use deployment stage logger
deployment_logger = create_stage_logger(
    stage=PipelineStage.DEPLOYMENT,
    module_name=__name__,
    cpu_count=2,
    memory_limit="2GB",
    enable_cloud_logging=True
)

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
        with deployment_logger.stage_context() as logger:
            logger.info(f"Creating artifacts repository: {self.repository_name}")
            command = [
                "gcloud", "artifacts", "repositories", "create",
                self.repository_name,
                f"--location={self.location}",
                f"--repository-format={self.repository_format}",
                f"--project={self.project_id}",
            ]
            event = run_sh(command)
            logger.info(f"Repository creation result: {event}")

    def describe(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Checking artifacts repository: {self.repository_name}")
            is_exist = False
            command = [
                "gcloud", "artifacts", "repositories", "describe",
                self.repository_name,
                f"--location={self.location}",
                f"--project={self.project_id}",
            ]
            event = run_sh(command)
            logger.info(f"Repository describe result: {event}")
            if "ERROR" not in event:
                is_exist = True
                logger.info(f"Repository {self.repository_name} exists")
            else:
                logger.warning(f"Repository {self.repository_name} does not exist")
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

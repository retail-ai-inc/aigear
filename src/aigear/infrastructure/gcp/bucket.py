from aigear.common import run_sh
from aigear.common import create_stage_logger, PipelineStage


# Use deployment stage logger
deployment_logger = create_stage_logger(
    stage=PipelineStage.DEPLOYMENT,
    module_name=__name__,
    cpu_count=2,
    memory_limit="2GB",
    enable_cloud_logging=True
)

class Bucket:
    def __init__(
        self,
        bucket_name: str,
        location: str,
        project_id: str,
    ):
        self.bucket_gs = f"gs://{bucket_name}"
        self.location = location
        self.project_id = project_id

    def create(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Creating bucket: {self.bucket_gs}")
            command = [
                "gcloud", "storage", "buckets", "create",
                self.bucket_gs,
                f"--location={self.location}",
                "--uniform-bucket-level-access",
                f"--project={self.project_id}",
            ]
            event = run_sh(command)
            logger.info(f"Bucket creation result: {event}")

    def describe(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Checking bucket: {self.bucket_gs}")
            is_exist = False
            command = [
                "gcloud", "storage", "buckets", "describe",
                self.bucket_gs,
                f"--project={self.project_id}",
            ]
            event = run_sh(command)
            logger.info(f"Bucket describe result: {event}")
            if self.bucket_gs in event and "ERROR" not in event:
                is_exist = True
                logger.info(f"Bucket {self.bucket_gs} exists")
            else:
                logger.warning(f"Bucket {self.bucket_gs} does not exist")
            return is_exist

    def list(self):
        with deployment_logger.stage_context() as logger:
            logger.info(f"Listing bucket contents: {self.bucket_gs}")
            command = [
                "gcloud", "storage", "buckets", "list",
                self.bucket_gs,
                f"--project={self.project_id}",
            ]
            event = run_sh(command)
            logger.info(f"Bucket list result:\n{event}")

    def delete(self):
        with deployment_logger.stage_context() as logger:
            logger.warning(f"Deleting bucket: {self.bucket_gs}")
            command = [
                "gcloud", "storage", "rm", "-r",
                self.bucket_gs,
                f"--project={self.project_id}",
            ]
            event = run_sh(command)
            logger.info(f"Bucket deletion result: {event}")

if __name__ == "__main__":
    project_id = "ssc-ape-staging"
    bucket_name = "medovik-ape-staging"
    location = "asia-northeast1"
    bucket = Bucket(
        bucket_name=bucket_name,
        project_id=project_id,
        location=location,
    )
    bucket_exist = bucket.describe()
    print("bucket: ", bucket_exist)

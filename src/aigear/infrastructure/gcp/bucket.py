from aigear.common import run_sh
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()

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
        command = [
            "gcloud", "storage", "buckets", "create",
            self.bucket_gs,
            f"--location={self.location}",
            "--uniform-bucket-level-access",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "storage", "buckets", "describe",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)
        if self.bucket_gs in event and "ERROR" not in event:
            is_exist = True
        return is_exist

    def list(self):
        command = [
            "gcloud", "storage", "buckets", "list",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")

    def delete(self):
        command = [
            "gcloud", "storage", "rm", "-r",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

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

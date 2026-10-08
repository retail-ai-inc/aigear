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
            "gcloud",
            "storage",
            "buckets",
            "create",
            self.bucket_gs,
            f"--location={self.location}",
            "--uniform-bucket-level-access",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def add_permissions_to_gcs(self, sa_email):
        command = [
            "gcloud",
            "storage",
            "buckets",
            "add-iam-policy-binding",
            self.bucket_gs,
            f"--member=serviceAccount:{sa_email}",
            "--role=roles/storage.admin",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)

    def describe(self):
        command = [
            "gcloud",
            "storage",
            "buckets",
            "describe",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        try:
            event = run_sh(command, check=True)
        except RuntimeError as exc:
            if any(
                marker in str(exc)
                for marker in ("BucketNotFoundException", "NOT_FOUND", "not found")
            ):
                return False
            raise
        if self.bucket_gs not in event:
            raise RuntimeError(f"Unexpected bucket describe output: {event}")
        return True

    def list(self):
        command = [
            "gcloud",
            "storage",
            "buckets",
            "list",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")

    def delete(self):
        command = [
            "gcloud",
            "storage",
            "rm",
            "-r",
            self.bucket_gs,
            f"--project={self.project_id}",
        ]
        event = run_sh(command, check=True)
        logger.info(event)

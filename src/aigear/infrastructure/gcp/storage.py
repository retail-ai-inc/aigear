import logging
from ...common.sh import run_sh


class Bucket:
    def __init__(
            self,
            bucket_name: str,
            project_id: str,
            location: str,
    ):
        self.bucket = f"gs://{bucket_name}-{project_id}"
        self.location = location

    def create(self):
        command = [
            "gcloud", "storage", "buckets", "create",
            self.bucket,
            f"--location={self.location}",
            "--uniform-bucket-level-access",
        ]
        event = run_sh(command)
        logging.info(event)

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "storage", "buckets", "describe",
            self.bucket,
        ]
        event = run_sh(command)
        logging.info(event)
        if self.bucket in event and "ERROR" not in event:
            is_exist = True
        return is_exist

    def list(self):
        command = [
            "gcloud", "storage", "buckets", "list",
            self.bucket,
        ]
        event = run_sh(command)
        logging.info(f"\n{event}")

    def delete(self):
        command = [
            "gcloud", "storage", "rm", "-r",
            self.bucket,
        ]
        event = run_sh(command)
        logging.info(event)


class ManagedFolders:
    def __init__(self, bucket_name, project_id):
        self.bucket = f"gs://{bucket_name}-{project_id}"

    def create(self, folder_name):
        folder = f"{self.bucket}/{folder_name}"
        command = [
            "gcloud", "storage", "managed-folders", "create",
            folder,
        ]
        event = run_sh(command)
        logging.info(event)

    def describe(self, folder_name):
        is_exist = False
        folder = f"{self.bucket}/{folder_name}"
        command = [
            "gcloud", "storage", "managed-folders", "describe",
            folder,
        ]
        event = run_sh(command)
        logging.info(event)
        if folder in event and "ERROR" not in event:
            is_exist = True
        return is_exist

    def list(self):
        command = [
            "gcloud", "storage", "managed-folders", "list",
            self.bucket,
        ]
        event = run_sh(command)
        logging.info(f"\n{event}")

    def delete(self, folder_name):
        folder = f"{self.bucket}/{folder_name}"
        command = [
            "gcloud", "storage", "managed-folders", "delete",
            folder,
        ]
        event = run_sh(command)
        logging.info(event)

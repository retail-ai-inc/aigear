import weakref
from pathlib import Path
from google.cloud import storage
from aigear.common.schema.bucket_schema import BucketABC
import shutil


class RealGCS(BucketABC):
    def __init__(self, project_id, bucket_name):
        super().__init__(project_id, bucket_name)
        self.bucket_name = bucket_name
        self.bucket_client = storage.Client(project_id)
        self.bucket = self.bucket_client.get_bucket(bucket_name)
        weakref.finalize(self, self.close)

    def close(self):
        # Close database link during recycling
        if hasattr(self, "bucket_client"):
            self.bucket_client.close()

    def download(self, bucket_blob_name, local_blob_path):
        download_path = Path(local_blob_path)
        download_path.parent.mkdir(parents=True, exist_ok=True)

        blob = self.bucket.blob(bucket_blob_name)
        blob.download_to_filename(local_blob_path)

    def upload(self, local_blob_name, bucket_blob_name):
        blob = self.bucket.blob(bucket_blob_name)
        blob.cache_control = 'no-cache'
        blob.upload_from_filename(local_blob_name)

    def copy_blob(self, source_blob_name, destination_blob_name):
        source_blob = self.bucket.blob(source_blob_name)
        if source_blob.exists():
            destination_bucket = self.bucket_client.bucket(self.bucket_name)

            self.bucket.copy_blob(
                source_blob, destination_bucket, destination_blob_name
            )
        else:
            print(f"The source file {source_blob_name} does not exist and cannot be copied.")


class LocalGCSMock(BucketABC):
    def __init__(self, project_id, bucket_name):
        super().__init__(project_id, bucket_name)
        if not bucket_name:
            bucket_name = "gcs_mock"
            print(f"'bucket_name' is not set in env.json, default 'gcs_mock'.")
        self.bucket_path = bucket_name
        self.bucket_path.mkdir(parents=True, exist_ok=True)

    def close(self):
        pass

    def download(self, bucket_blob_name, local_blob_path):
        original_path = self.bucket_path / bucket_blob_name
        if isinstance(local_blob_path, str):
            local_blob_path = Path(local_blob_path)
        local_blob_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(original_path, local_blob_path)

    def upload(self, local_blob_name, bucket_blob_name):
        target_path = self.bucket_path / bucket_blob_name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local_blob_name, target_path)

    def copy_blob(self, source_blob_name, destination_blob_name):
        original_path = self.bucket_path / source_blob_name
        target_path = self.bucket_path / destination_blob_name
        shutil.copy(original_path, target_path)


def bucket_client(
    project_id: str | None = None,
    bucket_name: str | None = None,
    bucket_on: bool = True
):
    if bucket_on:
        return RealGCS(project_id, bucket_name)
    else:
        return LocalGCSMock(project_id, bucket_name)

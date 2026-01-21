import weakref
from pathlib import Path
from google.cloud import storage
from aigear.common.schema.bucket_schema import BucketABC


class BucketClient(BucketABC):
    def __init__(self, project_id, bucket_name, bucket_on=True):
        super().__init__(project_id, bucket_name)
        self.bucket_on = bucket_on
        if self.bucket_on:
            self.bucket_name = bucket_name
            self.bucket_client = storage.Client(project_id)
            self.bucket = self.bucket_client.get_bucket(bucket_name)
            weakref.finalize(self, self.close)

    def close(self):
        # Close database link during recycling
        if hasattr(self, "bucket_client"):
            self.bucket_client.close()

    def download(self, bucket_blob_name, local_blob_path):
        if self.bucket_on:
            download_path = Path(local_blob_path)
            download_path.parent.mkdir(parents=True, exist_ok=True)

            blob = self.bucket.blob(bucket_blob_name)
            blob.download_to_filename(local_blob_path)

    def upload(self, local_blob_name, bucket_blob_name):
        if self.bucket_on:
            blob = self.bucket.blob(bucket_blob_name)
            blob.cache_control = 'no-cache'
            blob.upload_from_filename(local_blob_name)

    def copy_blob(self, source_blob_name, destination_blob_name):
        if self.bucket_on:
            source_blob = self.bucket.blob(source_blob_name)
            if source_blob.exists():
                destination_bucket = self.bucket_client.bucket(self.bucket_name)

                self.bucket.copy_blob(
                    source_blob, destination_bucket, destination_blob_name
                )
            else:
                print(f"The source file {source_blob_name} does not exist and cannot be copied.")

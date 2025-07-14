from abc import ABC, abstractmethod


class BucketABC(ABC):
    def __init__(self, project_id: str, bucket_name: str):
        self.project_id = project_id
        self.bucket_name = bucket_name

    @abstractmethod
    def close(self):
        # Close database link during recycling
        pass

    @abstractmethod
    def download(self, bucket_blob_name: str, local_blob_name: str):
        """Download a blob from the bucket to a local file."""
        pass

    @abstractmethod
    def upload(self, local_blob_name: str, bucket_blob_name: str):
        """Upload a local file to the bucket."""
        pass

    @abstractmethod
    def copy_blob(self, source_blob_name: str, destination_blob_name: str):
        """Copy a blob within the bucket."""
        pass

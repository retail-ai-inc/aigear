from pathlib import Path
from typing import Literal

from aigear.db.bucket import LocalGCSMock, RealGCS


class AssetManagement:
    def __init__(
        self,
        pipeline_version: str,
        data_type: Literal["dataset", "feature", "training"],
        project_id: str | None = None,
        bucket_name: str | None = None,
        bucket_on: bool = True,
    ):
        self.pipeline_version = pipeline_version
        self.project_dir = Path.cwd()
        self.data_type = data_type

        self.local_asset_path = self.project_dir / "asset"
        if bucket_on:
            self.bucket_client = RealGCS(project_id, bucket_name)
        else:
            local_bucket_mock_path = self.project_dir / "asset" / bucket_name
            self.bucket_client = LocalGCSMock(project_id, local_bucket_mock_path)

    def download(self, file_name: str) -> Path:
        local_blob_path = self.get_local_path(file_name)
        bucket_blob_path = self.get_bucket_blob(file_name)
        self.bucket_client.download(bucket_blob_path, local_blob_path)
        return local_blob_path

    def upload(self, file_name: str) -> None:
        local_blob_path = self.get_local_path(file_name)
        bucket_blob_path = self.get_bucket_blob(file_name)
        self.bucket_client.upload(local_blob_path, bucket_blob_path)

    def copy_blob(self, source_file_name: str, destination_file_name: str) -> None:
        source_blob_path = self.get_bucket_blob(source_file_name)
        destination_blob_path = self.get_bucket_blob(destination_file_name)
        self.bucket_client.copy_blob(source_blob_path, destination_blob_path)

    def get_local_path(self, local_file_name: str) -> Path:
        dir_path = self.local_asset_path / self.pipeline_version / self.data_type
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / local_file_name

    def get_bucket_blob(self, bucket_file_name: str) -> str:
        return f"{self.pipeline_version}/{self.data_type}/{bucket_file_name}"

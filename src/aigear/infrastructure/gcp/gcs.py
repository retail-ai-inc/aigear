"""
GCS (Google Cloud Storage) Model Storage Module

Provides functionality to upload, download, and manage model files in GCS.
"""

from aigear.common import run_sh
from aigear.common.logger import Logging
from pathlib import Path
from typing import Optional, List


logger = Logging(log_name=__name__).console_logging()


class GCSModelStorage:
    """GCS Model Storage Manager"""

    def __init__(
        self,
        bucket_name: str,
        project_id: str,
        location: str = "asia-northeast1",
    ):
        """
        Initialize GCS Model Storage

        Args:
            bucket_name: GCS bucket name
            project_id: GCP project ID
            location: Bucket location
        """
        self.bucket_name = bucket_name
        self.project_id = project_id
        self.location = location
        self.bucket_uri = f"gs://{bucket_name}"

    def create_bucket(self) -> bool:
        """
        Create GCS bucket if it doesn't exist

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Creating GCS bucket: {self.bucket_name}")

        command = [
            "gsutil", "mb",
            "-p", self.project_id,
            "-l", self.location,
            "-b", "on",  # Enable uniform bucket-level access
            self.bucket_uri,
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event and "already exists" not in event:
                logger.error(f"Failed to create bucket: {event}")
                return False

            logger.info(f"✓ Bucket {self.bucket_name} ready")
            return True

        except Exception as e:
            logger.error(f"Error creating bucket: {str(e)}")
            return False

    def bucket_exists(self) -> bool:
        """
        Check if bucket exists

        Returns:
            True if bucket exists, False otherwise
        """
        command = [
            "gsutil", "ls",
            "-b", self.bucket_uri,
        ]

        try:
            event = run_sh(command)
            return "ERROR" not in event

        except Exception as e:
            logger.error(f"Error checking bucket: {str(e)}")
            return False

    def upload_file(self, local_path: Path, gcs_path: str) -> bool:
        """
        Upload file to GCS

        Args:
            local_path: Local file path
            gcs_path: GCS path (relative to bucket, e.g., "models/trial/alc3/model.pkl")

        Returns:
            True if successful, False otherwise
        """
        if not local_path.exists():
            logger.error(f"Local file not found: {local_path}")
            return False

        gcs_uri = f"{self.bucket_uri}/{gcs_path}"
        logger.info(f"Uploading {local_path} to {gcs_uri}")

        command = [
            "gsutil", "cp",
            str(local_path),
            gcs_uri,
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to upload file: {event}")
                return False

            logger.info(f"✓ File uploaded successfully")
            return True

        except Exception as e:
            logger.error(f"Error uploading file: {str(e)}")
            return False

    def upload_directory(self, local_dir: Path, gcs_prefix: str) -> bool:
        """
        Upload entire directory to GCS

        Args:
            local_dir: Local directory path
            gcs_prefix: GCS prefix (e.g., "models/trial/alc3/")

        Returns:
            True if successful, False otherwise
        """
        if not local_dir.exists() or not local_dir.is_dir():
            logger.error(f"Local directory not found: {local_dir}")
            return False

        gcs_uri = f"{self.bucket_uri}/{gcs_prefix}"
        logger.info(f"Uploading directory {local_dir} to {gcs_uri}")

        command = [
            "gsutil", "-m", "cp", "-r",
            f"{local_dir}/*",
            gcs_uri,
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to upload directory: {event}")
                return False

            logger.info(f"✓ Directory uploaded successfully")
            return True

        except Exception as e:
            logger.error(f"Error uploading directory: {str(e)}")
            return False

    def download_file(self, gcs_path: str, local_path: Path) -> bool:
        """
        Download file from GCS

        Args:
            gcs_path: GCS path (relative to bucket)
            local_path: Local file path

        Returns:
            True if successful, False otherwise
        """
        gcs_uri = f"{self.bucket_uri}/{gcs_path}"
        logger.info(f"Downloading {gcs_uri} to {local_path}")

        # Create parent directory if needed
        local_path.parent.mkdir(parents=True, exist_ok=True)

        command = [
            "gsutil", "cp",
            gcs_uri,
            str(local_path),
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to download file: {event}")
                return False

            logger.info(f"✓ File downloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}")
            return False

    def download_directory(self, gcs_prefix: str, local_dir: Path) -> bool:
        """
        Download entire directory from GCS

        Args:
            gcs_prefix: GCS prefix (e.g., "models/trial/alc3/")
            local_dir: Local directory path

        Returns:
            True if successful, False otherwise
        """
        gcs_uri = f"{self.bucket_uri}/{gcs_prefix}"
        logger.info(f"Downloading directory {gcs_uri} to {local_dir}")

        # Create directory if needed
        local_dir.mkdir(parents=True, exist_ok=True)

        command = [
            "gsutil", "-m", "cp", "-r",
            f"{gcs_uri}*",
            str(local_dir),
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to download directory: {event}")
                return False

            logger.info(f"✓ Directory downloaded successfully")
            return True

        except Exception as e:
            logger.error(f"Error downloading directory: {str(e)}")
            return False

    def list_files(self, prefix: str = "") -> List[str]:
        """
        List files in bucket with optional prefix

        Args:
            prefix: Optional prefix to filter files

        Returns:
            List of file paths
        """
        gcs_uri = f"{self.bucket_uri}/{prefix}" if prefix else self.bucket_uri

        command = [
            "gsutil", "ls",
            gcs_uri,
        ]

        try:
            event = run_sh(command)
            files = [line.strip() for line in event.split('\n') if line.strip() and not line.endswith('/')]
            return files

        except Exception as e:
            logger.error(f"Error listing files: {str(e)}")
            return []

    def delete_file(self, gcs_path: str) -> bool:
        """
        Delete file from GCS

        Args:
            gcs_path: GCS path (relative to bucket)

        Returns:
            True if successful, False otherwise
        """
        gcs_uri = f"{self.bucket_uri}/{gcs_path}"
        logger.info(f"Deleting {gcs_uri}")

        command = [
            "gsutil", "rm",
            gcs_uri,
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to delete file: {event}")
                return False

            logger.info(f"✓ File deleted successfully")
            return True

        except Exception as e:
            logger.error(f"Error deleting file: {str(e)}")
            return False

    def sync_directory(self, local_dir: Path, gcs_prefix: str, delete: bool = False) -> bool:
        """
        Sync local directory with GCS (rsync-like behavior)

        Args:
            local_dir: Local directory path
            gcs_prefix: GCS prefix
            delete: Delete files in GCS that don't exist locally

        Returns:
            True if successful, False otherwise
        """
        if not local_dir.exists() or not local_dir.is_dir():
            logger.error(f"Local directory not found: {local_dir}")
            return False

        gcs_uri = f"{self.bucket_uri}/{gcs_prefix}"
        logger.info(f"Syncing {local_dir} to {gcs_uri}")

        command = [
            "gsutil", "-m", "rsync", "-r",
        ]

        if delete:
            command.append("-d")  # Delete files in destination not in source

        command.extend([
            str(local_dir),
            gcs_uri,
        ])

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to sync directory: {event}")
                return False

            logger.info(f"✓ Directory synced successfully")
            return True

        except Exception as e:
            logger.error(f"Error syncing directory: {str(e)}")
            return False

    def generate_model_downloader_script(self, model_paths: dict, output_file: Path) -> Path:
        """
        Generate a shell script to download models from GCS

        This script can be used in Docker ENTRYPOINT to download models on container startup.

        Args:
            model_paths: Dictionary of model paths (from env.json)
            output_file: Output script file path

        Returns:
            Path to generated script
        """
        script_lines = [
            "#!/bin/bash",
            "# Auto-generated model downloader script",
            "set -e",
            "",
            "echo 'Downloading models from GCS...'",
            "",
        ]

        for model_name, model_path in model_paths.items():
            # Check if path is a GCS URI
            if model_path.startswith("gs://"):
                local_path = model_path.replace("gs://", "/models/")
                script_lines.extend([
                    f"echo 'Downloading {model_name}...'",
                    f"mkdir -p $(dirname {local_path})",
                    f"gsutil cp {model_path} {local_path}",
                    f"echo '✓ {model_name} downloaded'",
                    "",
                ])

        script_lines.extend([
            "echo 'All models downloaded successfully!'",
        ])

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text('\n'.join(script_lines), encoding='utf-8')

        # Make executable
        try:
            import stat
            output_file.chmod(output_file.stat().st_mode | stat.S_IEXEC)
        except:
            pass

        logger.info(f"✓ Model downloader script generated: {output_file}")
        return output_file


if __name__ == "__main__":
    # Example usage
    storage = GCSModelStorage(
        bucket_name="my-ml-models",
        project_id="my-project",
        location="asia-northeast1"
    )

    # Create bucket
    storage.create_bucket()

    # Upload model file
    local_model = Path("models/trial/alc3/model.pkl")
    storage.upload_file(local_model, "models/trial/alc3/model.pkl")

    # List files
    files = storage.list_files("models/")
    print(f"Files in bucket: {files}")

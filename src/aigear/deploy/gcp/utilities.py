from __future__ import annotations
import tarfile
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from google.cloud import storage
from google.auth import default

from .errors import CloudBuildConfigError, CloudBuildError
from ...common.logger import logger


def upload_source_to_gcs(
    source_path: Path,
    bucket_name: str,
    object_name: str = "source.tar.gz",
    project_id: Optional[str] = None
) -> str:
    """
    Upload source code to GCS as a tar.gz file
    
    Args:
        source_path: Path to source code directory
        bucket_name: GCS bucket name
        object_name: Object name in bucket
        project_id: Google Cloud project ID
        
    Returns:
        str: GCS URI of uploaded file
        
    Raises:
        CloudBuildError: If upload fails
        CloudBuildConfigError: If configuration is invalid
    """
    if not source_path.exists():
        raise CloudBuildConfigError(f"Source path does not exist: {source_path}")
    
    if not source_path.is_dir():
        raise CloudBuildConfigError(f"Source path must be a directory: {source_path}")
    
    try:
        # Get credentials and project
        credentials, detected_project = default()
        project_id = project_id or detected_project
        
        if not project_id:
            raise CloudBuildConfigError("No Google Cloud project found")
        
        # Create storage client
        storage_client = storage.Client(credentials=credentials, project=project_id)
        
        # Get or create bucket
        try:
            bucket = storage_client.get_bucket(bucket_name)
        except Exception:
            logger.info(f"Creating bucket: {bucket_name}")
            bucket = storage_client.create_bucket(bucket_name, project=project_id)
        
        # Create temporary tar.gz file
        with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        
        try:
            # Create tar.gz archive
            with tarfile.open(temp_path, "w:gz") as tar:
                tar.add(source_path, arcname=".")
            
            # Upload to GCS
            blob = bucket.blob(object_name)
            blob.upload_from_filename(temp_path)
            
            gcs_uri = f"gs://{bucket_name}/{object_name}"
            logger.info(f"Uploaded source to: {gcs_uri}")
            
            return gcs_uri
            
        finally:
            # Clean up temporary file
            temp_path.unlink(missing_ok=True)
            
    except Exception as e:
        raise CloudBuildError(f"Failed to upload source to GCS: {e}") from e


def create_cloudbuild_yaml(
    output_path: Path,
    image_name: str,
    dockerfile: str = "Dockerfile",
    steps: Optional[list] = None,
    substitutions: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None
) -> None:
    """
    Create a cloudbuild.yaml configuration file
    
    Args:
        output_path: Path to save cloudbuild.yaml
        image_name: Name for the Docker image
        dockerfile: Path to Dockerfile
        steps: Custom build steps
        substitutions: Build substitutions
        options: Build options
        
    Raises:
        CloudBuildConfigError: If configuration is invalid
    """
    if not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Default build steps
    if steps is None:
        steps = [
            {
                "name": "gcr.io/cloud-builders/docker",
                "args": [
                    "build",
                    "-f", dockerfile,
                    "-t", f"gcr.io/$PROJECT_ID/{image_name}",
                    "."
                ]
            }
        ]
    
    # Default options
    if options is None:
        options = {
            "machineType": "E2_HIGHCPU_8",
            "timeout": "600s"
        }
    
    # Create cloudbuild.yaml content
    config = {
        "steps": steps,
        "images": [f"gcr.io/$PROJECT_ID/{image_name}"],
        "options": options
    }
    
    # Add substitutions if provided
    if substitutions:
        config["substitutions"] = substitutions
    
    # Write to file
    import yaml
    with open(output_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    logger.info(f"Created cloudbuild.yaml at: {output_path}")


def validate_cloudbuild_config(config_path: Path) -> bool:
    """
    Validate a cloudbuild.yaml configuration file
    
    Args:
        config_path: Path to cloudbuild.yaml file
        
    Returns:
        bool: True if valid, False otherwise
        
    Raises:
        CloudBuildConfigError: If configuration is invalid
    """
    if not config_path.exists():
        raise CloudBuildConfigError(f"Config file does not exist: {config_path}")
    
    try:
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Validate required fields
        required_fields = ['steps', 'images']
        for field in required_fields:
            if field not in config:
                raise CloudBuildConfigError(f"Missing required field: {field}")
        
        # Validate steps
        if not isinstance(config['steps'], list) or len(config['steps']) == 0:
            raise CloudBuildConfigError("Steps must be a non-empty list")
        
        # Validate images
        if not isinstance(config['images'], list) or len(config['images']) == 0:
            raise CloudBuildConfigError("Images must be a non-empty list")
        
        logger.info(f"Cloud Build config is valid: {config_path}")
        return True
        
    except Exception as e:
        raise CloudBuildConfigError(f"Invalid Cloud Build config: {e}") from e


def get_gcs_bucket_name(project_id: str, suffix: str = "cloudbuild") -> str:
    """
    Generate a GCS bucket name for Cloud Build
    
    Args:
        project_id: Google Cloud project ID
        suffix: Bucket name suffix
        
    Returns:
        str: Bucket name
    """
    # GCS bucket names must be globally unique and follow DNS naming conventions
    bucket_name = f"{project_id}-{suffix}"
    
    # Replace invalid characters
    bucket_name = bucket_name.replace("_", "-").lower()
    
    # Ensure it starts with a letter or number
    if not bucket_name[0].isalnum():
        bucket_name = f"aigear-{bucket_name}"
    
    return bucket_name 
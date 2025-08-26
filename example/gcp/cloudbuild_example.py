#!/usr/bin/env python3
"""
Example script demonstrating how to use the Cloud Build functionality.

This script shows how to:
1. Build Docker images using Cloud Build
2. Upload source code to GCS
3. Create and validate cloudbuild.yaml configurations
4. Monitor build status
"""

import sys
from pathlib import Path
from aigear.deploy.gcp import CloudBuildBuilder
# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from aigear.deploy.gcp import (
    CloudBuildBuilder,
    upload_source_to_gcs,
    create_cloudbuild_yaml,
    validate_cloudbuild_config,
    get_gcs_bucket_name,
    get_project_id
)
from aigear.common.logger import logger


def example_build_from_gcs():
    """Example: Build from GCS source"""
    logger.info("=== Example: Build from GCS ===")
    
    try:
        # Initialize builder
        builder = CloudBuildBuilder()
        
        # Build from GCS source
        gcs_source = "gs://your-bucket/source.tar.gz"  # Replace with your GCS path
        image_name = "my-app"
        
        build_id = builder.build_from_gcs(
            gcs_source=gcs_source,
            image_name=image_name,
            dockerfile="Dockerfile",
            timeout_minutes=15,
            tags=["latest", "v1.0.0"]
        )
        
        logger.info(f"Build started with ID: {build_id}")
        
        # Get build status
        build_status = builder.get_build_status(build_id)
        logger.info(f"Build status: {build_status.status}")
        
    except Exception as e:
        logger.error(f"Build failed: {e}")


def example_upload_and_build():
    """Example: Upload source to GCS and build"""
    logger.info("=== Example: Upload and Build ===")
    
    try:
        # Get project ID
        project_id = get_project_id()
        logger.info(f"Using project: {project_id}")
        
        # Generate bucket name
        bucket_name = get_gcs_bucket_name(project_id)
        logger.info(f"Using bucket: {bucket_name}")
        
        # Upload source code to GCS
        source_path = Path(".")  # Current directory
        gcs_uri = upload_source_to_gcs(
            source_path=source_path,
            bucket_name=bucket_name,
            object_name="source.tar.gz"
        )
        
        # Initialize builder
        builder = CloudBuildBuilder()
        
        # Build from uploaded source
        image_name = "my-app"
        build_id = builder.build_from_gcs(
            gcs_source=gcs_uri,
            image_name=image_name,
            timeout_minutes=10
        )
        
        logger.info(f"Build completed: {build_id}")
        
    except Exception as e:
        logger.error(f"Upload and build failed: {e}")


def example_create_cloudbuild_config():
    """Example: Create cloudbuild.yaml configuration"""
    logger.info("=== Example: Create cloudbuild.yaml ===")
    
    try:
        # Create cloudbuild.yaml
        config_path = Path("cloudbuild.yaml")
        image_name = "my-app"
        
        create_cloudbuild_yaml(
            output_path=config_path,
            image_name=image_name,
            dockerfile="Dockerfile",
            substitutions={
                "VERSION": "1.0.0",
                "ENVIRONMENT": "production"
            },
            options={
                "machineType": "E2_HIGHCPU_8",
                "timeout": "600s",
                "logStreamingOption": "STREAM_ON"
            }
        )
        
        # Validate the configuration
        is_valid = validate_cloudbuild_config(config_path)
        logger.info(f"Configuration is valid: {is_valid}")
        
    except Exception as e:
        logger.error(f"Config creation failed: {e}")


def example_list_builds():
    """Example: List recent builds"""
    logger.info("=== Example: List Builds ===")
    
    try:
        # Initialize builder
        builder = CloudBuildBuilder()
        
        # List recent builds
        builds = builder.list_builds(page_size=5)
        
        logger.info(f"Found {len(builds)} recent builds:")
        for build in builds:
            logger.info(f"  - {build.id}: {build.status} ({build.create_time})")
            
    except Exception as e:
        logger.error(f"List builds failed: {e}")


def main():
    """Main function to run examples"""
    logger.info("Starting Cloud Build examples...")
    
    # Run examples
    example_create_cloudbuild_config()
    example_list_builds()
    
    # Uncomment the following lines to run actual builds
    # (requires proper GCP setup and authentication)
    # example_upload_and_build()
    # example_build_from_gcs()
    
    logger.info("Examples completed!")


if __name__ == "__main__":
    main() 
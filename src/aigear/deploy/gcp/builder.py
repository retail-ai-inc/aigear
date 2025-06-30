from __future__ import annotations
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from google.cloud import cloudbuild_v1
from google.protobuf import duration_pb2

from .client import get_cloud_build_client, get_project_id
from .errors import (
    CloudBuildError,
    CloudBuildConfigError,
    CloudBuildTimeoutError
)
from ...common.logger import logger


class CloudBuildBuilder:
    """Cloud Build builder for automated builds"""
    
    def __init__(self, project_id: Optional[str] = None):
        """
        Initialize Cloud Build builder
        
        Args:
            project_id: Google Cloud project ID. If None, will be auto-detected
        """
        self.client = get_cloud_build_client()
        self.project_id = project_id or get_project_id()
        self.project_path = f"projects/{self.project_id}"
        
    def build_from_source(
        self,
        source_path: Path,
        image_name: str,
        dockerfile: str = "Dockerfile",
        timeout_minutes: int = 10,
        machine_type: str = "E2_HIGHCPU_8",
        substitutions: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ) -> str:
        """
        Build Docker image from source using Cloud Build
        
        Args:
            source_path: Path to source code directory
            image_name: Name for the Docker image
            dockerfile: Path to Dockerfile relative to source_path
            timeout_minutes: Build timeout in minutes
            machine_type: Cloud Build machine type
            substitutions: Build substitutions
            tags: Additional image tags
            **kwargs: Additional build options
            
        Returns:
            str: Build ID
            
        Raises:
            CloudBuildError: If build fails
            CloudBuildConfigError: If configuration is invalid
        """
        if not source_path.exists():
            raise CloudBuildConfigError(f"Source path does not exist: {source_path}")
            
        if not (source_path / dockerfile).exists():
            raise CloudBuildConfigError(f"Dockerfile not found: {source_path / dockerfile}")
        
        # Prepare build configuration
        build_config = self._create_build_config(
            source_path=source_path,
            image_name=image_name,
            dockerfile=dockerfile,
            timeout_minutes=timeout_minutes,
            machine_type=machine_type,
            substitutions=substitutions,
            tags=tags,
            **kwargs
        )
        
        logger.info(f"Starting Cloud Build for image: {image_name}")
        logger.info(f"Source path: {source_path}")
        logger.info(f"Project: {self.project_id}")
        
        try:
            # Create build request
            request = cloudbuild_v1.CreateBuildRequest(
                parent=self.project_path,
                build=build_config
            )
            
            # Start the build
            operation = self.client.create_build(request=request)
            
            # Wait for build to complete
            build_id = self._wait_for_build(operation)
            
            logger.info(f"Build completed successfully: {build_id}")
            return build_id
            
        except Exception as e:
            raise CloudBuildError(f"Build failed: {e}") from e
    
    def build_from_gcs(
        self,
        gcs_source: str,
        image_name: str,
        dockerfile: str = "Dockerfile",
        timeout_minutes: int = 10,
        machine_type: str = "E2_HIGHCPU_8",
        substitutions: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ) -> str:
        """
        Build Docker image from GCS source using Cloud Build
        
        Args:
            gcs_source: GCS bucket and object path (e.g., "gs://bucket/source.tar.gz")
            image_name: Name for the Docker image
            dockerfile: Path to Dockerfile relative to source
            timeout_minutes: Build timeout in minutes
            machine_type: Cloud Build machine type
            substitutions: Build substitutions
            tags: Additional image tags
            **kwargs: Additional build options
            
        Returns:
            str: Build ID
            
        Raises:
            CloudBuildError: If build fails
            CloudBuildConfigError: If configuration is invalid
        """
        # Prepare build configuration
        build_config = self._create_build_config(
            gcs_source=gcs_source,
            image_name=image_name,
            dockerfile=dockerfile,
            timeout_minutes=timeout_minutes,
            machine_type=machine_type,
            substitutions=substitutions,
            tags=tags,
            **kwargs
        )
        
        logger.info(f"Starting Cloud Build from GCS: {gcs_source}")
        logger.info(f"Image name: {image_name}")
        logger.info(f"Project: {self.project_id}")
        
        try:
            # Create build request
            request = cloudbuild_v1.CreateBuildRequest(
                parent=self.project_path,
                build=build_config
            )
            
            # Start the build
            operation = self.client.create_build(request=request)
            
            # Wait for build to complete
            build_id = self._wait_for_build(operation)
            
            logger.info(f"Build completed successfully: {build_id}")
            return build_id
            
        except Exception as e:
            raise CloudBuildError(f"Build failed: {e}") from e
    
    def get_build_status(self, build_id: str) -> cloudbuild_v1.Build:
        """
        Get build status by build ID
        
        Args:
            build_id: Cloud Build build ID
            
        Returns:
            Build: Build status information
        """
        request = cloudbuild_v1.GetBuildRequest(
            name=f"{self.project_path}/builds/{build_id}"
        )
        return self.client.get_build(request=request)
    
    def list_builds(
        self,
        page_size: int = 20,
        filter_str: Optional[str] = None
    ) -> List[cloudbuild_v1.Build]:
        """
        List recent builds
        
        Args:
            page_size: Number of builds to return
            filter_str: Filter string for builds
            
        Returns:
            List[Build]: List of builds
        """
        request = cloudbuild_v1.ListBuildsRequest(
            parent=self.project_path,
            page_size=page_size,
            filter=filter_str
        )
        
        builds = []
        for build in self.client.list_builds(request=request):
            builds.append(build)
            
        return builds
    
    def _create_build_config(
        self,
        image_name: str,
        dockerfile: str = "Dockerfile",
        timeout_minutes: int = 10,
        machine_type: str = "E2_HIGHCPU_8",
        substitutions: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        source_path: Optional[Path] = None,
        gcs_source: Optional[str] = None,
        **kwargs
    ) -> cloudbuild_v1.Build:
        """Create Cloud Build configuration"""
        
        # Set up source
        if source_path:
            source = cloudbuild_v1.Source(
                storage_source=cloudbuild_v1.StorageSource(
                    bucket=f"{self.project_id}_cloudbuild",
                    object_="source.tar.gz"  # This would need to be uploaded first
                )
            )
        elif gcs_source:
            # Parse GCS source
            if not gcs_source.startswith("gs://"):
                raise CloudBuildConfigError("GCS source must start with 'gs://'")
            
            bucket_object = gcs_source[5:]  # Remove "gs://"
            if "/" not in bucket_object:
                raise CloudBuildConfigError("Invalid GCS source format")
                
            bucket, object_path = bucket_object.split("/", 1)
            source = cloudbuild_v1.Source(
                storage_source=cloudbuild_v1.StorageSource(
                    bucket=bucket,
                    object_=object_path
                )
            )
        else:
            raise CloudBuildConfigError("Either source_path or gcs_source must be provided")
        
        # Set up steps
        steps = [
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=[
                    "build",
                    "-f", dockerfile,
                    "-t", f"gcr.io/{self.project_id}/{image_name}",
                    "."
                ]
            )
        ]
        
        # Add additional tags if provided
        if tags:
            for tag in tags:
                steps[0].args.extend(["-t", f"gcr.io/{self.project_id}/{image_name}:{tag}"])
        
        # Set up images
        images = [f"gcr.io/{self.project_id}/{image_name}"]
        if tags:
            for tag in tags:
                images.append(f"gcr.io/{self.project_id}/{image_name}:{tag}")
        
        # Set up timeout
        timeout = duration_pb2.Duration()
        timeout.FromSeconds(timeout_minutes * 60)
        
        # Create build config
        build_config = cloudbuild_v1.Build(
            source=source,
            steps=steps,
            images=images,
            timeout=timeout,
            options=cloudbuild_v1.BuildOptions(
                machine_type=machine_type
            )
        )
        
        # Add substitutions if provided
        if substitutions:
            build_config.substitutions = substitutions
        
        return build_config
    
    def _wait_for_build(
        self,
        operation,
        timeout_minutes: int = 30
    ) -> str:
        """
        Wait for build to complete
        
        Args:
            operation: Build operation
            timeout_minutes: Maximum wait time in minutes
            
        Returns:
            str: Build ID
            
        Raises:
            CloudBuildTimeoutError: If build times out
            CloudBuildError: If build fails
        """
        start_time = time.time()
        timeout_seconds = timeout_minutes * 60
        
        while True:
            if time.time() - start_time > timeout_seconds:
                raise CloudBuildTimeoutError(f"Build timed out after {timeout_minutes} minutes")
            
            # Check if operation is done
            if operation.done():
                break
                
            time.sleep(10)  # Wait 10 seconds before checking again
        
        # Get the result
        result = operation.result()
        
        if result.status != cloudbuild_v1.Build.Status.SUCCESS:
            error_msg = f"Build failed with status: {result.status}"
            if result.status_detail:
                error_msg += f" - {result.status_detail}"
            raise CloudBuildError(error_msg)
        
        return result.id 
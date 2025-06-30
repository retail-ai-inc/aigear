from __future__ import annotations
import os
from typing import Optional
from google.cloud import cloudbuild_v1
from google.auth import default
from google.auth.exceptions import DefaultCredentialsError
from .errors import CloudBuildAuthenticationError
from ...common.logger import logger


def get_cloud_build_client() -> cloudbuild_v1.CloudBuildClient:
    """
    Get authenticated Cloud Build client
    
    Returns:
        CloudBuildClient: Authenticated Cloud Build client
        
    Raises:
        CloudBuildAuthenticationError: If authentication fails
    """
    try:
        # Try to get default credentials
        credentials, project = default()
        
        if not project:
            # Try to get project from environment variable
            project = os.getenv('GOOGLE_CLOUD_PROJECT')
            if not project:
                raise CloudBuildAuthenticationError(
                    "No Google Cloud project found. Set GOOGLE_CLOUD_PROJECT environment variable."
                )
        
        logger.info(f"Using Google Cloud project: {project}")
        
        # Create Cloud Build client
        client = cloudbuild_v1.CloudBuildClient(credentials=credentials)
        return client
        
    except DefaultCredentialsError as e:
        raise CloudBuildAuthenticationError(
            f"Failed to authenticate with Google Cloud: {e}"
        ) from e
    except Exception as e:
        raise CloudBuildAuthenticationError(
            f"Unexpected error during authentication: {e}"
        ) from e


def get_project_id() -> str:
    """
    Get the current Google Cloud project ID
    
    Returns:
        str: Project ID
        
    Raises:
        CloudBuildAuthenticationError: If project ID cannot be determined
    """
    try:
        _, project = default()
        if project:
            return project
        
        project = os.getenv('GOOGLE_CLOUD_PROJECT')
        if project:
            return project
            
        raise CloudBuildAuthenticationError(
            "No Google Cloud project found. Set GOOGLE_CLOUD_PROJECT environment variable."
        )
    except Exception as e:
        raise CloudBuildAuthenticationError(
            f"Failed to get project ID: {e}"
        ) from e 
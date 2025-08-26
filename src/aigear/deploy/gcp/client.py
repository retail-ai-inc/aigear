from __future__ import annotations
import os
from typing import Optional, Dict, Any
from google.cloud.devtools import cloudbuild_v1
from google.auth import default
from google.auth.exceptions import DefaultCredentialsError
from .errors import CloudBuildAuthenticationError
from ...common.logger import logger


class CloudBuildClientFactory:
    """
    Factory Pattern: Create appropriate Cloud Build client for different environments
    Also implements singleton pattern to cache client instances
    """
    
    _instances: Dict[str, cloudbuild_v1.CloudBuildClient] = {}
    _project_cache: Optional[str] = None
    
    @classmethod
    def get_client(
        cls, 
        project_id: Optional[str] = None, 
        credentials_path: Optional[str] = None
    ) -> cloudbuild_v1.CloudBuildClient:
        """
        Get Cloud Build client (singleton pattern)
        
        Args:
            project_id: Google Cloud project ID
            credentials_path: Path to service account key file
            
        Returns:
            CloudBuildClient: Authenticated Cloud Build client
            
        Raises:
            CloudBuildAuthenticationError: If authentication fails
        """
        # Use project_id and credentials_path as cache key
        cache_key = f"{project_id or 'default'}_{credentials_path or 'default'}"
        
        if cache_key not in cls._instances:
            cls._instances[cache_key] = cls._create_client(project_id, credentials_path)
            
        return cls._instances[cache_key]
    
    @classmethod
    def _create_client(
        cls, 
        project_id: Optional[str] = None, 
        credentials_path: Optional[str] = None
    ) -> cloudbuild_v1.CloudBuildClient:
        """Create Cloud Build client"""
        try:
            if credentials_path:
                # Use specified service account key
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_path
            
            # Get default credentials
            credentials, detected_project = default()
            
            # Determine project ID
            final_project = project_id or detected_project or cls._get_project_from_env()
            
            if not final_project:
                raise CloudBuildAuthenticationError(
                    "No Google Cloud project found. Set GOOGLE_CLOUD_PROJECT environment variable "
                    "or provide project_id parameter."
                )
            
            # Cache project ID
            cls._project_cache = final_project
            
            logger.info(f"Using Google Cloud project: {final_project}")
            
            # Create Cloud Build client
            return cloudbuild_v1.CloudBuildClient(credentials=credentials)
            
        except DefaultCredentialsError as e:
            raise CloudBuildAuthenticationError(
                f"Failed to authenticate with Google Cloud: {e}\n"
                f"Make sure you have:\n"
                f"1. Run 'gcloud auth application-default login'\n"
                f"2. Set GOOGLE_APPLICATION_CREDENTIALS environment variable\n"
                f"3. Have proper IAM roles (Cloud Build Editor)"
            ) from e
        except Exception as e:
            raise CloudBuildAuthenticationError(
                f"Unexpected error during authentication: {e}"
            ) from e
    
    @classmethod
    def get_project_id(cls) -> str:
        """
        Get current Google Cloud project ID
        
        Returns:
            str: Project ID
            
        Raises:
            CloudBuildAuthenticationError: If project ID cannot be determined
        """
        if cls._project_cache:
            return cls._project_cache
            
        try:
            _, project = default()
            if project:
                cls._project_cache = project
                return project
            
            project = cls._get_project_from_env()
            if project:
                cls._project_cache = project
                return project
                
            raise CloudBuildAuthenticationError(
                "No Google Cloud project found. Set GOOGLE_CLOUD_PROJECT environment variable."
            )
        except Exception as e:
            raise CloudBuildAuthenticationError(
                f"Failed to get project ID: {e}"
            ) from e
    
    @staticmethod
    def _get_project_from_env() -> Optional[str]:
        """Get project ID from environment variables"""
        return (
            os.getenv('GOOGLE_CLOUD_PROJECT') or 
            os.getenv('GCLOUD_PROJECT') or 
            os.getenv('GCP_PROJECT')
        )
    
    @classmethod
    def clear_cache(cls):
        """Clear client cache (for testing or reinitialization)"""
        cls._instances.clear()
        cls._project_cache = None


# Maintain backward compatible function interface
def get_cloud_build_client(project_id: Optional[str] = None) -> cloudbuild_v1.CloudBuildClient:
    """
    Get authenticated Cloud Build client
    
    Returns:
        CloudBuildClient: Authenticated Cloud Build client
        
    Raises:
        CloudBuildAuthenticationError: If authentication fails
    """
    return CloudBuildClientFactory.get_client(project_id)


def get_project_id() -> str:
    """
    Get the current Google Cloud project ID
    
    Returns:
        str: Project ID
        
    Raises:
        CloudBuildAuthenticationError: If project ID cannot be determined
    """
    return CloudBuildClientFactory.get_project_id() 
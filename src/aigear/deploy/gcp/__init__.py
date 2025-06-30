"""
Google Cloud Platform deployment utilities for aigear.

This module provides functionality for automated Cloud Build operations,
including building Docker images, managing builds, and deploying to GCP.
"""

from .builder import CloudBuildBuilder
from .client import get_cloud_build_client, get_project_id
from .utilities import (
    upload_source_to_gcs,
    create_cloudbuild_yaml,
    validate_cloudbuild_config,
    get_gcs_bucket_name
)
from .errors import (
    CloudBuildError,
    CloudBuildConfigError,
    CloudBuildAuthenticationError,
    CloudBuildTimeoutError
)

__all__ = [
    # Main builder class
    "CloudBuildBuilder",
    
    # Client functions
    "get_cloud_build_client",
    "get_project_id",
    
    # Utility functions
    "upload_source_to_gcs",
    "create_cloudbuild_yaml",
    "validate_cloudbuild_config",
    "get_gcs_bucket_name",
    
    # Error classes
    "CloudBuildError",
    "CloudBuildConfigError",
    "CloudBuildAuthenticationError",
    "CloudBuildTimeoutError",
]

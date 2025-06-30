# GCP Cloud Build Integration

This directory contains the Google Cloud Platform Cloud Build integration for aigear, providing automated Docker image building and deployment capabilities.

## Features

- **Automated Cloud Build**: Build Docker images using Google Cloud Build
- **GCS Integration**: Upload source code to Google Cloud Storage
- **Build Management**: Monitor and manage Cloud Build operations
- **Configuration Generation**: Create and validate cloudbuild.yaml files
- **Error Handling**: Comprehensive error handling and logging

## Prerequisites

1. **Google Cloud Project**: You need a Google Cloud project with Cloud Build API enabled
2. **Authentication**: Set up authentication using one of these methods:
   - Service account key file
   - Application Default Credentials (ADC)
   - gcloud CLI authentication

3. **Required APIs**: Enable the following APIs in your Google Cloud project:
   - Cloud Build API
   - Cloud Storage API
   - Container Registry API (if using gcr.io)

## Installation

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

2. Set up authentication:
```bash
# Option 1: Using gcloud CLI
gcloud auth application-default login

# Option 2: Using service account key
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"

# Option 3: Set project ID
export GOOGLE_CLOUD_PROJECT="your-project-id"
```

## Usage

### Basic Example

```python
from aigear.deploy.gcp import CloudBuildBuilder
from pathlib import Path

# Initialize builder
builder = CloudBuildBuilder()

# Build from GCS source
build_id = builder.build_from_gcs(
    gcs_source="gs://your-bucket/source.tar.gz",
    image_name="my-app",
    dockerfile="Dockerfile",
    timeout_minutes=15,
    tags=["latest", "v1.0.0"]
)

print(f"Build completed: {build_id}")
```

### Upload and Build

```python
from aigear.deploy.gcp import (
    CloudBuildBuilder,
    upload_source_to_gcs,
    get_gcs_bucket_name,
    get_project_id
)
from pathlib import Path

# Get project and bucket
project_id = get_project_id()
bucket_name = get_gcs_bucket_name(project_id)

# Upload source code
source_path = Path(".")
gcs_uri = upload_source_to_gcs(
    source_path=source_path,
    bucket_name=bucket_name,
    object_name="source.tar.gz"
)

# Build from uploaded source
builder = CloudBuildBuilder()
build_id = builder.build_from_gcs(
    gcs_source=gcs_uri,
    image_name="my-app"
)
```

### Create Configuration File

```python
from aigear.deploy.gcp import create_cloudbuild_yaml, validate_cloudbuild_config
from pathlib import Path

# Create cloudbuild.yaml
config_path = Path("cloudbuild.yaml")
create_cloudbuild_yaml(
    output_path=config_path,
    image_name="my-app",
    dockerfile="Dockerfile",
    substitutions={
        "VERSION": "1.0.0",
        "ENVIRONMENT": "production"
    }
)

# Validate configuration
is_valid = validate_cloudbuild_config(config_path)
```

### Monitor Builds

```python
from aigear.deploy.gcp import CloudBuildBuilder

builder = CloudBuildBuilder()

# List recent builds
builds = builder.list_builds(page_size=10)
for build in builds:
    print(f"{build.id}: {build.status}")

# Get specific build status
build_status = builder.get_build_status("build-id")
print(f"Status: {build_status.status}")
```

## Configuration

### Environment Variables

- `GOOGLE_CLOUD_PROJECT`: Your Google Cloud project ID
- `GOOGLE_APPLICATION_CREDENTIALS`: Path to service account key file

### Build Options

- `timeout_minutes`: Build timeout (default: 10)
- `machine_type`: Cloud Build machine type (default: E2_HIGHCPU_8)
- `tags`: Additional image tags
- `substitutions`: Build substitutions for variables

## Error Handling

The module provides specific error classes:

- `CloudBuildError`: General Cloud Build errors
- `CloudBuildConfigError`: Configuration errors
- `CloudBuildAuthenticationError`: Authentication errors
- `CloudBuildTimeoutError`: Build timeout errors

## Running the Example

```bash
# Run the example script
python cloudbuild_example.py
```

## File Structure

```
gcp/
├── __init__.py              # Module exports
├── builder.py               # Main CloudBuildBuilder class
├── client.py                # Authentication and client setup
├── errors.py                # Error classes
├── utilities.py             # Utility functions
├── cloudbuild_example.py    # Usage examples
├── requirements.txt         # Dependencies
└── README.md               # This file
```

## Integration with aigear

This Cloud Build integration is designed to work seamlessly with the existing aigear framework:

- Follows the same error handling patterns as other aigear modules
- Uses the common logger for consistent logging
- Integrates with the existing deployment architecture
- Supports the same configuration patterns

## Troubleshooting

### Common Issues

1. **Authentication Errors**: Ensure you have proper Google Cloud authentication set up
2. **Project Not Found**: Set the `GOOGLE_CLOUD_PROJECT` environment variable
3. **API Not Enabled**: Enable the required Google Cloud APIs
4. **Permission Errors**: Ensure your service account has the necessary permissions

### Required Permissions

Your service account needs the following roles:
- Cloud Build Service Account
- Storage Object Admin (for GCS operations)
- Container Registry Service Agent (for image pushing)

## Contributing

When contributing to this module:

1. Follow the existing code style and patterns
2. Add appropriate error handling
3. Include logging for important operations
4. Update this README for new features
5. Add tests for new functionality 
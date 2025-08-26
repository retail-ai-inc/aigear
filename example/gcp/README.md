# GCP Cloud Build Integration

This directory contains the Google Cloud Platform Cloud Build integration for aigear, providing automated Docker image building and deployment capabilities.

## Features

- **Automated Cloud Build**: Build Docker images using Google Cloud Build
- **GCS Integration**: Upload source code to Google Cloud Storage
- **GitHub Integration**: Build directly from GitHub repositories (public/private)
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

### Build from GitHub (recommended)

Build an image directly from a GitHub repository. For private repos, configure access (e.g., GitHub App, deploy key, or token via Cloud Build secret) and adapt the git clone step.

```python
from aigear.deploy.gcp import CloudBuildBuilder

builder = CloudBuildBuilder()

build_id = builder.build_from_github(
    repo_url="https://github.com/your-org/your-repo.git",
    image_name="your-app",
    branch="main",                 # or commit_sha="<sha>"
    context_dir=".",               # subdirectory within repo used as build context
    dockerfile="Dockerfile",       # path relative to context_dir
    timeout_minutes=15,
    tags=["latest", "v1.0.0"],
    build_args={"ENV": "prod"},
)
```

Notes:
- For private repositories, add an auth step before cloning (e.g., configure `git` to use a token from Secret Manager) or set up a GitHub App connection.
- Alternatively, use Cloud Build GitHub App triggers for first-class GitHub integration.

### Build from GCS

```python
from aigear.deploy.gcp import CloudBuildBuilder

builder = CloudBuildBuilder()
build_id = builder.build_from_gcs(
    gcs_source="gs://your-bucket/source.tar.gz",
    image_name="my-app",
)
```

### Upload local directory to GCS and build

```python
from aigear.deploy.gcp import (
  CloudBuildBuilder, upload_source_to_gcs, get_gcs_bucket_name, get_project_id
)
from pathlib import Path

project_id = get_project_id()
bucket_name = get_gcs_bucket_name(project_id)
gcs_uri = upload_source_to_gcs(Path("."), bucket_name)

builder = CloudBuildBuilder()
build_id = builder.build_from_gcs(gcs_source=gcs_uri, image_name="my-app")
```

### Create cloudbuild.yaml

```python
from aigear.deploy.gcp import create_cloudbuild_yaml, validate_cloudbuild_config
from pathlib import Path

config_path = Path("cloudbuild.yaml")
create_cloudbuild_yaml(output_path=config_path, image_name="my-app")
assert validate_cloudbuild_config(config_path)
```

### List and inspect builds

```python
from aigear.deploy.gcp import CloudBuildBuilder

builder = CloudBuildBuilder()
for b in builder.list_builds(page_size=10):
    print(b.id, b.status)
```

## Permissions

- Cloud Build Service Account
- Storage Object Admin (for GCS operations)
- Container Registry/Artifact Registry permissions to push images
- If cloning private GitHub repos: Secret Manager Accessor (for tokens) or GitHub App integration

## Troubleshooting

- Ensure `google-cloud-build`, `google-auth` are installed.
- For private repos, verify that credentials are available to the build steps.
- Use Cloud Build logs to diagnose git clone issues or docker build failures. 
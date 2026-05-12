"""
Shared constants for virtual environment configuration.

VENV_BASE_DIR is the single source of truth for the base directory under which
all virtual environments are created inside Docker images. It is referenced by:

  - Dockerfile.pl / Dockerfile.ms
        uv venv /opt/venv/<name> --python <version>

  - env.json  (per-pipeline, per-image)
        pipelines:
          <pipeline_name>:
            venv_pl: "<name>"              # training steps venv name
            model_service:
              venv_ms: "<name>"            # model service venv name

  - deploy/gcp/artifacts_image.py  (_validate_dockerfile_venvs)
        Checks that every venv_pl / venv_ms configured in env.json is actually
        created in the corresponding Dockerfile before the image build starts.

  - deploy/common/helm_chart.py
        Constructs the grpc command path:
        {VENV_BASE_DIR}/{venv_ms}/bin/aigear-grpc

  - infrastructure/gcp/function/index.js  (VENVBASEDIR placeholder)
        Replaced at deploy time by CloudFunction._copy_function_files():
        VENVBASEDIR/{venv}/bin/aigear-task workflow ...

Venv names (e.g. "pl", "ms", "ape3") are NOT constants — they are defined per
pipeline in env.json and must match the directories created in the Dockerfile.
Each pipeline can use a different venv, and a single Dockerfile may create
multiple venvs to serve multiple pipelines.
"""

VENV_BASE_DIR = "/opt/venv"

# Deployment environment names — used by model_service CLI, helm_chart, and grpc deploy functions.
ENV_LOCAL = "local"
ENV_STAGING = "staging"
ENV_PRODUCTION = "production"

# Standard Dockerfile names — used by artifacts_image CLI and project initialisation.
DOCKERFILE_PIPELINE = "Dockerfile.pl"
DOCKERFILE_SERVICE = "Dockerfile.ms"

"""
aigear: internal AI tooling for pipelines.

Top-level public API (keep small & stable):
- __version__: package version string
- logger: configured global logger
- state, StateType: workflow state helpers
- stable_hash, file_hash: hashing utilities
- workflow, WorkFlow: pipeline construction & execution
- task, Task, TaskRunner: task-level building blocks

Optional features (import succeeds even if deps are missing; calling them
without deps installed raises a helpful ImportError):
- Docker helpers:
    ImageBuilder, build_image, default_dockerfile, default_requirements,
    docker_client, silence_docker_warnings, Container,
    run_or_restart_container, stream_logs, flow_path_in_workdir,
    BuildError, PushError
- GCP Pub/Sub:
    PubSubClient
- gRPC helpers:
    GrpcClient, run_grpc_service
- Reader & Saver utilities:
    get_help, PickleModel
"""

from typing import Any
from ._version import __version__
from .project import Project
from .common.logger import logger
from .common.state import state, StateType
from .common.hashing import stable_hash, file_hash
from .common.errors import CallException, ParameterBindError

# --- Core pipeline API (required) -------------------------------------------
from .pipeline.pipeline import workflow, WorkFlow
from .pipeline.task import task, Task
from .pipeline.executor import TaskRunner


# --- Optional feature helper ------------------------------------------------
def _optional_feature_error(pkgs: str, feature: str) -> None:
    raise ImportError(
        f"`{feature}` requires additional dependency/dependencies: {pkgs}. "
        f"Please install them (e.g., `pip install {pkgs}`) or use an `aigear[...]` extra if provided."
    )


# --- Docker (optional: requires `docker`) -----------------------------------
try:
    from .deploy.docker.builder import (
        ImageBuilder,
        build_image,
        default_dockerfile,
        default_requirements,
    )
    from .deploy.docker.client import docker_client, silence_docker_warnings
    from .deploy.docker.container import (
        Container,
        run_or_restart_container,
        stream_logs,
    )
    from .deploy.docker.utilities import flow_path_in_workdir
    from .deploy.docker.errors import BuildError, PushError
except Exception:
    ImageBuilder = None  # type: ignore

    def build_image(*_: Any, **__: Any):
        _optional_feature_error("docker", "build_image")

    def default_dockerfile(*_: Any, **__: Any):
        _optional_feature_error("docker", "default_dockerfile")

    def default_requirements(*_: Any, **__: Any):
        _optional_feature_error("docker", "default_requirements")

    def docker_client(*_: Any, **__: Any):
        _optional_feature_error("docker", "docker_client")

    def silence_docker_warnings(*_: Any, **__: Any):
        _optional_feature_error("docker", "silence_docker_warnings")

    class Container:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            _optional_feature_error("docker", "Container")

    def run_or_restart_container(*_: Any, **__: Any):
        _optional_feature_error("docker", "run_or_restart_container")

    def stream_logs(*_: Any, **__: Any):
        _optional_feature_error("docker", "stream_logs")

    def flow_path_in_workdir(*_: Any, **__: Any):
        _optional_feature_error("docker", "flow_path_in_workdir")

    class BuildError(Exception):  # type: ignore
        pass

    class PushError(Exception):  # type: ignore
        pass


# --- GCP Pub/Sub (optional: requires `google-cloud-pubsub`) -----------------
try:
    from .deploy.gcp.pubsub import PubSubClient
except Exception:
    class PubSubClient:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            _optional_feature_error("google-cloud-pubsub", "PubSubClient")


# --- gRPC (optional: requires `grpcio`, `google.protobuf`; server also needs
#           `grpcio-health-checking` and optionally `sentry-sdk`) ------------
GrpcClient = None  # type: ignore

# Try plural package path first (matches earlier tree), then singular
try:
    from .microservices.grpc.client import MlgrpcClient as GrpcClient  # type: ignore
except Exception:
    try:
        from .microservice.grpc.client import MlgrpcClient as GrpcClient  # type: ignore
    except Exception:
        def _grpc_client_placeholder(*_: Any, **__: Any):
            _optional_feature_error("grpcio, google.protobuf", "GrpcClient")
        class GrpcClient:  # type: ignore
            def __init__(self, *args: Any, **kwargs: Any):
                _grpc_client_placeholder()

# Server entrypoint alias
def run_grpc_service(*args: Any, **kwargs: Any):
    # Prefer plural path, then singular
    try:
        from .microservices.grpc.service import main as _srv_main  # type: ignore
    except Exception:
        try:
            from .microservice.grpc.service import main as _srv_main  # type: ignore
        except Exception:
            _optional_feature_error(
                "grpcio, grpcio-health-checking, google.protobuf, sentry-sdk (optional)",
                "run_grpc_service",
            )
    return _srv_main(*args, **kwargs)  # type: ignore


# --- Reader / Saver utilities (light) ---------------------------------------
# Reader helper
try:
    from .reader.help import get_help
except Exception:
    def get_help() -> None:  # type: ignore
        print("Help unavailable: `aigear.reader` not installed correctly.")

# Pickle-based model saver/loader
try:
    from .saver.cloudpickle import PickleModel
except Exception:
    class PickleModel:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            _optional_feature_error("cloudpickle", "PickleModel")


# --- Local registry (optional: SQLAlchemy/Tabulate/Cloudpickle) ------------
try:
    from .manage.local.model_manager import ModelManager
    from .manage.local.pipeline_manager import PipelineManager
except Exception:
    class ModelManager:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            _optional_feature_error("sqlalchemy, tabulate, cloudpickle", "ModelManager")

    class PipelineManager:  # type: ignore
        def __init__(self, *args: Any, **kwargs: Any):
            _optional_feature_error("sqlalchemy, tabulate", "PipelineManager")


__all__ = [
    # version
    "__version__",
    "Project",
    # core utilities
    "logger",
    "state",
    "StateType",
    "stable_hash",
    "file_hash",
    "CallException",
    "ParameterBindError",
    # pipeline API
    "workflow",
    "WorkFlow",
    "task",
    "Task",
    "TaskRunner",
    # docker (optional)
    "ImageBuilder",
    "build_image",
    "default_dockerfile",
    "default_requirements",
    "docker_client",
    "silence_docker_warnings",
    "Container",
    "run_or_restart_container",
    "stream_logs",
    "flow_path_in_workdir",
    "BuildError",
    "PushError",
    # gcp pubsub (optional)
    "PubSubClient",
    # grpc (optional)
    "GrpcClient",
    "run_grpc_service",
    # reader/saver
    "get_help",
    "PickleModel",
    # local registry (optional)
    "ModelManager",
    "PipelineManager",
]

import warnings
from contextlib import contextmanager
from typing import (
    Generator,
)


@contextmanager
def silence_docker_warnings() -> Generator[None, None, None]:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="distutils Version classes are deprecated.*",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="The distutils package is deprecated and slated for removal.*",
            category=DeprecationWarning,
        )
        yield


with silence_docker_warnings():
    import docker
    from docker import DockerClient
    from docker.errors import DockerException, APIError, ImageNotFound, NotFound


@contextmanager
def docker_client() -> Generator["DockerClient", None, None]:
    """Get the environmentally-configured Docker client"""
    client = None
    try:
        with silence_docker_warnings():
            client = docker.DockerClient.from_env()
            yield client
    except DockerException as exc:
        raise RuntimeError(
            "This error is often thrown because Docker is not running. Please ensure Docker is running."
        ) from exc
    finally:
        client is not None and client.close()

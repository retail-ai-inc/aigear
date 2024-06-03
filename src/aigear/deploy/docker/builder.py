import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import (
    Generator,
    Optional,
    TextIO,
)
import aigear


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
    from docker.errors import DockerException


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


class BuildError(Exception):
    """Raised when a Docker build fails"""


# Labels to apply to all images built with Prefect
IMAGE_LABELS = {
    "io.aigear.version": aigear.__version__,
}


def build_image(
        context: Path,
        dockerfile: str = "Dockerfile",
        tag: Optional[str] = None,
        pull: bool = False,
        platform: str = None,
        stream_progress_to: Optional[TextIO] = None,
        **kwargs,
) -> str:
    """Builds a Docker image, returning the image ID

    Args:
        context: the root directory for the Docker build context
        dockerfile: the path to the Dockerfile, relative to the context
        tag: the tag to give this image
        pull: True to pull the base image during the build
        stream_progress_to: an optional stream (like sys.stdout, or an io.TextIO) that
            will collect the build output as it is reported by Docker

    Returns:
        The image ID
    """

    if not context:
        raise ValueError("context required to build an image")

    if not Path(context).exists():
        raise ValueError(f"Context path {context} does not exist")

    kwargs = {key: kwargs[key] for key in kwargs if key not in ["decode", "labels"]}

    image_id = None
    with docker_client() as client:
        events = client.api.build(
            path=str(context),
            tag=tag,
            dockerfile=dockerfile,
            pull=pull,
            decode=True,
            labels=IMAGE_LABELS,
            platform=platform,
            **kwargs,
        )

        try:
            for event in events:
                if "stream" in event:
                    if not stream_progress_to:
                        continue
                    stream_progress_to.write(event["stream"])
                    stream_progress_to.flush()
                elif "aux" in event:
                    image_id = event["aux"]["ID"]
                elif "error" in event:
                    raise BuildError(event["error"])
                elif "message" in event:
                    raise BuildError(event["message"])
        except docker.errors.APIError as e:
            raise BuildError(e.explanation) from e

    assert image_id, "The Docker daemon did not return an image ID"
    return image_id

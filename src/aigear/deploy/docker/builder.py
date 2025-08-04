from __future__ import annotations
import sys
import subprocess
from pathlib import Path
from typing import (
    Optional,
    TextIO,
)
from .client import docker_client, silence_docker_warnings
from .errors import BuildError
from ..._version import __version__
from ...common.logger import logger

with silence_docker_warnings():
    from docker.errors import APIError, ImageNotFound


class ImageBuilder:
    @staticmethod
    def build(
        image_path: Path = None,
        dockerfile: str = "Dockerfile",
        tag: Optional[str] = None,
        pull: bool = False,
        platform: str = None,
        stream_progress_to: Optional[TextIO] = None,
        **kwargs
    ):
        if not image_path:
            image_path = Path.cwd()
        logger.info(f"Create Docker Image from {image_path}.")

        if stream_progress_to is None:
            stream_progress_to = sys.stdout

        # If requirements.txt and dockerfile do not exist, the default will be used
        default_requirements(image_path)
        default_dockerfile(image_path)

        # create docker image
        image_id = build_image(
            image_path=image_path,
            dockerfile=dockerfile,
            tag=tag,
            pull=pull,
            platform=platform,
            stream_progress_to=stream_progress_to,
            **kwargs,
        )
        logger.info("Docker image creation completed.")
        return image_id

    def push(self):
        pass

    @staticmethod
    def get_image_id(tag: str):
        try:
            with docker_client() as client:
                image = client.images.get(tag)
            return image.id
        except ImageNotFound:
            logger.info('Image not found.')


def build_image(
    image_path: Path,
    dockerfile: str = "Dockerfile",
    tag: Optional[str] = None,
    pull: bool = False,
    platform: str = None,
    stream_progress_to: Optional[TextIO] = None,
    **kwargs,
) -> str:
    """Builds a Docker image, returning the image ID

    Args:
        image_path: the root directory for the Docker build context
        dockerfile: the path to the Dockerfile, relative to the context
        tag: the tag to give this image
        pull: True to pull the base image during the build
        platform (str): Platform in the format ``os[/arch[/variant]]``
        stream_progress_to: an optional stream (like sys.stdout, or an io.TextIO) that
            will collect the build output as it is reported by Docker
        fileobj: A file object to use as the Dockerfile. (Or a file-like
            object)
        quiet (bool): Whether to return the status
        nocache (bool): Don't use the cache when set to ``True``
        rm (bool): Remove intermediate containers. The ``docker build``
            command now defaults to ``--rm=true``, but we have kept the old
            default of `False` to preserve backward compatibility
        timeout (int): HTTP timeout
        custom_context (bool): Optional if using ``fileobj``
        encoding (str): The encoding for a stream. Set to ``gzip`` for
            compressing
        forcerm (bool): Always remove intermediate containers, even after
            unsuccessful builds
        buildargs (dict): A dictionary of build arguments
        container_limits (dict): A dictionary of limits applied to each
            container created by the build process. Valid keys:

            - memory (int): set memory limit for build
            - memswap (int): Total memory (memory + swap), -1 to disable
                swap
            - cpushares (int): CPU shares (relative weight)
            - cpusetcpus (str): CPUs in which to allow execution, e.g.,
                ``"0-3"``, ``"0,1"``
        shmsize (int): Size of `/dev/shm` in bytes. The size must be
            greater than 0. If omitted the system uses 64MB
        labels (dict): A dictionary of labels to set on the image
        cache_from (list): A list of images used for build cache
            resolution
        target (str): Name of the build-stage to build in a multi-stage
            Dockerfile
        network_mode (str): networking mode for the run commands during
            build
        squash (bool): Squash the resulting images layers into a
            single layer.
        extra_hosts (dict): Extra hosts to add to /etc/hosts in building
            containers, as a mapping of hostname to IP address.
        isolation (str): Isolation technology used during build.
            Default: `None`.
        use_config_proxy (bool): If ``True``, and if the docker client
            configuration file (``~/.docker/config.json`` by default)
            contains a proxy configuration, the corresponding environment
            variables will be set in the container being built.

    Returns:
        The image ID
    """

    if not image_path:
        raise ValueError("image_path required to build an image")

    if not Path(image_path).exists():
        raise ValueError(f"Context path {image_path} does not exist")

    kwargs = {key: kwargs[key] for key in kwargs if key not in ["decode", "labels"]}
    image_labels = {
        "io.aigear.version": __version__,
    }

    image_id = None
    with docker_client() as client:
        events = client.api.build(
            path=image_path.as_posix(),
            tag=tag,
            dockerfile=dockerfile,
            pull=pull,
            decode=True,
            labels=image_labels,
            platform=platform,
            rm=True,
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
        except APIError as e:
            raise BuildError(e.explanation) from e

    assert image_id, "The Docker daemon did not return an image ID"
    return image_id


def default_dockerfile(
    image_path: Optional[Path] = None,
    base_image: str = None,
    package_source: str = None,
):
    if not image_path:
        raise ValueError("image_path required to build an image")

    if (image_path / "Dockerfile").exists():
        return

    lines = []
    if base_image is None:
        base_image = f"python:{sys.version_info.major}.{sys.version_info.minor}"
    lines.append(f"FROM {base_image}")

    dir_name = image_path.name
    workdir = f"/aigear/{dir_name}"

    lines.append(f"WORKDIR {workdir}/")
    lines.append(f"COPY . {workdir}/")

    if package_source is None:
        package_source = ""
    else:
        package_source = " -i " + package_source

    lines.append(f"COPY requirements.txt {workdir}/requirements.txt")
    lines.append(f"RUN python -m pip install --upgrade pip{package_source}")
    lines.append(
        f"RUN python -m pip install -r {workdir}/requirements.txt{package_source}"
    )
    lines.append(
        f"""
        ENV PYTHONDONTWRITEBYTECODE=1 \
        PYTHONBUFFERED=1
        """
    )

    with Path("Dockerfile").open("w") as f:
        f.writelines(line + "\n" for line in lines)


def default_requirements(
    image_path: Optional[Path] = None,
):
    if not image_path:
        raise ValueError("image_path required to build an image")

    if (image_path / "requirements.txt").exists():
        return

    try:
        command = ['pipreqs', image_path, '--use-local', '--encoding', 'UTF-8']
        subprocess.run(command, check=True, text=True, capture_output=True)
        print("Automatically generate ./requirements.txt. But it's not tidy.")
    except subprocess.CalledProcessError as e:
        print("Error occurred while running command:", e.stderr)

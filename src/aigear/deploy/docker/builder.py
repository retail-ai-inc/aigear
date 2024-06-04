import hashlib
import os
import shutil
import sys
import subprocess
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import (
    Iterable,
    List,
    Optional,
    TextIO,
    Type,
    Union,
)
from typing_extensions import Self
from aigear import __version__
from .client import docker_client, APIError
from .errors import BuildError


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
    image_labels = {
        "io.aigear.version": __version__,
    }

    image_id = None
    with docker_client() as client:
        events = client.api.build(
            path=context.as_posix(),
            tag=tag,
            dockerfile=dockerfile,
            pull=pull,
            decode=True,
            labels=image_labels,
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
        except APIError as e:
            raise BuildError(e.explanation) from e

    assert image_id, "The Docker daemon did not return an image ID"
    return image_id


def default_dockerfile(
        context: Optional[Path] = None,
        base_image: str = None,
        package_source: str = None
):
    if not context:
        context = Path.cwd()

    if (context / "Dockerfile").exists():
        return

    lines = []
    if base_image is None:
        base_image = f"python:{sys.version_info.major}.{sys.version_info.minor}"
    lines.append(f"FROM {base_image}")

    dir_name = context.name
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

    with Path("Dockerfile").open("w") as f:
        f.writelines(line + "\n" for line in lines)


def default_requirements(
        context: Optional[Path] = None,
):
    if not context:
        context = Path.cwd()

    if (context / "requirements.txt").exists():
        return

    try:
        command = ['pipreqs', context, '--use-local', '--encoding', 'UTF-8']
        subprocess.run(command, check=True, text=True, capture_output=True)
        print("Automatically generate ./requirements.txt. But it's not tidy.")
    except subprocess.CalledProcessError as e:
        print("Error occurred while running command:", e.stderr)

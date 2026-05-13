import re
from pathlib import Path

from aigear.common import run_sh, run_sh_stream
from aigear.common.config import AigearConfig, AppConfig
from aigear.common.constant import VENV_BASE_DIR
from aigear.common.image import get_image_path
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class LocalImage:
    def __init__(self, image_path: str):
        self.image_path = image_path
        self._image_name = image_path.rsplit(":", 1)[0]

    def build(self, dockerfile_path=None, build_context=".") -> bool:
        if dockerfile_path is None:
            logger.info(
                "Please specify Dockerfile (Dockerfile.pl or Dockerfile.ms) to build the image."
            )
            return False
        command = [
            "docker",
            "build",
            "-f",
            dockerfile_path,
            "-t",
            self.image_path,
            build_context,
        ]
        return run_sh_stream(command) == 0

    def tag(self, src_tag: str, target_tag: str) -> bool:
        command = [
            "docker",
            "tag",
            f"{self._image_name}:{src_tag}",
            f"{self._image_name}:{target_tag}",
        ]
        return run_sh_stream(command) == 0

    def remove(self) -> bool:
        return run_sh_stream(["docker", "rmi", self.image_path]) == 0

    def clear_all(self) -> bool:
        result = run_sh(["docker", "images", self._image_name, "-q"])
        image_ids = result.strip().splitlines()
        if not image_ids:
            logger.info(f"No local images found for '{self._image_name}'.")
            return True
        return run_sh_stream(["docker", "rmi"] + image_ids) == 0


class RegistryImage:
    def __init__(self, image_path: str):
        self.image_path = image_path
        self._image_name = image_path.rsplit(":", 1)[0]

    def configure_auth(self, location: str) -> None:
        event = run_sh(
            [
                "gcloud",
                "auth",
                "configure-docker",
                f"{location}-docker.pkg.dev",
                "--quiet",
            ]
        )
        logger.info(event)

    def push(self) -> bool:
        return run_sh_stream(["docker", "push", self.image_path]) == 0

    def exists(self) -> bool:
        event = run_sh(
            ["gcloud", "artifacts", "docker", "images", "describe", self.image_path]
        )
        logger.info(event)
        return not (
            ("Image not found" in event or "NOT_FOUND" in event) and "ERROR" in event
        )

    def delete(self) -> bool:
        result = run_sh(
            [
                "gcloud",
                "artifacts",
                "docker",
                "images",
                "delete",
                self.image_path,
                "--delete-tags",
                "--quiet",
            ]
        )
        logger.info(result)
        return "ERROR" not in result

    def clear_all(self) -> bool:
        result = run_sh(
            [
                "gcloud",
                "artifacts",
                "docker",
                "images",
                "delete",
                self._image_name,
                "--delete-tags",
                "--quiet",
            ]
        )
        logger.info(result)
        return "ERROR" not in result

    def retag(self, src_tag: str, target_tag: str) -> bool:
        result = run_sh(
            [
                "gcloud",
                "artifacts",
                "docker",
                "tags",
                "add",
                f"{self._image_name}:{src_tag}",
                f"{self._image_name}:{target_tag}",
            ]
        )
        logger.info(result)
        return "ERROR" not in result



def _validate_dockerfile_venvs(dockerfile_path: str, is_service: bool) -> None:
    """
    Two-stage validation before building a Docker image.

    Stage 1 — Base directory:
        Checks that VENV_BASE in the Dockerfile matches VENV_BASE_DIR in
        aigear/common/constant.py, ensuring the runtime path resolution
        (scheduler, helm chart, cloud function) stays in sync.

    Stage 2 — Venv existence:
        For each pipeline in env.json, checks that the configured venv name
        appears as ${VENV_BASE}/<name> in the Dockerfile.
        Pipeline image  (is_service=False): checks venv_pl per pipeline.
        Service image   (is_service=True):  checks model_service.venv_ms per pipeline.

    Raises ValueError on the first stage that fails.
    """
    content = Path(dockerfile_path).read_text(encoding="utf-8")

    # Stage 1: base directory must match constant.py
    expected_base_line = f"VENV_BASE={VENV_BASE_DIR}"
    if expected_base_line not in content:
        raise ValueError(
            f"{dockerfile_path}: VENV_BASE mismatch.\n  Expected: {expected_base_line}"
        )

    # Stage 2: each configured venv name must exist in the Dockerfile
    pipelines = AppConfig.pipelines()
    missing = []

    for version, pipeline_config in pipelines.items():
        if not isinstance(pipeline_config, dict):
            continue
        if not is_service:
            venv_pl = pipeline_config.get("venv_pl")
            if venv_pl and not re.search(
                r"\$\{VENV_BASE\}/" + re.escape(venv_pl) + r"(?=[^a-zA-Z0-9_-]|$)",
                content,
            ):
                missing.append(
                    f"pipeline '{version}' venv_pl '{venv_pl}' → ${{VENV_BASE}}/{venv_pl}"
                )
        else:
            venv_ms = pipeline_config.get("model_service", {}).get("venv_ms")
            if venv_ms and not re.search(
                r"\$\{VENV_BASE\}/" + re.escape(venv_ms) + r"(?=[^a-zA-Z0-9_-]|$)",
                content,
            ):
                missing.append(
                    f"pipeline '{version}' venv_ms '{venv_ms}' → ${{VENV_BASE}}/{venv_ms}"
                )

    if missing:
        raise ValueError(
            f"The following venvs are configured in env.json but not found in {dockerfile_path}:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


def create_artifacts_image(
    dockerfile_path=None,
    build_context=".",
    is_service=False,
    is_build=True,
    is_push=False,
) -> bool:
    """Returns True if the requested operations succeeded, False otherwise."""
    log_tag = "model service" if is_service else "pipeline"
    aigear_config = AigearConfig.get_config()
    image_path = get_image_path(is_service=is_service)
    local = LocalImage(image_path)
    registry = RegistryImage(image_path)

    if is_build:
        if dockerfile_path:
            _validate_dockerfile_venvs(dockerfile_path, is_service)
        if not local.build(
            dockerfile_path=dockerfile_path, build_context=build_context
        ):
            return False
        logger.info(f"The {log_tag} image has been created.")

    if is_push:
        registry.configure_auth(aigear_config.gcp.location)
        if not registry.push():
            return False
        logger.info(f"The {log_tag} image has been pushed.")
    return True


def delete_artifacts_image(is_service=False, is_push=False) -> bool:
    """Returns True if the requested operations succeeded, False otherwise."""
    log_tag = "model service" if is_service else "pipeline"
    aigear_config = AigearConfig.get_config()
    image_path = get_image_path(is_service=is_service)
    local = LocalImage(image_path)
    registry = RegistryImage(image_path)

    if not local.remove():
        return False
    logger.info(f"The {log_tag} local image has been deleted.")

    if is_push:
        registry.configure_auth(aigear_config.gcp.location)
        if not registry.delete():
            return False
        logger.info(f"The {log_tag} registry image has been deleted.")
    return True


def clear_artifacts_image(is_service=False, is_push=False) -> bool:
    """Remove all local (and optionally registry) images regardless of tag."""
    log_tag = "model service" if is_service else "pipeline"
    aigear_config = AigearConfig.get_config()
    image_path = get_image_path(is_service=is_service)
    local = LocalImage(image_path)
    registry = RegistryImage(image_path)

    if not local.clear_all():
        return False
    logger.info(f"All {log_tag} local images have been cleared.")

    if is_push:
        registry.configure_auth(aigear_config.gcp.location)
        if not registry.clear_all():
            return False
        logger.info(f"All {log_tag} registry images have been cleared.")
    return True


def retag_artifacts_image(
    src_tag: str, target_tag: str, is_service=False, is_push=False
) -> bool:
    """Returns True if the requested operations succeeded, False otherwise."""
    log_tag = "model service" if is_service else "pipeline"
    aigear_config = AigearConfig.get_config()
    image_path = get_image_path(is_service=is_service)
    local = LocalImage(image_path)
    registry = RegistryImage(image_path)

    if not local.tag(src_tag=src_tag, target_tag=target_tag):
        return False
    logger.info(
        f"The {log_tag} local image has been retagged {src_tag} -> {target_tag}."
    )

    if is_push:
        registry.configure_auth(aigear_config.gcp.location)
        if not registry.retag(src_tag=src_tag, target_tag=target_tag):
            return False
        logger.info(
            f"The {log_tag} registry image has been retagged {src_tag} -> {target_tag}."
        )
    return True



import re
from pathlib import Path

from aigear.common import run_sh, run_sh_stream
from aigear.common.config import AigearConfig, AppConfig
from aigear.common.constant import VENV_BASE_DIR
from aigear.common.image import get_image_path
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class ArtifactsImage:
    def __init__(self, artifacts_image):
        self.artifacts_image = artifacts_image

    def create_image(self, dockerfile_path=None, build_context="."):
        if dockerfile_path is None:
            logger.info("Please specify Dockerfile(Dockerfile.pl or Dockerfile.ms) to build the image.")
        command = [
            "docker", "build", "-f", dockerfile_path, "-t", self.artifacts_image, build_context
        ]
        event = run_sh_stream(command)
        logger.info(event)

    @staticmethod
    def obtain_permissions(location):
        command = [
            "gcloud", "auth", "configure-docker", f"{location}-docker.pkg.dev", "--quiet"
        ]
        event = run_sh(command)
        logger.info(event)

    def push_image(self):
        command = [
            "docker", "push", self.artifacts_image
        ]
        event = run_sh_stream(command)
        logger.info(event)

    def image_exist_in_artifacts(self):
        is_exist = True
        command = [
            "gcloud", "artifacts", "docker", "images", "describe", self.artifacts_image
        ]
        event = run_sh(command)
        if ("Image not found" in event or "NOT_FOUND" in event) and "ERROR" in event:
            is_exist = False
        logger.info(event)
        return is_exist


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
            f"{dockerfile_path}: VENV_BASE mismatch.\n"
            f"  Expected: {expected_base_line}"
        )

    # Stage 2: each configured venv name must exist in the Dockerfile
    pipelines = AppConfig.pipelines()
    missing = []

    for version, pipeline_config in pipelines.items():
        if not isinstance(pipeline_config, dict):
            continue
        if not is_service:
            venv_pl = pipeline_config.get("venv_pl")
            if venv_pl and not re.search(r'\$\{VENV_BASE\}/' + re.escape(venv_pl) + r'(?=[^a-zA-Z0-9_-]|$)', content):
                missing.append(f"pipeline '{version}' venv_pl '{venv_pl}' → ${{VENV_BASE}}/{venv_pl}")
        else:
            venv_ms = pipeline_config.get("model_service", {}).get("venv_ms")
            if venv_ms and not re.search(r'\$\{VENV_BASE\}/' + re.escape(venv_ms) + r'(?=[^a-zA-Z0-9_-]|$)', content):
                missing.append(f"pipeline '{version}' venv_ms '{venv_ms}' → ${{VENV_BASE}}/{venv_ms}")

    if missing:
        raise ValueError(
            f"The following venvs are configured in env.json but not found in {dockerfile_path}:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


def create_artifacts_image(
    dockerfile_path=None,
    build_context=".",
    force=False,
    is_service=False,
    is_push=False
):
    log_tag = "model service" if is_service else "pipeline"

    if dockerfile_path:
        _validate_dockerfile_venvs(dockerfile_path, is_service)

    aigear_config = AigearConfig.get_config()
    artifacts_image = get_image_path(is_service=is_service)
    artifacts_image_instance = ArtifactsImage(artifacts_image=artifacts_image)
    if is_push:
        is_exist = artifacts_image_instance.image_exist_in_artifacts()
        if is_exist and not force:
            logger.info(f"The {log_tag} image already exists in gcp artifacts: {artifacts_image}")
            return
        logger.info(f"The {log_tag} image exists: {is_exist}, force flag: {force}, the image will be created.")
        artifacts_image_instance.create_image(
            dockerfile_path=dockerfile_path,
            build_context=build_context
        )
        logger.info(f"The {log_tag} image has been created.")
        artifacts_image_instance.obtain_permissions(aigear_config.gcp.location)
        artifacts_image_instance.push_image()
    else:
        artifacts_image_instance.create_image(
            dockerfile_path=dockerfile_path,
            build_context=build_context
        )
        logger.info(f"The {log_tag} image has been created.")
    logger.info(f"The {log_tag} image has been pushed.")
    logger.info("------------------------------------")

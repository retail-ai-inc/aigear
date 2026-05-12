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
        command = ["docker", "build", "-f", dockerfile_path, "-t", self.image_path, build_context]
        return run_sh_stream(command) == 0

    def tag(self, src_tag: str, target_tag: str) -> bool:
        command = [
            "docker", "tag",
            f"{self._image_name}:{src_tag}",
            f"{self._image_name}:{target_tag}",
        ]
        return run_sh_stream(command) == 0

    def remove(self) -> bool:
        return run_sh_stream(["docker", "rmi", self.image_path]) == 0

    def prune(self, keep: int) -> list[str]:
        output = run_sh([
            "docker", "images", "--format", "{{.Tag}}\t{{.CreatedAt}}", self._image_name
        ])
        entries = []
        for line in output.strip().splitlines():
            parts = line.strip().split("\t", 1)
            if len(parts) == 2:
                entries.append((parts[0], parts[1]))
        entries.sort(key=lambda x: x[1], reverse=True)  # newest first
        to_delete = entries[keep:]
        deleted = []
        for tag, _ in to_delete:
            if run_sh_stream(["docker", "rmi", f"{self._image_name}:{tag}"]) == 0:
                deleted.append(tag)
            else:
                logger.warning(f"Failed to remove local image tag: {tag}")
        return deleted


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

    if is_build:
        if dockerfile_path:
            _validate_dockerfile_venvs(dockerfile_path, is_service)
        if not local.build(dockerfile_path=dockerfile_path, build_context=build_context):
            return False
        logger.info(f"The {log_tag} image has been created.")

    if is_push:
        location = aigear_config.gcp.location
        run_sh(["gcloud", "auth", "configure-docker", f"{location}-docker.pkg.dev", "--quiet"])
        if run_sh_stream(["docker", "push", image_path]) != 0:
            return False
        logger.info(f"The {log_tag} image has been pushed.")
    return True

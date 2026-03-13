from aigear.common import run_sh, run_sh_stream
from aigear.common.config import AigearConfig, get_project_name
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
        if "Image not found" in event and "ERROR" in event:
            is_exist = False
        logger.info(event)
        return is_exist


def define_image_name(image_name, repository_name, is_service=False):
    if image_name is None:
        image_name = get_project_name()
        image_name = image_name.replace("_", "-")
    if image_name is None:
        image_name = repository_name
    if is_service:
        image_name += "-service"
    return image_name


def get_artifacts_image(aigear_config, image_name=None, image_version="latest", is_service=False):
    project_id = aigear_config.gcp.gcp_project_id
    zone = aigear_config.gcp.location
    repository_name = aigear_config.gcp.artifacts.repository_name
    image_name = define_image_name(image_name, repository_name, is_service)
    artifacts_image = f"{zone}-docker.pkg.dev/{project_id}/{repository_name}/{image_name}:{image_version}"
    return artifacts_image


def create_artifacts_image(
    dockerfile_path=None,
    build_context=".",
    force=False,
    image_name=None,
    image_version="latest",
    is_service=False,
    is_push=False
):
    if is_service:
        log_tag = "model servicr"
    else:
        log_tag = "pipeline"

    aigear_config = AigearConfig.get_config()
    artifacts_image = get_artifacts_image(aigear_config, image_name, image_version, is_service)
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

from aigear.common import run_sh, run_sh_stream
from aigear.common.logger import Logging
from aigear.common.config import AigearConfig, get_project_name


logger = Logging(log_name=__name__).console_logging()

class ArtifactsImage:
    def __init__(self, artifacts_image):
        self.artifacts_image = artifacts_image

    def create_image(self, dockerfile_path="."):
        command = [
            "docker", "build", "-t", self.artifacts_image, dockerfile_path
        ]
        event = run_sh_stream(command)
        logger.info(event)

    @staticmethod
    def obtain_permissions():
        command = [
            "gcloud", "auth", "configure-docker", "asia-northeast1-docker.pkg.dev", "--quiet"
        ]
        event = run_sh(command)
        logger.info(event)

    def push_image(self):
        command = [
            "docker", "push", self.artifacts_image
        ]
        event = run_sh_stream(command)
        logger.info(event)

    def iamge_exist_in_artifacts(self):
        is_exist = True
        command = [
            "gcloud", "artifacts", "docker", "images", "describe", self.artifacts_image
        ]
        event = run_sh(command)
        if "Image not found" in event and "ERROR" in event:
            is_exist = False
        logger.info(event)
        return is_exist

def get_artifacts_image(aigear_config, image_name=None, image_version="latest"):
    project_id = aigear_config.gcp.gcp_project_id
    zone = aigear_config.gcp.location
    repository_name = aigear_config.gcp.artifacts.repository_name
    if image_name is None:
        image_name = get_project_name()
    if image_name is None:
        image_name = repository_name
    artifacts_image = f"{zone}-docker.pkg.dev/{project_id}/{repository_name}/{image_name}:{image_version}"
    return artifacts_image

def create_artifacts_image(dockerfile_path=".", force=False, image_name=None, image_version="latest"):
    aigear_config = AigearConfig.get_config()
    artifacts_image = get_artifacts_image(aigear_config, image_name, image_version)
    artifacts_image_instance = ArtifactsImage(artifacts_image=artifacts_image)
    is_exist = artifacts_image_instance.iamge_exist_in_artifacts()
    if is_exist and not force:
        logger.info(f"The image already exists in gcp artifacts: {artifacts_image}")
        return
    elif is_exist and force:
        logger.info(f"The image already exists but force flag is set, recreating: {artifacts_image}")
    else:
        logger.info("The image does not exist in gcp artifacts, it will be created.")
    artifacts_image_instance.create_image(dockerfile_path)
    artifacts_image_instance.obtain_permissions()
    artifacts_image_instance.push_image()
    logger.info("The image has been created.")

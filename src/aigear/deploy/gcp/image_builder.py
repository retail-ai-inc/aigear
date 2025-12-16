"""
Docker Image Builder Module

Provides functionality to build and push Docker images to Artifact Registry.
"""

from aigear.common import run_sh
from aigear.common.logger import Logging
from pathlib import Path
from typing import Optional


logger = Logging(log_name=__name__).console_logging()


class ImageBuilder:
    """Docker Image Builder"""

    def __init__(
        self,
        project_id: str,
        repository_name: str,
        location: str = "asia-northeast1",
        image_name: Optional[str] = None,
        dockerfile: str = "Dockerfile-grpc",
    ):
        """
        Initialize Image Builder

        Args:
            project_id: GCP project ID
            repository_name: Artifact Registry repository name
            location: Repository location
            image_name: Image name (defaults to repository name)
            dockerfile: Dockerfile name
        """
        self.project_id = project_id
        self.repository_name = repository_name
        self.location = location
        self.image_name = image_name or repository_name
        self.dockerfile = dockerfile

        # Construct full image URL
        self.registry_url = f"{location}-docker.pkg.dev/{project_id}/{repository_name}"
        self.full_image_url = f"{self.registry_url}/{self.image_name}"

    def configure_docker_auth(self) -> bool:
        """
        Configure Docker authentication for Artifact Registry

        Returns:
            True if successful, False otherwise
        """
        logger.info("Configuring Docker authentication for Artifact Registry")

        command = [
            "gcloud", "auth", "configure-docker",
            f"{self.location}-docker.pkg.dev",
            "--quiet",
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to configure Docker auth: {event}")
                return False

            logger.info("✓ Docker authentication configured")
            return True

        except Exception as e:
            logger.error(f"Error configuring Docker auth: {str(e)}")
            return False

    def build_image(
        self,
        context_dir: Path,
        tag: str = "latest",
        additional_tags: Optional[list] = None,
        build_args: Optional[dict] = None,
    ) -> bool:
        """
        Build Docker image locally

        Args:
            context_dir: Build context directory
            tag: Image tag
            additional_tags: Additional tags for the image
            build_args: Build arguments

        Returns:
            True if successful, False otherwise
        """
        if not context_dir.exists():
            logger.error(f"Context directory not found: {context_dir}")
            return False

        dockerfile_path = context_dir / self.dockerfile
        if not dockerfile_path.exists():
            logger.error(f"Dockerfile not found: {dockerfile_path}")
            return False

        logger.info(f"Building Docker image: {self.full_image_url}:{tag}")

        # Build command
        command = [
            "docker", "build",
            "-f", str(dockerfile_path),
            "-t", f"{self.full_image_url}:{tag}",
        ]

        # Add additional tags
        if additional_tags:
            for additional_tag in additional_tags:
                command.extend(["-t", f"{self.full_image_url}:{additional_tag}"])

        # Add build args
        if build_args:
            for key, value in build_args.items():
                command.extend(["--build-arg", f"{key}={value}"])

        # Add context directory
        command.append(str(context_dir))

        try:
            event = run_sh(command, timeout=600000)  # 10 minutes timeout
            logger.info(event)

            if "ERROR" in event or "error" in event.lower():
                logger.error(f"Failed to build image: {event}")
                return False

            logger.info(f"✓ Image built successfully: {self.full_image_url}:{tag}")
            return True

        except Exception as e:
            logger.error(f"Error building image: {str(e)}")
            return False

    def push_image(self, tag: str = "latest") -> bool:
        """
        Push Docker image to Artifact Registry

        Args:
            tag: Image tag

        Returns:
            True if successful, False otherwise
        """
        image_url = f"{self.full_image_url}:{tag}"
        logger.info(f"Pushing image to Artifact Registry: {image_url}")

        command = [
            "docker", "push",
            image_url,
        ]

        try:
            event = run_sh(command, timeout=600000)  # 10 minutes timeout
            logger.info(event)

            if "ERROR" in event or "error" in event.lower():
                logger.error(f"Failed to push image: {event}")
                return False

            logger.info(f"✓ Image pushed successfully: {image_url}")
            return True

        except Exception as e:
            logger.error(f"Error pushing image: {str(e)}")
            return False

    def build_and_push(
        self,
        context_dir: Path,
        tag: str = "latest",
        additional_tags: Optional[list] = None,
        build_args: Optional[dict] = None,
    ) -> bool:
        """
        Build and push Docker image in one step

        Args:
            context_dir: Build context directory
            tag: Image tag
            additional_tags: Additional tags for the image
            build_args: Build arguments

        Returns:
            True if successful, False otherwise
        """
        # Configure Docker auth
        if not self.configure_docker_auth():
            return False

        # Build image
        if not self.build_image(context_dir, tag, additional_tags, build_args):
            return False

        # Push main tag
        if not self.push_image(tag):
            return False

        # Push additional tags
        if additional_tags:
            for additional_tag in additional_tags:
                if not self.push_image(additional_tag):
                    logger.warning(f"Failed to push additional tag: {additional_tag}")

        return True

    def build_with_cloud_build(
        self,
        context_dir: Path,
        tag: str = "latest",
        substitutions: Optional[dict] = None,
    ) -> bool:
        """
        Build Docker image using Cloud Build (recommended for production)

        Args:
            context_dir: Build context directory
            tag: Image tag
            substitutions: Cloud Build substitutions

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Building image with Cloud Build: {self.full_image_url}:{tag}")

        command = [
            "gcloud", "builds", "submit",
            "--tag", f"{self.full_image_url}:{tag}",
            f"--project={self.project_id}",
        ]

        # Add substitutions
        if substitutions:
            subs_str = ",".join([f"{k}={v}" for k, v in substitutions.items()])
            command.extend(["--substitutions", subs_str])

        # Add context directory
        command.append(str(context_dir))

        try:
            event = run_sh(command, timeout=1200000)  # 20 minutes timeout
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to build with Cloud Build: {event}")
                return False

            logger.info(f"✓ Image built and pushed with Cloud Build: {self.full_image_url}:{tag}")
            return True

        except Exception as e:
            logger.error(f"Error building with Cloud Build: {str(e)}")
            return False

    def tag_image(self, source_tag: str, target_tag: str) -> bool:
        """
        Tag an existing image with a new tag

        Args:
            source_tag: Source tag
            target_tag: Target tag

        Returns:
            True if successful, False otherwise
        """
        source_url = f"{self.full_image_url}:{source_tag}"
        target_url = f"{self.full_image_url}:{target_tag}"

        logger.info(f"Tagging image: {source_url} -> {target_url}")

        command = [
            "docker", "tag",
            source_url,
            target_url,
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to tag image: {event}")
                return False

            logger.info(f"✓ Image tagged successfully")
            return True

        except Exception as e:
            logger.error(f"Error tagging image: {str(e)}")
            return False

    def list_images(self) -> list:
        """
        List images in Artifact Registry

        Returns:
            List of image tags
        """
        command = [
            "gcloud", "artifacts", "docker", "images", "list",
            self.registry_url,
            f"--project={self.project_id}",
            "--format=value(tags)",
        ]

        try:
            event = run_sh(command)
            tags = [line.strip() for line in event.split('\n') if line.strip()]
            return tags

        except Exception as e:
            logger.error(f"Error listing images: {str(e)}")
            return []

    def delete_image(self, tag: str) -> bool:
        """
        Delete image from Artifact Registry

        Args:
            tag: Image tag to delete

        Returns:
            True if successful, False otherwise
        """
        image_url = f"{self.full_image_url}:{tag}"
        logger.info(f"Deleting image: {image_url}")

        command = [
            "gcloud", "artifacts", "docker", "images", "delete",
            image_url,
            f"--project={self.project_id}",
            "--quiet",
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to delete image: {event}")
                return False

            logger.info(f"✓ Image deleted successfully")
            return True

        except Exception as e:
            logger.error(f"Error deleting image: {str(e)}")
            return False


if __name__ == "__main__":
    # Example usage
    builder = ImageBuilder(
        project_id="my-project",
        repository_name="grpc-services",
        location="asia-northeast1",
        image_name="my-alc-service",
    )

    # Build and push image
    context_dir = Path(".")
    builder.build_and_push(
        context_dir=context_dir,
        tag="v1.0.0",
        additional_tags=["latest"],
    )

    # Or use Cloud Build (recommended)
    # builder.build_with_cloud_build(
    #     context_dir=context_dir,
    #     tag="v1.0.0",
    # )

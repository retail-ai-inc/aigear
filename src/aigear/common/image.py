from aigear.common.config import AigearConfig, get_project_name


def get_image_name(image_name: str = None, is_service: bool = False) -> str:
    """
    Resolve the image name from config or derive it from project_name.

    Priority:
      1. Explicit image_name argument
      2. artifacts.ms_image_name / pl_image_name from config
      3. Derived from project_name (underscores replaced with hyphens)
      4. Fall back to repository_name

    Args:
        image_name: Optional override.
        is_service: True for model service image (ms), False for pipeline image (pl).
    """
    if image_name:
        return image_name

    artifacts = AigearConfig.get_config().gcp.artifacts
    config_name = artifacts.ms_image_name if is_service else artifacts.pl_image_name
    if config_name:
        return config_name

    name = get_project_name()
    if name:
        name = name.replace("_", "-")
        return name + "-service" if is_service else name

    repo = artifacts.repository_name
    return repo + "-service" if is_service else repo


def get_image_path(
    image_name: str = None, image_tag: str = None, is_service: bool = False
) -> str:
    """
    Build the full Artifact Registry image path.

    Format: <location>-docker.pkg.dev/<project_id>/<repository>/<image_name>:<tag>

    Args:
        image_name: Optional image name override.
        image_tag:  Optional tag override. Falls back to artifacts.image_tag, then "latest".
        is_service: True for model service image, False for pipeline image.
    """
    gcp = AigearConfig.get_config().gcp
    location = gcp.location
    project_id = gcp.gcp_project_id
    repository = gcp.artifacts.repository_name
    tag = image_tag or gcp.artifacts.image_tag or "latest"
    name = get_image_name(image_name, is_service)
    return f"{location}-docker.pkg.dev/{project_id}/{repository}/{name}:{tag}"

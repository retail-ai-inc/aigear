import platform
from pathlib import Path
from string import Template

from aigear.common.config import get_project_name
from aigear.common.constant import ENV_LOCAL, VENV_BASE_DIR
from aigear.common.image import get_image_path
from aigear.common.logger import Logging

_TEMPLATE_PATH = Path(__file__).parent / "grpc_deployment.yaml.tpl"

logger = Logging(log_name=__name__).console_logging()


def _to_hostpath(path: Path) -> str:
    """Convert a local path to Kubernetes hostPath format.

    On Windows with Docker Desktop (WSL2), drives are mounted at
    /run/desktop/mnt/host/<drive>/... inside the node.
    """
    if platform.system() == "Windows":
        posix = path.as_posix()  # e.g. C:/Users/foo/asset
        if len(posix) >= 2 and posix[1] == ":":
            drive = posix[0].lower()
            rest = posix[2:]  # e.g. /Users/foo/asset
            return f"/run/desktop/mnt/host/{drive}{rest}"
    return path.as_posix()


def get_helm_path(
        model_class_path=None,
        env: str = ENV_LOCAL,
) -> Path:
    project_path = Path.cwd()
    if model_class_path is None:
        helm_location = project_path
    else:
        indices = [i for i, c in enumerate(model_class_path) if c == '.']
        split_pos = indices[-2]
        helm_address = model_class_path[:split_pos]
        helm_location = helm_address.replace(".", "/")
        helm_location = project_path / helm_location

    helm_path = helm_location / f"grpc_deployment_{env}.yaml"
    return helm_path


def _create_helm_chart(
        helm_path,
        service_name,
        service_image,
        service_ports: str = "50051",
        replicas: int = 1,
        port: str = "50051",
        pipeline_version=None,
        model_class_path=None,
        env: str = ENV_LOCAL,
        venv: str = None,
):
    if pipeline_version is None or model_class_path is None:
        print("The 'pipeline_version' and 'model_class_path' of 'create_helm_chart' is empty.")
        grpc_command = ""
    else:
        aigear_task = f"{VENV_BASE_DIR}/{venv}/bin/aigear-task" if venv else "aigear-task"
        grpc_command = (
            f'command: ["{aigear_task}", "grpc"]\n'
            f'        args:\n'
            f'          - "--version"\n'
            f'          - "{pipeline_version}"\n'
            f'          - "--module"\n'
            f'          - "{model_class_path}"'
        )

    image_pull_policy = "Never" if env == ENV_LOCAL else "Always"

    asset_volume_mount = ""
    asset_volume = ""
    if env == ENV_LOCAL:
        asset_path = _to_hostpath(Path.cwd() / "asset")
        asset_volume_mount = (
            "\n        volumeMounts:\n"
            "        - name: asset-volume\n"
            "          mountPath: /ms/asset"
        )
        asset_volume = (
            f"\n      volumes:\n"
            f"      - name: asset-volume\n"
            f"        hostPath:\n"
            f"          path: {asset_path}\n"
            f"          type: DirectoryOrCreate"
        )

    tpl = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    content = tpl.substitute(
        service_name=service_name,
        service_image=service_image,
        image_pull_policy=image_pull_policy,
        replicas=replicas,
        service_ports=service_ports,
        port=port,
        grpc_command=grpc_command,
        asset_volume_mount=asset_volume_mount,
        asset_volume=asset_volume,
    )
    helm_path.write_text(content, encoding="utf-8")


def create_helm_file(
    pipeline_version: str = None,
    model_class_path: str = None,
    service_ports: str = "50051",
    replicas: int = 1,
    port: str = "50051",
    env: str = ENV_LOCAL,
    venv: str = None,
    force: bool = False,
):
    artifacts_image = get_image_path(is_service=True)
    if pipeline_version is None:
        logger.info("The 'pipeline_version' is empty, don't know which service to deploy.")
        return
    service_name = pipeline_version.replace("_", "-")
    project_name = get_project_name()
    project_name = project_name.replace("_", "-")
    service_name = f"{project_name}-{service_name}-service"

    helm_path = get_helm_path(model_class_path=model_class_path, env=env)
    if helm_path.exists() and not force:
        logger.info(f"YAML already exists, skipping: {helm_path}")
        return None
    _create_helm_chart(
        helm_path=helm_path,
        service_name=service_name,
        service_image=artifacts_image,
        service_ports=service_ports,
        replicas=replicas,
        port=port,
        pipeline_version=pipeline_version,
        model_class_path=model_class_path,
        env=env,
        venv=venv,
    )
    return helm_path

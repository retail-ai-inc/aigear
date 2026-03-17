import platform
from pathlib import Path

from aigear.common.config import AigearConfig, get_project_name
from aigear.common.logger import Logging
from aigear.deploy.gcp.artifacts_image import get_artifacts_image

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
        is_local: bool = False,
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

    filename = "grpc_deployment_local.yaml" if is_local else "grpc_deployment.yaml"
    helm_path = helm_location / filename
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
        is_local: bool = False,
):
    grpc_commad = f"""
        command: ["aigear-grpc"]
        args:
          - "--version"
          - "{pipeline_version}"
          - "--model_class_path"
          - "{model_class_path}"
"""
    if pipeline_version is None or model_class_path is None:
        print("The 'pipeline_version' and 'model_class_path' of 'create_helm_chart' is empty.")
        grpc_commad = ""

    image_pull_policy = "Never" if is_local else "Always"

    asset_volume_mount = ""
    asset_volume = ""
    if is_local:
        asset_path = _to_hostpath(Path.cwd() / "asset")
        asset_volume_mount = """
        volumeMounts:
        - name: asset-volume
          mountPath: /ms/asset"""
        asset_volume = f"""
      volumes:
      - name: asset-volume
        hostPath:
          path: {asset_path}
          type: DirectoryOrCreate"""

    deploy_template = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {service_name}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {service_name}
  template:
    metadata:
      labels:
        app: {service_name}
    spec:
      containers:
      - name: {service_name}
        image: {service_image}
        imagePullPolicy: {image_pull_policy}
        {grpc_commad}
        ports:
        - containerPort: {service_ports}{asset_volume_mount}{asset_volume}
---

apiVersion: v1
kind: Service
metadata:
  name: {service_name}
spec:
  type: LoadBalancer
  ports:
    - port: {port}
      targetPort: {service_ports}
      protocol: TCP
  selector:
    app: {service_name}
"""
    with open(helm_path, "w") as f:
        f.write(deploy_template)


def create_helm_file(
    pipeline_version: str = None,
    model_class_path: str = None,
    service_ports: str = "50051",
    replicas: int = 1,
    port: str = "50051",
    is_local: bool = False,
):
    aigear_config = AigearConfig.get_config()
    artifacts_image = get_artifacts_image(aigear_config=aigear_config, is_service=True)
    if pipeline_version is None:
        logger.info("The 'pipeline_version' is empty, don't know which service to deploy.")
        return
    service_name = pipeline_version.replace("_", "-")
    project_name = get_project_name()
    project_name = project_name.replace("_", "-")
    service_name = f"{project_name}-{service_name}-service"

    helm_path = get_helm_path(model_class_path=model_class_path, is_local=is_local)
    if not helm_path.exists():
        _create_helm_chart(
            helm_path=helm_path,
            service_name=service_name,
            service_image=artifacts_image,
            service_ports=service_ports,
            replicas=replicas,
            port=port,
            pipeline_version=pipeline_version,
            model_class_path=model_class_path,
            is_local=is_local,
        )
    return helm_path

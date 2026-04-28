from aigear.common import run_sh
from aigear.common.config import PipelinesConfig
from aigear.common.constant import ENV_LOCAL
from aigear.service.grpc.constant import DEFAULT_GRPC_PORT
from aigear.common.logger import Logging
from aigear.deploy.common.helm_chart import create_helm_file, get_helm_path
from aigear.deploy.common.kubectl_command import helm_deploy, helm_deployment_delete

logger = Logging(log_name=__name__).console_logging()


def switch_local_context() -> None:
    command = [
        "kubectl", "config", "use-context", "docker-desktop"
    ]
    event = run_sh(command)
    logger.info(event)


def deploy_local_grpc(
        pipeline_version: str | None = None,
        service_ports: str = DEFAULT_GRPC_PORT,
        replicas: int = 1,
        port: str = DEFAULT_GRPC_PORT,
):
    pipe_config       = PipelinesConfig.get_version_config(pipeline_version)
    ms_config         = pipe_config.get("model_service", {})
    model_class_path  = ms_config.get("model_class_path")
    venv              = ms_config.get("venv_ms")

    helm_path = create_helm_file(
        pipeline_version=pipeline_version,
        model_class_path=model_class_path,
        service_ports=service_ports,
        replicas=replicas,
        port=port,
        env=ENV_LOCAL,
        venv=venv,
    )

    switch_local_context()
    event = helm_deploy(helm_path)
    if "error" in event:
        logger.info(f"Error: {event}.")
    else:
        logger.info("Deployment completed.")


def delete_local_grpc(pipeline_version: str | None = None) -> None:
    pipe_config      = PipelinesConfig.get_version_config(pipeline_version)
    model_class_path = pipe_config.get("model_service", {}).get("model_class_path")
    helm_path        = get_helm_path(model_class_path=model_class_path, env=ENV_LOCAL)
    helm_deployment_delete(helm_path)

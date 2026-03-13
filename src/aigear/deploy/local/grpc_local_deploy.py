from aigear.common import run_sh
from aigear.common.config import PipelinesConfig
from aigear.common.logger import Logging
from aigear.deploy.common.helm_chart import create_helm_file, get_helm_path
from aigear.deploy.common.kubectl_command import helm_deploy, helm_deployment_delete

logger = Logging(log_name=__name__).console_logging()


def switch_local_context():
    command = [
        "kubectl", "config", "use-context", "docker-desktop"
    ]
    event = run_sh(command)
    logger.info(event)

def deploy_local_grpc(
        pipeline_version: str=None,
        model_class_path: str=None,
        service_ports: str = "50051",
        replicas: int = 1,
        port: str = "50051",
):
    helm_path = create_helm_file(
        pipeline_version=pipeline_version,
        model_class_path=model_class_path,
        service_ports = service_ports,
        replicas = replicas,
        port = port,
    )

    pipe_config = PipelinesConfig.get_version_config(pipeline_version)
    release_switch = pipe_config.get("model_service", {}).get("release", False)
    if release_switch:
        switch_local_context()
        event = helm_deploy(helm_path)
        if "error" in event:
            logger.info(f"Error: {event}.")
        else:
            logger.info("Deployment completed.")
    else:
        logger.info("The publishing model service is not set in the configuration(model_service.release).")


def delete_local_grpc(
        model_class_path=None
):
    helm_path = get_helm_path(model_class_path=model_class_path)
    helm_deployment_delete(helm_path)

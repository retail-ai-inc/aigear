from aigear.common import run_sh
from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.common.constant import ENV_STAGING
from aigear.common.logger import Logging
from aigear.deploy.common.helm_chart import create_helm_file, get_helm_path
from aigear.deploy.common.kubectl_command import helm_deploy, helm_deployment_delete

logger = Logging(log_name=__name__).console_logging()


def switch_gcp_context(cluster_name, project_id, region):
    command = [
        "gcloud", "container", "clusters", "get-credentials",
        cluster_name,
        f"--region={region}",
        f"--project={project_id}",
    ]
    event = run_sh(command)
    logger.info(event)


def deploy_gcp_grpc(
    pipeline_version: str = None,
    service_ports: str = "50051",
    replicas: int = 1,
    port: str = "50051",
    env: str = ENV_STAGING,
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
        env=env,
        venv=venv,
    )

    aigear_config = AigearConfig.get_config()
    switch_gcp_context(
        cluster_name=aigear_config.gcp.kubernetes.cluster_name,
        project_id=aigear_config.gcp.gcp_project_id,
        region=aigear_config.gcp.location
    )
    event = helm_deploy(helm_path)
    if "error" in event:
        logger.info(f"Error: {event}.")
    else:
        logger.info("Deployment completed.")


def delete_gcp_grpc(
    pipeline_version: str = None,
    env: str = ENV_STAGING,
):
    pipe_config      = PipelinesConfig.get_version_config(pipeline_version)
    model_class_path = pipe_config.get("model_service", {}).get("model_class_path")
    helm_path        = get_helm_path(model_class_path=model_class_path, env=env)
    helm_deployment_delete(helm_path)

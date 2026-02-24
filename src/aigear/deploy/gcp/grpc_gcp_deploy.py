from aigear.deploy.common.helm_chart import create_helm_chart
from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


def switch_gcp_context(cluster_name, zone):
    command = [
        "gcloud", "container", "clusters", "get-credentials", cluster_name, f"--zone={zone}"
    ]
    event = run_sh(command)
    logger.info(event)


def helm_deploy(helm_location="."):
    command = [
        "kubectl", "apply", "-f", f"{helm_location}/grpc_deployment.yaml"
    ]
    event = run_sh(command)
    logger.info(event)
    return event


def deploy_gcp_grpc(
    cluster_name=None,
    zone=None,
    helm_location=".",
    service_name=None,
    service_image=None,
    service_ports=None
):
    switch_gcp_context(cluster_name, zone)
    create_helm_chart(
        helm_location=helm_location,
        service_name=service_name,
        service_image=service_image,
        service_ports=service_ports
    )
    event = helm_deploy(helm_location=helm_location)
    if "error" not in event:
        logger.info("Deployment completed.")
        logger.info(f"Methods of accessing services:\n"
                    f"  kubectl get service grpc-server\n"
                    f"  grpcurl -plaintext ExternalIP:port list\n"
                    f"  grpcurl -plaintext ExternalIP:port 'service path'")


def delete_local_grpc(
    helm_location="."
):
    command = [
        "kubectl", "delete", "-f", f"{helm_location}/grpc_deployment.yaml", "--wait=false"
    ]
    event = run_sh(command)
    logger.info(event)


if __name__ == "__main__":
    deploy_gcp_grpc(
        cluster_name="my-grpc-cluster",
        zone="asia-northeast1-a",
        helm_location=".",
        service_name="grpc-service",
        service_image="asia-northeast1-docker.pkg.dev/ssc-ape-staging/test-pipelines-images/hello-world-grpc:latest",
        service_ports="50051"
    )
    # delete_local_grpc(
    #     helm_location="."
    # )

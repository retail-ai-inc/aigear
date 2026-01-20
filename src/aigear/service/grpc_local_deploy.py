from aigear.service.helm_chart import create_helm_chart
from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


def switch_local_context():
    command = [
        "kubectl", "config", "use-context", "docker-desktop"
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


def deploy_local_grpc(
        helm_location=".",
        service_name=None,
        service_image=None,
        service_ports=None
):
    switch_local_context()
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
                    f"  grpcurl -plaintext localhost:50051 list\n"
                    f"  grpcurl -plaintext localhost:50051 'service path'")


def delete_local_grpc(
        helm_location="."
):
    command = [
        "kubectl", "delete", "-f", f"{helm_location}/grpc_deployment.yaml"
    ]
    event = run_sh(command)
    logger.info(event)


if __name__ == "__main__":
    # deploy_local_grpc(
    #     helm_location=".",
    #     service_name="grpc",
    #     service_image="aguilbau/hello-world-grpc:latest",
    #     service_ports="50051"
    # )
    delete_local_grpc(
        helm_location="."
    )

    # grpcurl -plaintext localhost:50051 list
    # grpcurl -plaintext localhost:50051 helloworld.Greeter/SayHello

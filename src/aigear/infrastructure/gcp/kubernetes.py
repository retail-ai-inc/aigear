from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class KubernetesCluster:
    def __init__(
        self,
        cluster_name,
        zone,
        num_nodes,
        min_nodes,
        max_nodes,
        project_id
    ):
        self.cluster_name = cluster_name
        self.zone = zone
        self.num_nodes = num_nodes
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        self.project_id = project_id

    def create(self):
        command = [
            "gcloud", "container", "clusters", "create",
            self.cluster_name,
            f"--region={self.zone}",
            f"--node-locations={self.zone}-a",
            "--enable-autoscaling",
            f"--num-nodes={self.num_nodes}",
            f"--min-nodes={self.min_nodes}",
            f"--max-nodes={self.max_nodes}",
            f"--project={self.project_id}",
            "--async",
            "--quiet"
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.info(event)
            logger.error("Error occurred while creating GKE cluster.")

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "container", "clusters", "describe",
            self.cluster_name,
            f"--zone={self.zone}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "ERROR" not in event:
            is_exist = True
        elif "Not found" not in event and "NOT_FOUND" not in event:
            logger.error(f"Unexpected error describing cluster ({self.cluster_name}): {event}")
        return is_exist

    def delete(self):
        command = [
            "gcloud", "container", "clusters", "delete",
            self.cluster_name,
            f"--location={self.zone}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command, "yes\n")
        logger.info(f"\n{event}")


if __name__ == "__main__":
    kubernetes_cluster = KubernetesCluster(
        cluster_name="my-grpc-cluster",
        zone="asia-northeast1-a",
        num_nodes=1,
        min_nodes=1,
        max_nodes=5,
        project_id="",
    )
    kubernetes_cluster_exist = kubernetes_cluster.describe()
    print("kubernetes_cluster: ", kubernetes_cluster_exist)
    if not kubernetes_cluster_exist:
        kubernetes_cluster.create()

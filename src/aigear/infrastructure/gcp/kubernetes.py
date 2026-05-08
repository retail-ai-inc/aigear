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
        run_sh(command, check=True)

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
            "--async",
            "--quiet",
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(f"Error occurred while deleting GKE cluster ({self.cluster_name}): {event}")
        else:
            logger.info(f"GKE cluster '{self.cluster_name}' deletion initiated (async).")

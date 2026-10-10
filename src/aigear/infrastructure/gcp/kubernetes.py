from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class KubernetesCluster:
    def __init__(self, cluster_name, zone, num_nodes, min_nodes, max_nodes, project_id):
        self.cluster_name = cluster_name
        self.zone = zone
        self.num_nodes = num_nodes
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        self.project_id = project_id

    def create(self):
        command = [
            "gcloud",
            "container",
            "clusters",
            "create",
            self.cluster_name,
            f"--region={self.zone}",
            f"--node-locations={self.zone}-a",
            "--enable-autoscaling",
            f"--num-nodes={self.num_nodes}",
            f"--min-nodes={self.min_nodes}",
            f"--max-nodes={self.max_nodes}",
            f"--project={self.project_id}",
            "--async",
            "--quiet",
        ]
        run_sh(command, check=True)

    def describe(self):
        command = [
            "gcloud",
            "container",
            "clusters",
            "describe",
            self.cluster_name,
            f"--region={self.zone}",
            f"--project={self.project_id}",
        ]
        try:
            event = run_sh(command, check=True)
        except RuntimeError as exc:
            if "Not found" in str(exc) or "NOT_FOUND" in str(exc):
                return False
            raise
        if not event.strip():
            raise RuntimeError("Empty GKE cluster describe output.")
        return True

    def delete(self, wait: bool = False):
        command = [
            "gcloud",
            "container",
            "clusters",
            "delete",
            self.cluster_name,
            f"--location={self.zone}",
            f"--project={self.project_id}",
            "--quiet",
        ]
        if not wait:
            command.append("--async")
        run_sh(command, check=True, timeout=1800 if wait else 30)
        state = "deleted" if wait else "deletion initiated (async)"
        logger.info(f"GKE cluster '{self.cluster_name}' {state}.")

    def update(self):
        command_autoscaling = [
            "gcloud",
            "container",
            "clusters",
            "update",
            self.cluster_name,
            "--enable-autoscaling",
            f"--min-nodes={self.min_nodes}",
            f"--max-nodes={self.max_nodes}",
            f"--region={self.zone}",
            "--node-pool=default-pool",
            f"--project={self.project_id}",
            "--quiet",
        ]
        run_sh(command_autoscaling, check=True, timeout=600)

        command_resize = [
            "gcloud",
            "container",
            "clusters",
            "resize",
            self.cluster_name,
            f"--num-nodes={self.num_nodes}",
            f"--region={self.zone}",
            "--node-pool=default-pool",
            f"--project={self.project_id}",
            "--async",
            "--quiet",
        ]
        run_sh(command_resize, check=True)

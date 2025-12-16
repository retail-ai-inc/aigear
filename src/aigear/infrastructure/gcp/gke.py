"""
GKE Cluster Management Module

Provides functionality to create, manage, and delete GKE clusters.
"""

from aigear.common import run_sh
from aigear.common.logger import Logging
from typing import Optional, Dict, List


logger = Logging(log_name=__name__).console_logging()


class GKECluster:
    """GKE Cluster Manager"""

    def __init__(
        self,
        cluster_name: str,
        project_id: str,
        region: str,
        node_count: int = 3,
        machine_type: str = "e2-standard-4",
        disk_size: int = 100,
        enable_autoscaling: bool = True,
        min_nodes: int = 1,
        max_nodes: int = 10,
        enable_autopilot: bool = False,
    ):
        """
        Initialize GKE Cluster Manager

        Args:
            cluster_name: Cluster name
            project_id: GCP project ID
            region: GCP region (e.g., asia-northeast1)
            node_count: Initial node count
            machine_type: Machine type for nodes
            disk_size: Disk size in GB
            enable_autoscaling: Enable cluster autoscaling
            min_nodes: Minimum nodes for autoscaling
            max_nodes: Maximum nodes for autoscaling
            enable_autopilot: Use GKE Autopilot mode (recommended for cost savings)
        """
        self.cluster_name = cluster_name
        self.project_id = project_id
        self.region = region
        self.node_count = node_count
        self.machine_type = machine_type
        self.disk_size = disk_size
        self.enable_autoscaling = enable_autoscaling
        self.min_nodes = min_nodes
        self.max_nodes = max_nodes
        self.enable_autopilot = enable_autopilot

    def create(self) -> bool:
        """
        Create GKE cluster

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Creating GKE cluster: {self.cluster_name}")

        if self.enable_autopilot:
            # Autopilot mode - simpler and more cost-effective
            command = [
                "gcloud", "container", "clusters", "create-auto",
                self.cluster_name,
                f"--region={self.region}",
                f"--project={self.project_id}",
            ]
            logger.info("Using GKE Autopilot mode (recommended)")
        else:
            # Standard mode with more control
            command = [
                "gcloud", "container", "clusters", "create",
                self.cluster_name,
                f"--region={self.region}",
                f"--project={self.project_id}",
                f"--num-nodes={self.node_count}",
                f"--machine-type={self.machine_type}",
                f"--disk-size={self.disk_size}",
                "--enable-ip-alias",
                "--enable-stackdriver-kubernetes",
            ]

            if self.enable_autoscaling:
                command.extend([
                    "--enable-autoscaling",
                    f"--min-nodes={self.min_nodes}",
                    f"--max-nodes={self.max_nodes}",
                ])

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to create cluster: {event}")
                return False

            logger.info(f"✓ Cluster {self.cluster_name} created successfully")
            return True

        except Exception as e:
            logger.error(f"Error creating cluster: {str(e)}")
            return False

    def describe(self) -> bool:
        """
        Check if cluster exists

        Returns:
            True if cluster exists, False otherwise
        """
        command = [
            "gcloud", "container", "clusters", "describe",
            self.cluster_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" not in event and self.cluster_name in event:
                return True
            return False

        except Exception as e:
            logger.error(f"Error describing cluster: {str(e)}")
            return False

    def get_credentials(self) -> bool:
        """
        Get cluster credentials for kubectl

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Getting credentials for cluster: {self.cluster_name}")

        command = [
            "gcloud", "container", "clusters", "get-credentials",
            self.cluster_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to get credentials: {event}")
                return False

            logger.info("✓ Credentials configured successfully")
            return True

        except Exception as e:
            logger.error(f"Error getting credentials: {str(e)}")
            return False

    def delete(self) -> bool:
        """
        Delete GKE cluster

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Deleting GKE cluster: {self.cluster_name}")

        command = [
            "gcloud", "container", "clusters", "delete",
            self.cluster_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
            "--quiet",  # Skip confirmation
        ]

        try:
            event = run_sh(command)
            logger.info(event)

            if "ERROR" in event:
                logger.error(f"Failed to delete cluster: {event}")
                return False

            logger.info(f"✓ Cluster {self.cluster_name} deleted successfully")
            return True

        except Exception as e:
            logger.error(f"Error deleting cluster: {str(e)}")
            return False

    def list_clusters(self) -> List[str]:
        """
        List all clusters in the project

        Returns:
            List of cluster names
        """
        command = [
            "gcloud", "container", "clusters", "list",
            f"--project={self.project_id}",
            "--format=value(name)",
        ]

        try:
            event = run_sh(command)
            clusters = [line.strip() for line in event.split('\n') if line.strip()]
            return clusters

        except Exception as e:
            logger.error(f"Error listing clusters: {str(e)}")
            return []

    def get_cluster_info(self) -> Optional[Dict]:
        """
        Get detailed cluster information

        Returns:
            Dictionary with cluster information or None
        """
        command = [
            "gcloud", "container", "clusters", "describe",
            self.cluster_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
            "--format=json",
        ]

        try:
            import json
            event = run_sh(command)

            if "ERROR" in event:
                return None

            cluster_info = json.loads(event)
            return cluster_info

        except Exception as e:
            logger.error(f"Error getting cluster info: {str(e)}")
            return None


if __name__ == "__main__":
    # Example usage
    cluster = GKECluster(
        cluster_name="grpc-ml-cluster",
        project_id="my-project",
        region="asia-northeast1",
        enable_autopilot=True,
    )

    # Check if cluster exists
    exists = cluster.describe()
    if not exists:
        logger.info("Cluster does not exist, creating...")
        cluster.create()

    # Get credentials
    cluster.get_credentials()

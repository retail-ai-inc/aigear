from aigear.common.config import AigearConfig
from aigear.infrastructure.gcp.bucket import Bucket
from aigear.infrastructure.gcp.build import CloudBuild
from aigear.infrastructure.gcp.function import CloudFunction
from aigear.infrastructure.gcp.iam import ServiceAccounts
from aigear.infrastructure.gcp.pub_sub import PubSub
from aigear.infrastructure.gcp.artifacts import Artifacts
from aigear.infrastructure.gcp.gke import GKECluster
from aigear.infrastructure.gcp.gcs import GCSModelStorage
from aigear.infrastructure.gcp.constant import (
    entry_point_of_cloud_fuction,
)
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()

class Infra:
    def __init__(self):
        self.aigear_config = AigearConfig.get_config()
        self.service_account = f"{self.aigear_config.gcp.iam.account_name}@{self.aigear_config.gcp.gcp_project_id}.iam.gserviceaccount.com"
        self.model_bucket = Bucket(
            bucket_name=self.aigear_config.gcp.bucket.bucket_name,
            location=self.aigear_config.gcp.location,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.release_model_bucket = Bucket(
            bucket_name=self.aigear_config.gcp.bucket.bucket_name_for_release,
            location=self.aigear_config.gcp.location,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.cloud_build = CloudBuild(
            trigger_name=self.aigear_config.gcp.cloud_build.trigger_name,
            description=self.aigear_config.gcp.cloud_build.description,
            repo_owner=self.aigear_config.gcp.cloud_build.repo_owner,
            repo_name=self.aigear_config.gcp.cloud_build.repo_name,
            branch_pattern=self.aigear_config.gcp.cloud_build.branch_pattern,
            build_config=self.aigear_config.gcp.cloud_build.build_config,
            region=self.aigear_config.gcp.location,
            substitutions=self.aigear_config.gcp.cloud_build.substitutions,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.cloud_function = CloudFunction(
            function_name=self.aigear_config.gcp.cloud_function.function_name,
            region=self.aigear_config.gcp.location,
            entry_point=entry_point_of_cloud_fuction,
            topic_name=self.aigear_config.gcp.pub_sub.topic_name,
            project_id=self.aigear_config.gcp.gcp_project_id,
            service_account=self.service_account
        )
        self.service_accounts = ServiceAccounts(
            project_id=self.aigear_config.gcp.gcp_project_id,
            account_name=self.aigear_config.gcp.iam.account_name,
        )
        self.pubsub = PubSub(
            topic_name=self.aigear_config.gcp.pub_sub.topic_name,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )
        self.artifacts = Artifacts(
            repository_name=self.aigear_config.gcp.artifacts.repository_name,
            location=self.aigear_config.gcp.location,
            project_id=self.aigear_config.gcp.gcp_project_id,
        )

        # GKE Cluster (optional, for gRPC service deployment)
        self.gke_cluster = None
        if hasattr(self.aigear_config.gcp, 'gke') and self.aigear_config.gcp.gke.on:
            cluster_config = self.aigear_config.gcp.gke.cluster
            self.gke_cluster = GKECluster(
                cluster_name=cluster_config.name,
                project_id=self.aigear_config.gcp.gcp_project_id,
                region=self.aigear_config.gcp.location,
                node_count=cluster_config.get('node_count', 3),
                machine_type=cluster_config.get('machine_type', 'e2-standard-4'),
                disk_size=cluster_config.get('disk_size', 100),
                enable_autoscaling=cluster_config.get('enable_autoscaling', True),
                min_nodes=cluster_config.get('min_nodes', 1),
                max_nodes=cluster_config.get('max_nodes', 10),
                enable_autopilot=cluster_config.get('enable_autopilot', False),
            )

        # GCS Model Storage (optional, for model file storage)
        self.gcs_storage = None
        if hasattr(self.aigear_config.gcp, 'gcs_models') and self.aigear_config.gcp.gcs_models.on:
            self.gcs_storage = GCSModelStorage(
                bucket_name=self.aigear_config.gcp.gcs_models.bucket_name,
                project_id=self.aigear_config.gcp.gcp_project_id,
                location=self.aigear_config.gcp.location,
            )

    def create(self):
        if self.aigear_config.gcp.iam.on:
            service_accounts_exist = self.service_accounts.describe()
            if not service_accounts_exist:
                logger.info(f"Service accounts({self.aigear_config.gcp.iam.account_name}) not found, will be created.")
                self.service_accounts.create()
                self.service_accounts.add_iam_policy_binding()
            else:
                logger.info(f"Service accounts({self.aigear_config.gcp.iam.account_name}) already exists.")
        else:
            logger.info(f"The service account has been closed in the configuration.")

        if self.aigear_config.gcp.pub_sub.on:
            pubsub_exist = self.pubsub.describe()
            if not pubsub_exist:
                logger.info(f"PubSub({self.aigear_config.gcp.pub_sub.topic_name}) not found, will be created.")
                self.pubsub.create()
            else:
                logger.info(f"PubSub({self.aigear_config.gcp.pub_sub.topic_name}) already exists.")
        else:
            logger.info(f"The pub_sub has been closed in the configuration.")

        if self.aigear_config.gcp.cloud_build.on:
            cloud_build_exist = self.cloud_build.describe()
            if not cloud_build_exist:
                logger.info(
                    f"Cloud build({self.aigear_config.gcp.cloud_build.trigger_name}) not found, will be created.")
                self.cloud_build.create()
            else:
                logger.info(f"Cloud build({self.aigear_config.gcp.cloud_build.trigger_name}) already exists.")
        else:
            logger.info(f"The cloud_build has been closed in the configuration.")

        if self.aigear_config.gcp.bucket.on:
            model_bucket_exist = self.model_bucket.describe()
            if not model_bucket_exist:
                logger.info(
                    f"Model bucket({self.aigear_config.gcp.bucket.bucket_name}) not found, will be created.")
                self.model_bucket.create()
            else:
                logger.info(f"Model bucket({self.aigear_config.gcp.bucket.bucket_name}) already exists.")

            release_model_bucket_exist = self.release_model_bucket.describe()
            if not release_model_bucket_exist:
                logger.info(
                    f"Release model bucket({self.aigear_config.gcp.bucket.bucket_name_for_release}) not found, will be created.")
                self.release_model_bucket.create()
            else:
                logger.info(
                    f"Release model bucket({self.aigear_config.gcp.bucket.bucket_name_for_release}) already exists.")
        else:
            logger.info(f"The bucket has been closed in the configuration.")

        if self.aigear_config.gcp.artifacts.on:
            artifacts_exist = self.artifacts.describe()
            if not artifacts_exist:
                logger.info(
                    f"Artifacts({self.aigear_config.gcp.artifacts.repository_name}) not found, will be created.")
                self.artifacts.create()
            else:
                logger.info(f"Artifacts({self.aigear_config.gcp.pub_sub.topic_name}) already exists.")
        else:
            logger.info(f"The artifacts has been closed in the configuration.")

        if self.aigear_config.gcp.cloud_function.on:
            cloud_function_exist = self.cloud_function.describe()
            if not cloud_function_exist:
                logger.info(
                    f"Cloud function({self.aigear_config.gcp.cloud_function.function_name}) not found, will be created.")
                self.cloud_function.deploy()
            else:
                logger.info(f"Cloud function({self.aigear_config.gcp.cloud_function.function_name}) already exists.")
        else:
            logger.info(f"The cloud_function has been closed in the configuration.")

        # GKE Cluster (optional)
        if self.gke_cluster:
            gke_exist = self.gke_cluster.describe()
            if not gke_exist:
                logger.info(f"GKE cluster({self.gke_cluster.cluster_name}) not found, will be created.")
                logger.info("⏳ This may take 5-10 minutes...")
                self.gke_cluster.create()
            else:
                logger.info(f"GKE cluster({self.gke_cluster.cluster_name}) already exists.")
        else:
            logger.info(f"The GKE cluster has been closed in the configuration.")

        # GCS Model Storage (optional)
        if self.gcs_storage:
            gcs_exist = self.gcs_storage.bucket_exists()
            if not gcs_exist:
                logger.info(f"GCS model bucket({self.gcs_storage.bucket_name}) not found, will be created.")
                self.gcs_storage.create_bucket()
            else:
                logger.info(f"GCS model bucket({self.gcs_storage.bucket_name}) already exists.")
        else:
            logger.info(f"The GCS model storage has been closed in the configuration.")

    def deploy_grpc_to_gke(self, project_dir, companies=None, versions=None, use_cloud_build=False):
        """
        Deploy gRPC service to GKE

        Args:
            project_dir: Project directory path
            companies: List of company codes (optional, will read from config if not provided)
            versions: List of versions (optional, will read from config if not provided)
            use_cloud_build: Use Cloud Build instead of local Docker

        Returns:
            True if successful, False otherwise
        """
        from pathlib import Path
        from aigear.deploy.gcp.gke_deployer import GKEDeployer

        if not self.gke_cluster:
            logger.error("GKE cluster is not configured in env.json")
            return False

        logger.info("=" * 60)
        logger.info("Deploying gRPC service to GKE")
        logger.info("=" * 60)

        # Get GKE configuration
        gke_config = {}
        if hasattr(self.aigear_config.gcp, 'gke'):
            gke_config = {
                'enabled': self.aigear_config.gcp.gke.on,
                'cluster': self.aigear_config.gcp.gke.cluster.__dict__ if hasattr(self.aigear_config.gcp.gke, 'cluster') else {},
                'deployment': self.aigear_config.gcp.gke.deployment.__dict__ if hasattr(self.aigear_config.gcp.gke, 'deployment') else {},
                'service': self.aigear_config.gcp.gke.service.__dict__ if hasattr(self.aigear_config.gcp.gke, 'service') else {},
                'image': self.aigear_config.gcp.gke.image.__dict__ if hasattr(self.aigear_config.gcp.gke, 'image') else {},
            }

        # Get project name
        project_name = self.aigear_config.project_name

        # Get companies and versions from config if not provided
        if not companies or not versions:
            # Try to read from grpc config
            if hasattr(self.aigear_config, 'grpc'):
                grpc_config = self.aigear_config.grpc
                if hasattr(grpc_config, 'servers'):
                    companies = companies or list(grpc_config.servers.keys())
                    # Get versions from first server
                    if companies and not versions:
                        first_server = grpc_config.servers[companies[0]]
                        if hasattr(first_server, 'modelPaths'):
                            versions = list(first_server.modelPaths.keys())

        if not companies or not versions:
            logger.error("Companies and versions must be provided or configured in env.json")
            return False

        # Create deployer
        deployer = GKEDeployer(
            project_name=project_name,
            project_id=self.aigear_config.gcp.gcp_project_id,
            region=self.aigear_config.gcp.location,
            companies=companies,
            versions=versions,
            project_dir=Path(project_dir),
            gke_config=gke_config,
        )

        # Deploy
        success = deployer.deploy(
            skip_build=False,
            skip_cluster_creation=True,  # Cluster already created in create()
            use_cloud_build=use_cloud_build,
        )

        if success:
            logger.info("=" * 60)
            logger.info("✅ gRPC service deployed successfully to GKE!")
            logger.info("=" * 60)
        else:
            logger.error("❌ Failed to deploy gRPC service to GKE")

        return success

"""
GKE Deployer Module

Orchestrates the complete deployment process to GKE:
1. Build and push Docker image
2. Create/update GKE cluster
3. Generate Kubernetes configurations
4. Deploy to GKE
5. Wait for service to be ready
"""

from pathlib import Path
from typing import Optional, Dict, List
import json
import time

from aigear.common import run_sh
from aigear.common.logger import Logging
from aigear.infrastructure.gcp.gke import GKECluster
from aigear.infrastructure.gcp.artifacts import Artifacts
from aigear.infrastructure.gcp.gcs import GCSModelStorage
from aigear.deploy.gcp.image_builder import ImageBuilder
from aigear.generators.k8s_generator import K8sConfigGenerator


logger = Logging(log_name=__name__).console_logging()


class GKEDeployer:
    """GKE Deployment Orchestrator"""

    def __init__(
        self,
        project_name: str,
        project_id: str,
        region: str,
        companies: List[str],
        versions: List[str],
        project_dir: Path,
        gke_config: Optional[Dict] = None,
    ):
        """
        Initialize GKE Deployer

        Args:
            project_name: Project name
            project_id: GCP project ID
            region: GCP region
            companies: List of company codes
            versions: List of versions
            project_dir: Project directory path
            gke_config: GKE configuration from env.json
        """
        self.project_name = project_name
        self.project_id = project_id
        self.region = region
        self.companies = companies
        self.versions = versions
        self.project_dir = Path(project_dir)
        self.gke_config = gke_config or self._default_gke_config()

        # Extract configuration
        cluster_config = self.gke_config.get('cluster', {})
        deployment_config = self.gke_config.get('deployment', {})
        image_config = self.gke_config.get('image', {})

        # Initialize components
        self.cluster_name = cluster_config.get('name', f"{project_name}-cluster")
        self.repository_name = image_config.get('repository', 'grpc-services')
        self.image_tag = image_config.get('tag', 'latest')

        # GKE Cluster
        self.gke_cluster = GKECluster(
            cluster_name=self.cluster_name,
            project_id=project_id,
            region=region,
            node_count=cluster_config.get('nodeCount', 3),
            machine_type=cluster_config.get('machineType', 'e2-standard-4'),
            disk_size=cluster_config.get('diskSize', 100),
            enable_autoscaling=cluster_config.get('enableAutoscaling', True),
            min_nodes=cluster_config.get('minNodes', 1),
            max_nodes=cluster_config.get('maxNodes', 10),
            enable_autopilot=cluster_config.get('enableAutopilot', False),
        )

        # Artifact Registry
        self.artifacts = Artifacts(
            repository_name=self.repository_name,
            location=region,
            project_id=project_id,
        )

        # Image Builder
        self.image_builder = ImageBuilder(
            project_id=project_id,
            repository_name=self.repository_name,
            location=region,
            image_name=project_name,
        )

        # GCS Storage (optional)
        self.gcs_storage = None
        if self.gke_config.get('modelStorage', {}).get('enabled', False):
            bucket_name = self.gke_config['modelStorage'].get('bucketName')
            if bucket_name:
                self.gcs_storage = GCSModelStorage(
                    bucket_name=bucket_name,
                    project_id=project_id,
                    location=region,
                )

        # K8s output directory
        self.k8s_dir = self.project_dir / "k8s"

    def _default_gke_config(self) -> Dict:
        """Get default GKE configuration"""
        return {
            "enabled": True,
            "cluster": {
                "name": f"{self.project_name}-cluster",
                "nodeCount": 3,
                "machineType": "e2-standard-4",
                "diskSize": 100,
                "enableAutoscaling": True,
                "minNodes": 1,
                "maxNodes": 10,
                "enableAutopilot": False,
            },
            "deployment": {
                "replicas": 2,
                "resources": {
                    "requests": {"cpu": "500m", "memory": "1Gi"},
                    "limits": {"cpu": "2000m", "memory": "4Gi"}
                },
                "autoscaling": {
                    "enabled": True,
                    "minReplicas": 2,
                    "maxReplicas": 10,
                    "targetCPU": 70
                }
            },
            "service": {
                "type": "LoadBalancer",
                "port": 50051
            },
            "image": {
                "repository": "grpc-services",
                "tag": "latest"
            }
        }

    def deploy(
        self,
        skip_build: bool = False,
        skip_cluster_creation: bool = False,
        use_cloud_build: bool = False,
    ) -> bool:
        """
        Execute complete deployment process

        Args:
            skip_build: Skip Docker image build
            skip_cluster_creation: Skip GKE cluster creation
            use_cloud_build: Use Cloud Build instead of local Docker

        Returns:
            True if successful, False otherwise
        """
        logger.info("=" * 60)
        logger.info("Starting GKE Deployment")
        logger.info(f"Project: {self.project_name}")
        logger.info(f"GCP Project: {self.project_id}")
        logger.info(f"Region: {self.region}")
        logger.info(f"Companies: {', '.join(self.companies)}")
        logger.info(f"Versions: {', '.join(self.versions)}")
        logger.info("=" * 60)

        try:
            # Step 1: Setup Artifact Registry
            if not self._setup_artifact_registry():
                return False

            # Step 2: Build and push Docker image
            if not skip_build:
                if not self._build_and_push_image(use_cloud_build):
                    return False
            else:
                logger.info("⏭ Skipping image build")

            # Step 3: Setup GKE cluster
            if not skip_cluster_creation:
                if not self._setup_gke_cluster():
                    return False
            else:
                logger.info("⏭ Skipping cluster creation")

            # Step 4: Get cluster credentials
            if not self.gke_cluster.get_credentials():
                return False

            # Step 5: Upload models to GCS (if enabled)
            if self.gcs_storage:
                if not self._upload_models_to_gcs():
                    logger.warning("⚠ Model upload failed, continuing anyway")

            # Step 6: Deploy services to GKE
            if not self._deploy_services():
                return False

            # Step 7: Wait for services to be ready
            if not self._wait_for_services():
                logger.warning("⚠ Some services may not be ready")

            # Step 8: Display service information
            self._display_service_info()

            logger.info("=" * 60)
            logger.info("✅ Deployment completed successfully!")
            logger.info("=" * 60)
            return True

        except Exception as e:
            logger.error(f"❌ Deployment failed: {str(e)}", exc_info=True)
            return False

    def _setup_artifact_registry(self) -> bool:
        """Setup Artifact Registry"""
        logger.info("\n📦 Step 1: Setting up Artifact Registry")

        # Check if repository exists
        if self.artifacts.describe():
            logger.info(f"✓ Artifact Registry repository already exists: {self.repository_name}")
            return True

        # Create repository
        logger.info(f"Creating Artifact Registry repository: {self.repository_name}")
        if not self.artifacts.create():
            logger.error("Failed to create Artifact Registry repository")
            return False

        logger.info("✓ Artifact Registry setup complete")
        return True

    def _build_and_push_image(self, use_cloud_build: bool = False) -> bool:
        """Build and push Docker image"""
        logger.info("\n🐳 Step 2: Building and pushing Docker image")

        if use_cloud_build:
            logger.info("Using Cloud Build (recommended for production)")
            success = self.image_builder.build_with_cloud_build(
                context_dir=self.project_dir,
                tag=self.image_tag,
            )
        else:
            logger.info("Using local Docker build")
            success = self.image_builder.build_and_push(
                context_dir=self.project_dir,
                tag=self.image_tag,
                additional_tags=["latest"] if self.image_tag != "latest" else None,
            )

        if not success:
            logger.error("Failed to build and push image")
            return False

        logger.info(f"✓ Image built and pushed: {self.image_builder.full_image_url}:{self.image_tag}")
        return True

    def _setup_gke_cluster(self) -> bool:
        """Setup GKE cluster"""
        logger.info("\n☸️  Step 3: Setting up GKE cluster")

        # Check if cluster exists
        if self.gke_cluster.describe():
            logger.info(f"✓ GKE cluster already exists: {self.cluster_name}")
            return True

        # Create cluster
        logger.info(f"Creating GKE cluster: {self.cluster_name}")
        logger.info("⏳ This may take 5-10 minutes...")

        if not self.gke_cluster.create():
            logger.error("Failed to create GKE cluster")
            return False

        logger.info("✓ GKE cluster setup complete")
        return True

    def _upload_models_to_gcs(self) -> bool:
        """Upload model files to GCS"""
        logger.info("\n📤 Step 5: Uploading models to GCS")

        if not self.gcs_storage:
            logger.info("⏭ GCS model storage not configured")
            return True

        # Create bucket if needed
        if not self.gcs_storage.bucket_exists():
            if not self.gcs_storage.create_bucket():
                return False

        # Upload models directory
        models_dir = self.project_dir / "models"
        if models_dir.exists():
            logger.info(f"Uploading models from {models_dir}")
            if not self.gcs_storage.sync_directory(models_dir, "models/"):
                return False
            logger.info("✓ Models uploaded to GCS")
        else:
            logger.warning(f"⚠ Models directory not found: {models_dir}")

        return True

    def _deploy_services(self) -> bool:
        """Deploy services to GKE"""
        logger.info("\n🚀 Step 6: Deploying services to GKE")

        # Load env.json
        env_file = self.project_dir / "env.json"
        if not env_file.exists():
            logger.error("env.json not found")
            return False

        with open(env_file, 'r', encoding='utf-8') as f:
            env_config = json.load(f)

        deployment_config = self.gke_config.get('deployment', {})
        service_config = self.gke_config.get('service', {})

        # Deploy each company-version combination
        for company in self.companies:
            for version in self.versions:
                logger.info(f"\n  Deploying {company}/{version}...")

                # Generate K8s configurations
                k8s_generator = K8sConfigGenerator(
                    project_name=self.project_name,
                    company=company,
                    version=version,
                    image_url=f"{self.image_builder.full_image_url}:{self.image_tag}",
                    port=service_config.get('port', 50051),
                    replicas=deployment_config.get('replicas', 2),
                    resources=deployment_config.get('resources'),
                    autoscaling=deployment_config.get('autoscaling'),
                    service_type=service_config.get('type', 'LoadBalancer'),
                    output_dir=self.k8s_dir,
                )

                # Generate all K8s configs
                generated_files = k8s_generator.generate_all(env_config)
                logger.info(f"  ✓ Generated {len(generated_files)} K8s configuration files")

                # Apply configurations
                for config_file in generated_files:
                    if not self._apply_k8s_config(config_file):
                        logger.error(f"  ❌ Failed to apply: {config_file}")
                        return False
                    logger.info(f"  ✓ Applied: {config_file.name}")

        logger.info("\n✓ All services deployed")
        return True

    def _apply_k8s_config(self, config_file: Path) -> bool:
        """Apply Kubernetes configuration"""
        command = [
            "kubectl", "apply",
            "-f", str(config_file),
        ]

        try:
            event = run_sh(command)

            if "ERROR" in event or "error" in event.lower():
                logger.error(f"Failed to apply config: {event}")
                return False

            return True

        except Exception as e:
            logger.error(f"Error applying config: {str(e)}")
            return False

    def _wait_for_services(self, timeout: int = 300) -> bool:
        """Wait for services to be ready"""
        logger.info("\n⏳ Step 7: Waiting for services to be ready")

        for company in self.companies:
            for version in self.versions:
                service_name = f"{self.project_name}-{company}-{version}"
                logger.info(f"  Waiting for {service_name}...")

                # Wait for deployment to be ready
                command = [
                    "kubectl", "wait",
                    "--for=condition=available",
                    "--timeout={}s".format(timeout),
                    f"deployment/{service_name}",
                ]

                try:
                    event = run_sh(command, timeout=timeout * 1000)

                    if "ERROR" in event or "error" in event.lower():
                        logger.warning(f"  ⚠ Service may not be ready: {service_name}")
                        continue

                    logger.info(f"  ✓ {service_name} is ready")

                except Exception as e:
                    logger.warning(f"  ⚠ Error waiting for service: {str(e)}")

        return True

    def _display_service_info(self):
        """Display service information"""
        logger.info("\n📋 Service Information:")

        for company in self.companies:
            for version in self.versions:
                service_name = f"{self.project_name}-{company}-{version}"

                # Get service details
                command = [
                    "kubectl", "get", "service",
                    service_name,
                    "-o", "json",
                ]

                try:
                    event = run_sh(command)
                    service_info = json.loads(event)

                    # Extract external IP
                    load_balancer = service_info.get('status', {}).get('loadBalancer', {})
                    ingress = load_balancer.get('ingress', [])

                    if ingress:
                        external_ip = ingress[0].get('ip', 'Pending')
                        port = service_info['spec']['ports'][0]['port']

                        logger.info(f"\n  🌐 {service_name}:")
                        logger.info(f"     External IP: {external_ip}")
                        logger.info(f"     Port: {port}")
                        logger.info(f"     gRPC URL: {external_ip}:{port}")
                    else:
                        logger.info(f"\n  🌐 {service_name}: External IP pending...")

                except Exception as e:
                    logger.warning(f"  ⚠ Could not get service info: {str(e)}")

    def delete_deployment(self) -> bool:
        """Delete all deployed resources"""
        logger.info("🗑️  Deleting deployment")

        # Delete all K8s resources
        if self.k8s_dir.exists():
            command = [
                "kubectl", "delete",
                "-f", str(self.k8s_dir),
                "--recursive",
            ]

            try:
                event = run_sh(command)
                logger.info(event)
                logger.info("✓ Kubernetes resources deleted")
            except Exception as e:
                logger.error(f"Error deleting K8s resources: {str(e)}")

        # Optionally delete cluster
        logger.info("\nTo delete the GKE cluster, run:")
        logger.info(f"  gcloud container clusters delete {self.cluster_name} --region={self.region} --project={self.project_id}")

        return True

    def get_deployment_status(self) -> Dict:
        """Get deployment status"""
        status = {
            "cluster": self.cluster_name,
            "services": []
        }

        for company in self.companies:
            for version in self.versions:
                service_name = f"{self.project_name}-{company}-{version}"

                command = [
                    "kubectl", "get", "deployment",
                    service_name,
                    "-o", "json",
                ]

                try:
                    event = run_sh(command)
                    deployment_info = json.loads(event)

                    status["services"].append({
                        "name": service_name,
                        "company": company,
                        "version": version,
                        "replicas": deployment_info['spec']['replicas'],
                        "ready": deployment_info['status'].get('readyReplicas', 0),
                        "available": deployment_info['status'].get('availableReplicas', 0),
                    })

                except Exception as e:
                    logger.warning(f"Could not get status for {service_name}: {str(e)}")

        return status


if __name__ == "__main__":
    # Example usage
    deployer = GKEDeployer(
        project_name="my-alc-service",
        project_id="my-project",
        region="asia-northeast1",
        companies=["trial", "aeon"],
        versions=["alc3", "alc4"],
        project_dir=Path("."),
    )

    # Deploy
    deployer.deploy(use_cloud_build=True)

    # Check status
    status = deployer.get_deployment_status()
    print(json.dumps(status, indent=2))

import json
from aigear.common.logger import Logging
from aigear.common.sh import run_sh
from aigear.common.config import AigearConfig
from aigear.infrastructure.gcp.bucket import Bucket
from aigear.infrastructure.gcp.build import CloudBuild
from aigear.infrastructure.gcp.function import CloudFunction
from aigear.infrastructure.gcp.iam import ServiceAccounts
from aigear.infrastructure.gcp.pub_sub import PubSub
from aigear.infrastructure.gcp.artifacts import Artifacts
from aigear.infrastructure.gcp.pre_vm_image import PreVMImage
from aigear.infrastructure.gcp.constant import entry_point_of_cloud_fuction
from aigear.infrastructure.gcp.kubernetes import KubernetesCluster

logger = Logging(log_name=__name__).console_logging()


class Infra:
    """
    Extended version with:
      - gcloud login check
      - project ID enforcement
      - step-by-step logging
      - idempotent resource verification
    """

    def __init__(self):
        self.aigear_config = AigearConfig.get_config()

        self.project_id = self.aigear_config.gcp.gcp_project_id
        self.location = self.aigear_config.gcp.location

        # ------- Instantiate all modules -------
        self.service_account = (
            f"{self.aigear_config.gcp.iam.account_name}"
            f"@{self.aigear_config.gcp.gcp_project_id}.iam.gserviceaccount.com"
        )

        self.model_bucket = Bucket(
            bucket_name=self.aigear_config.gcp.bucket.bucket_name,
            location=self.location,
            project_id=self.project_id,
        )

        self.release_model_bucket = Bucket(
            bucket_name=self.aigear_config.gcp.bucket.bucket_name_for_release,
            location=self.location,
            project_id=self.project_id,
        )

        self.cloud_build = CloudBuild(
            trigger_name=self.aigear_config.gcp.cloud_build.trigger_name,
            description=self.aigear_config.gcp.cloud_build.description,
            repo_owner=self.aigear_config.gcp.cloud_build.repo_owner,
            repo_name=self.aigear_config.gcp.cloud_build.repo_name,
            branch_pattern=self.aigear_config.gcp.cloud_build.branch_pattern,
            build_config=self.aigear_config.gcp.cloud_build.build_config,
            region=self.location,
            substitutions=self.aigear_config.gcp.cloud_build.substitutions,
            project_id=self.project_id,
        )

        self.cloud_function = CloudFunction(
            function_name=self.aigear_config.gcp.cloud_function.function_name,
            region=self.location,
            entry_point=entry_point_of_cloud_fuction,
            topic_name=self.aigear_config.gcp.pub_sub.topic_name,
            project_id=self.project_id,
            service_account=self.service_account,
        )

        self.service_accounts = ServiceAccounts(
            project_id=self.project_id,
            account_name=self.aigear_config.gcp.iam.account_name,
        )

        self.pubsub = PubSub(
            topic_name=self.aigear_config.gcp.pub_sub.topic_name,
            project_id=self.project_id,
        )

        self.artifacts = Artifacts(
            repository_name=self.aigear_config.gcp.artifacts.repository_name,
            location=self.location,
            project_id=self.project_id,
        )

        self.pre_vm_image = PreVMImage(
            project_id=self.project_id,
            zone=self.location,
            machine_type=self.aigear_config.gcp.pre_vm_image.machine_type,
            gpu_type=self.aigear_config.gcp.pre_vm_image.gpu_type,
            gpu_count=self.aigear_config.gcp.pre_vm_image.gpu_count,
            boot_disk_gb=self.aigear_config.gcp.pre_vm_image.boot_disk_gb,
            dlvm_family=self.aigear_config.gcp.pre_vm_image.dlvm_family,
            bake_vm=self.aigear_config.gcp.pre_vm_image.bake_vm,
            custom_image_name=self.aigear_config.gcp.pre_vm_image.custom_image_name,
            bake_timeout_sec=self.aigear_config.gcp.pre_vm_image.bake_timeout_sec,
            bake_poll_interval_sec=self.aigear_config.gcp.pre_vm_image.bake_poll_interval_sec,
        )

        self.kubernetes_cluster = KubernetesCluster(
            cluster_name=self.aigear_config.gcp.kubernetes.cluster_name,
            zone=self.location,
            num_nodes=self.aigear_config.gcp.kubernetes.num_nodes,
            min_nodes=self.aigear_config.gcp.kubernetes.min_nodes,
            max_nodes=self.aigear_config.gcp.kubernetes.max_nodes,
            project_id=self.project_id,
        )

    # ================================================================
    # Preflight checks: gcloud login + project switch
    # ================================================================
    def gcloud_login_check(self):
        """Check gcloud CLI installation and authentication status."""
        logger.info("===================================================")
        logger.info("             Aigear GCP Login Check                ")
        logger.info("===================================================")

        # 1. check gcloud installed
        try:
            run_sh(["gcloud", "--version"])
        except Exception:
            raise RuntimeError("`gcloud` CLI not found. Install Google Cloud SDK first.")

        # 2. check login
        try:
            output = run_sh(["gcloud", "auth", "list", "--format=json"])
            accounts = json.loads(output)
            active = [a for a in accounts if a.get("status") == "ACTIVE"]
        except Exception as e:
            raise RuntimeError(f"Cannot check gcloud auth status: {e}")

        if not active:
            logger.info("No active gcloud account detected. Running `gcloud auth login`...")
            run_sh(["gcloud", "auth", "login"])
        else:
            logger.info(f"Logged in as: {active[0]['account']}")

        logger.info("Login check OK.\n")

    def project_switch(self):
        """Ensure gcloud project matches the configured project ID."""
        logger.info("===================================================")
        logger.info("             Aigear GCP Project Switch             ")
        logger.info("===================================================")

        current_project = run_sh(["gcloud", "config", "get-value", "project"]).strip()
        if current_project != self.project_id:
            logger.info(f"Switching gcloud project -> {self.project_id}")
            run_sh(["gcloud", "config", "set", "project", self.project_id])
        else:
            logger.info(f"Project already set to {self.project_id}")

        logger.info("Project switch OK.\n")

    # ================================================================
    # Generic step wrapper for prettier logs (non-blocking on failure)
    # ================================================================
    def _step(self, title, fn):
        logger.info("\n---------------------------------------------------")
        logger.info(f"[{title}]")
        logger.info("---------------------------------------------------")

        try:
            fn()
            logger.info(f"✔ {title} SUCCESS")
            return True
        except Exception as e:
            logger.error(f"✖ {title} FAILED")
            logger.error(f"Error details: {str(e)}")
            logger.error(f"Error type: {type(e).__name__}")
            # Do not re-raise - continue with other infrastructure creation
            return False

    # ================================================================
    # Public API called from CLI
    # ================================================================
    def create(self):
        self.gcloud_login_check()
        self.project_switch()

        failed_steps = []

        # IAM
        if self.aigear_config.gcp.iam.on:
            success = self._step(
                f"Service Account ({self.aigear_config.gcp.iam.account_name})",
                self._ensure_service_account
            )
            if not success:
                failed_steps.append(f"Service Account ({self.aigear_config.gcp.iam.account_name})")
        else:
            logger.info(
                f"Service Account creation is disabled in the configuration file. "
                f"Skipping service account ({self.aigear_config.gcp.iam.account_name}) setup."
            )

        # Buckets
        if self.aigear_config.gcp.bucket.on:
            success = self._step(
                f"Model Bucket ({self.aigear_config.gcp.bucket.bucket_name})",
                self._ensure_model_bucket
            )
            if not success:
                failed_steps.append(f"Model Bucket ({self.aigear_config.gcp.bucket.bucket_name})")

            success = self._step(
                f"Release Model Bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release})",
                self._ensure_release_bucket
            )
            if not success:
                failed_steps.append(f"Release Model Bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release})")
        else:
            logger.info(
                f"Bucket creation is disabled in the configuration file. "
                f"Skipping model bucket ({self.aigear_config.gcp.bucket.bucket_name}) and "
                f"release bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release}) setup."
            )

        # Artifact Registry
        if self.aigear_config.gcp.artifacts.on:
            success = self._step(
                f"Artifact Registry ({self.aigear_config.gcp.artifacts.repository_name})",
                self._ensure_artifacts
            )
            if not success:
                failed_steps.append(f"Artifact Registry ({self.aigear_config.gcp.artifacts.repository_name})")
        else:
            logger.info(
                f"Artifact Registry creation is disabled in the configuration file. "
                f"Skipping artifact repository ({self.aigear_config.gcp.artifacts.repository_name}) setup."
            )

        # Pub/Sub
        if self.aigear_config.gcp.pub_sub.on:
            success = self._step(
                f"Pub/Sub Topic ({self.aigear_config.gcp.pub_sub.topic_name})",
                self._ensure_pubsub
            )
            if not success:
                failed_steps.append(f"Pub/Sub Topic ({self.aigear_config.gcp.pub_sub.topic_name})")
        else:
            logger.info(
                f"Pub/Sub creation is disabled in the configuration file. "
                f"Skipping Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) setup."
            )

        # Cloud Build
        if self.aigear_config.gcp.cloud_build.on:
            success = self._step(
                f"Cloud Build Trigger ({self.aigear_config.gcp.cloud_build.trigger_name})",
                self._ensure_cloud_build
            )
            if not success:
                failed_steps.append(f"Cloud Build Trigger ({self.aigear_config.gcp.cloud_build.trigger_name})")
        else:
            logger.info(
                f"Cloud Build creation is disabled in the configuration file. "
                f"Skipping Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) setup."
            )

        # Cloud Function
        if self.aigear_config.gcp.cloud_function.on:
            success = self._step(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name})",
                self._ensure_cloud_function
            )
            if not success:
                failed_steps.append(f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name})")
        else:
            logger.info(
                f"Cloud Function deployment is disabled in the configuration file. "
                f"Skipping Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) setup."
            )

        # Pre-VM Image
        if self.aigear_config.gcp.pre_vm_image.on:
            success = self._step(
                f"Pre-VM Image ({self.aigear_config.gcp.pre_vm_image.custom_image_name})",
                self._ensure_pre_vm_image
            )
            if not success:
                failed_steps.append(f"Pre-VM Image ({self.aigear_config.gcp.pre_vm_image.custom_image_name})")
        else:
            logger.info(
                f"Pre-VM Image creation is disabled in the configuration file. "
                f"Skipping custom VM image ({self.aigear_config.gcp.pre_vm_image.custom_image_name}) creation."
            )

        # Kubernetes Cluster
        if self.aigear_config.gcp.kubernetes.on:
            success = self._step(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name})",
                self._ensure_kubernetes_cluster
            )
            if not success:
                failed_steps.append(f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name})")
        else:
            logger.info(
                f"Kubernetes Cluster creation is disabled in the configuration file. "
                f"Skipping Kubernetes Cluster ({self.aigear_config.gcp.cloud_function.function_name}) setup."
            )

        # Summary
        logger.info("\n===================================================")
        if failed_steps:
            logger.warning("       Aigear GCP Infra Init Complete (with errors)")
            logger.warning("===================================================")
            logger.warning("The following steps failed:")
            for step in failed_steps:
                logger.warning(f"  - {step}")
            logger.info("Please review the errors above and retry the failed steps.\n")
        else:
            logger.info("            Aigear GCP Infra Init Complete         ")
            logger.info("===================================================\n")

    # ================================================================
    # Actual infra actions (use your existing classes)
    # ================================================================
    def _ensure_service_account(self):
        exists = self.service_accounts.describe()
        if not exists:
            logger.info(
                f"Service account ({self.aigear_config.gcp.iam.account_name}) not found in project "
                f"({self.project_id}). Creating service account and binding IAM policies..."
            )
            self.service_accounts.create()
            self.service_accounts.add_iam_policy_binding()
            logger.info(f"Service account ({self.aigear_config.gcp.iam.account_name}) created successfully.")
        else:
            logger.info(
                f"Service account ({self.aigear_config.gcp.iam.account_name}) already exists in project "
                f"({self.project_id}). Skipping creation."
            )

    def _ensure_model_bucket(self):
        exists = self.model_bucket.describe()
        if not exists:
            logger.info(
                f"Model bucket ({self.aigear_config.gcp.bucket.bucket_name}) not found in location "
                f"({self.location}). Creating bucket..."
            )
            self.model_bucket.create()
            logger.info(f"Model bucket ({self.aigear_config.gcp.bucket.bucket_name}) created successfully.")
        else:
            logger.info(
                f"Model bucket ({self.aigear_config.gcp.bucket.bucket_name}) already exists in location "
                f"({self.location}). Skipping creation."
            )

    def _ensure_release_bucket(self):
        exists = self.release_model_bucket.describe()
        if not exists:
            logger.info(
                f"Release model bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release}) not found in "
                f"location ({self.location}). Creating bucket..."
            )
            self.release_model_bucket.create()
            logger.info(
                f"Release model bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release}) created successfully."
            )
        else:
            logger.info(
                f"Release model bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release}) already exists in "
                f"location ({self.location}). Skipping creation."
            )

    def _ensure_artifacts(self):
        exists = self.artifacts.describe()
        if not exists:
            logger.info(
                f"Artifact registry ({self.aigear_config.gcp.artifacts.repository_name}) not found in location "
                f"({self.location}). Creating artifact repository..."
            )
            self.artifacts.create()
            logger.info(
                f"Artifact registry ({self.aigear_config.gcp.artifacts.repository_name}) created successfully."
            )
        else:
            logger.info(
                f"Artifact registry ({self.aigear_config.gcp.artifacts.repository_name}) already exists in location "
                f"({self.location}). Skipping creation."
            )

    def _ensure_pubsub(self):
        exists = self.pubsub.describe()
        if not exists:
            logger.info(
                f"Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) not found in project "
                f"({self.project_id}). Creating topic..."
            )
            self.pubsub.create()
            logger.info(f"Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) created successfully.")
        else:
            logger.info(
                f"Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) already exists in project "
                f"({self.project_id}). Skipping creation."
            )

    def _ensure_cloud_build(self):
        exists = self.cloud_build.describe()
        if not exists:
            logger.info(
                f"Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) not found in region "
                f"({self.location}). Creating Cloud Build trigger..."
            )
            self.cloud_build.create()
            logger.info(
                f"Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) created successfully."
            )
        else:
            logger.info(
                f"Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) already exists in region "
                f"({self.location}). Skipping creation."
            )

    def _ensure_cloud_function(self):
        exists = self.cloud_function.describe()
        if not exists:
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) not found in region "
                f"({self.location}). Deploying Cloud Function..."
            )
            self.cloud_function.deploy()
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) deployed successfully."
            )
        else:
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) already exists in region "
                f"({self.location}). Skipping deployment."
            )

    def _ensure_pre_vm_image(self):
        exists = self.pre_vm_image.image_exists()
        if not exists:
            logger.info(
                f"Pre-VM custom image ({self.aigear_config.gcp.pre_vm_image.custom_image_name}) not found in project "
                f"({self.project_id}). Starting VM image creation process (this may take several minutes)..."
            )
            self.pre_vm_image.create_vm_image()
            logger.info(
                f"Pre-VM custom image ({self.aigear_config.gcp.pre_vm_image.custom_image_name}) created successfully."
            )
        else:
            logger.info(
                f"Pre-VM custom image ({self.aigear_config.gcp.pre_vm_image.custom_image_name}) already exists in "
                f"project ({self.project_id}). Skipping creation."
            )

    def _ensure_kubernetes_cluster(self):
        exists = self.kubernetes_cluster.describe()
        if not exists:
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) not found in region "
                f"({self.location}). Creating Kubernetes Cluster..."
            )
            self.kubernetes_cluster.cluster_name()
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) created successfully."
            )
        else:
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.cloud_function.function_name}) already exists in region "
                f"({self.location})."
            )
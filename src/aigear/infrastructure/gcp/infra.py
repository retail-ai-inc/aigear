import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from aigear.common import run_sh
from aigear.common.config import AigearConfig, AppConfig
from aigear.common.image import get_image_name
from aigear.common.logger import Logging, _thread_local
from aigear.infrastructure.gcp.artifacts import Artifacts
from aigear.infrastructure.gcp.bucket import Bucket
from aigear.infrastructure.gcp.build import CloudBuild
from aigear.infrastructure.gcp.constant import entry_point_of_cloud_fuction
from aigear.infrastructure.gcp.function import CloudFunction
from aigear.infrastructure.gcp.iam import ServiceAccounts
from aigear.infrastructure.gcp.kms import CloudKMS
from aigear.infrastructure.gcp.kubernetes import KubernetesCluster
from aigear.infrastructure.gcp.pub_sub import PubSub

logger = Logging(log_name=__name__).console_logging()
_log_lock = threading.Lock()


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

        env = AppConfig.environment()
        if env not in ("staging", "production"):
            logger.warning(
                f"Current environment is '{env}'. "
                f"'local' is intended for local development only and its parameters (e.g. project_id, bucket names) "
                f"may not be trustworthy for GCP deployment. "
                f"GCP deployment requires environment to be 'staging' or 'production'."
            )
        self.environment = env if env in ("staging", "production") else "staging"

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
            event=self.aigear_config.gcp.cloud_build.event,
            branch_pattern=self.aigear_config.gcp.cloud_build.branch_pattern,
            tag_pattern=self.aigear_config.gcp.cloud_build.tag_pattern,
            region=self.location,
            project_id=self.project_id,
            substitutions=self._build_substitutions(),
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

        self.kubernetes_cluster = KubernetesCluster(
            cluster_name=self.aigear_config.gcp.kubernetes.cluster_name,
            zone=self.location,
            num_nodes=self.aigear_config.gcp.kubernetes.num_nodes,
            min_nodes=self.aigear_config.gcp.kubernetes.min_nodes,
            max_nodes=self.aigear_config.gcp.kubernetes.max_nodes,
            project_id=self.project_id,
        )

        self.cloud_kms = CloudKMS(
            project_id=self.project_id,
            location=self.location,
            keyring_name=self.aigear_config.gcp.kms.keyring_name,
            key_name=self.aigear_config.gcp.kms.key_name,
        )

    # ================================================================
    # Preflight checks: gcloud login + project switch
    # ================================================================
    def _preflight_check(self):
        """Verify gcloud is installed, then check auth and project in parallel."""
        logger.info("===================================================")
        logger.info("             Aigear GCP Environment Check          ")
        logger.info("===================================================")

        # Step 1: confirm gcloud is installed (prerequisite for everything below)
        try:
            run_sh(["gcloud", "--version"])
        except Exception:
            raise RuntimeError(
                "`gcloud` CLI not found. Install Google Cloud SDK first."
            )

        # Step 2: auth check and project check are independent — run in parallel
        auth_result: list = []
        project_result: list = []

        def _check_auth():
            try:
                output = run_sh(["gcloud", "auth", "list", "--format=json"])
                accounts = json.loads(output)
                auth_result.extend([a for a in accounts if a.get("status") == "ACTIVE"])
            except Exception as e:
                raise RuntimeError(f"Cannot check gcloud auth status: {e}")

        def _check_project():
            current = run_sh(["gcloud", "config", "get-value", "project"]).strip()
            project_result.append(current)

        with ThreadPoolExecutor(max_workers=2) as executor:
            f_auth = executor.submit(_check_auth)
            f_proj = executor.submit(_check_project)
            f_auth.result()
            f_proj.result()

        # Step 3: act on results (interactive / config change — must be sequential)
        if not auth_result:
            logger.info(
                "No active gcloud account detected. Running `gcloud auth login`..."
            )
            run_sh(["gcloud", "auth", "login"])
        else:
            logger.info(f"Logged in as: {auth_result[0]['account']}")
        logger.info("✅ Login check OK.")

        current_project = project_result[0]
        if current_project != self.project_id:
            logger.info(f"Switching gcloud project -> {self.project_id}")
            run_sh(["gcloud", "config", "set", "project", self.project_id])
        else:
            logger.info(f"Project already set to {self.project_id}")
        logger.info("✅ Project switch OK.")

    # ================================================================
    # Generic step wrapper for prettier logs (non-blocking on failure)
    # ================================================================
    def _build_substitutions(self) -> str:
        artifacts = self.aigear_config.gcp.artifacts
        return ",".join(
            [
                f"_ENVIRONMENT={self.environment}",
                f"_KMS_KEYRING={self.aigear_config.gcp.kms.keyring_name}",
                f"_KMS_KEY={self.aigear_config.gcp.kms.key_name}",
                f"_REPOSITORY={artifacts.repository_name}",
                f"_MS_IMAGE_NAME={get_image_name(is_service=True)}",
                f"_PL_IMAGE_NAME={get_image_name(is_service=False)}",
                f"_IMAGE_TAG={artifacts.image_tag}",
            ]
        )

    def _step(self, title, fn):
        _thread_local.log_buffer = []
        try:
            fn()
            success = True
            exc = None
        except Exception as e:
            success = False
            exc = e

        messages = _thread_local.log_buffer
        _thread_local.log_buffer = None

        with _log_lock:
            logger.info(f"[{title}]")
            for msg in messages:
                print(msg, flush=True)
            if success:
                logger.info(f"✅ {title} SUCCESS")
            else:
                logger.error(f"❌ {title} FAILED")
                logger.error(f"Error details: {str(exc)}")
                logger.error(f"Error type: {type(exc).__name__}")
            logger.info("---------------------------------------------------")
        return success

    def _step_skip(self, title):
        logger.info(f"[{title}]")
        logger.info(f"⚠️ {title} SKIPPED (disabled in configuration)")
        logger.info("---------------------------------------------------")

    def _step_no_update(self, title):
        logger.info(f"[{title}]")
        logger.info(f"⚠️ {title} SKIPPED (update not supported)")
        logger.info("---------------------------------------------------")

    def _step_fail(self, title, reason):
        logger.info(f"[{title}]")
        logger.error(f"❌ {title} FAILED ({reason})")
        logger.info("---------------------------------------------------")

    def _log_summary(self, failed_steps, title):
        logger.info("===================================================")
        if failed_steps:
            logger.warning(f"     Aigear GCP Infra {title} Complete (with errors)")
            logger.warning("===================================================")
            logger.warning("The following steps failed:")
            for step in failed_steps:
                logger.warning(f"  - {step}")
            logger.info("Please review the errors above and retry the failed steps.")
        else:
            logger.info(f"          Aigear GCP Infra {title} Complete          ")
            logger.info("===================================================")

    # ================================================================
    # Public API called from CLI
    # ================================================================
    def create(self):
        self._preflight_check()

        logger.info("===================================================")
        logger.info("             Aigear GCP Infra Creating             ")
        logger.info("===================================================")

        failed_steps = []
        cfg = self.aigear_config.gcp

        # ── Phase 1: Service Account (must be first) ─────────────────
        sa_exists = False
        if cfg.iam.on:
            success = self._step(
                f"Service Account ({cfg.iam.account_name})",
                self._ensure_service_account,
            )
            if not success:
                failed_steps.append(f"Service Account ({cfg.iam.account_name})")
            else:
                sa_exists = True  # step succeeded → SA is guaranteed to exist
        else:
            self._step_skip(f"Service Account ({cfg.iam.account_name})")
            sa_exists = self.service_accounts.describe()  # iam off → must verify

        # ── Gate 1→2: Service Account must exist ─────────────────────
        if not sa_exists:
            self._step_fail(
                f"Gate 1→2: Service Account ({cfg.iam.account_name})",
                "not found — Phase 2 and Phase 3 skipped",
            )
            failed_steps.append(
                f"Gate 1→2: Service Account ({cfg.iam.account_name}) not found"
            )
            self._log_summary(failed_steps, "Init")
            return

        # ── Phase 2: Independent resources (parallel) ─────────────────
        phase2_tasks = {}

        if cfg.bucket.on:
            phase2_tasks[f"Model Bucket ({cfg.bucket.bucket_name})"] = (
                self._ensure_model_bucket
            )
            phase2_tasks[
                f"Release Model Bucket ({cfg.bucket.bucket_name_for_release})"
            ] = self._ensure_release_bucket
        else:
            self._step_skip(f"Model Bucket ({cfg.bucket.bucket_name})")
            self._step_skip(
                f"Release Model Bucket ({cfg.bucket.bucket_name_for_release})"
            )

        if cfg.artifacts.on:
            phase2_tasks[f"Artifact Registry ({cfg.artifacts.repository_name})"] = (
                self._ensure_artifacts
            )
        else:
            self._step_skip(f"Artifact Registry ({cfg.artifacts.repository_name})")

        if cfg.pub_sub.on:
            phase2_tasks[f"Pub/Sub Topic ({cfg.pub_sub.topic_name})"] = (
                self._ensure_pubsub
            )
        else:
            self._step_skip(f"Pub/Sub Topic ({cfg.pub_sub.topic_name})")

        if cfg.kms.on:
            phase2_tasks[f"Cloud KMS ({cfg.kms.keyring_name}/{cfg.kms.key_name})"] = (
                self._ensure_kms
            )
        else:
            self._step_skip(f"Cloud KMS ({cfg.kms.keyring_name}/{cfg.kms.key_name})")

        if cfg.cloud_build.on:
            phase2_tasks[f"Cloud Build Trigger ({cfg.cloud_build.trigger_name})"] = (
                self._ensure_cloud_build
            )
        else:
            self._step_skip(f"Cloud Build Trigger ({cfg.cloud_build.trigger_name})")

        if cfg.pre_vm_image.on:
            phase2_tasks["Pre-VM Image (pre_vm_image)"] = self._ensure_pre_vm_image
        else:
            self._step_skip("Pre-VM Image (pre_vm_image)")

        if cfg.kubernetes.on:
            phase2_tasks[f"Kubernetes Cluster ({cfg.kubernetes.cluster_name})"] = (
                self._ensure_kubernetes_cluster
            )
        else:
            self._step_skip(f"Kubernetes Cluster ({cfg.kubernetes.cluster_name})")

        if phase2_tasks:
            with ThreadPoolExecutor(max_workers=len(phase2_tasks)) as executor:
                futures = {
                    executor.submit(self._step, title, fn): title
                    for title, fn in phase2_tasks.items()
                }
                for future in as_completed(futures):
                    title = futures[future]
                    if not future.result():
                        failed_steps.append(title)

        # ── Gate 2→3: Pub/Sub must exist ──────────────────────────────
        # Phase 2 already verified pubsub state; reuse that result instead of
        # calling describe() again.
        pubsub_step_key = f"Pub/Sub Topic ({cfg.pub_sub.topic_name})"
        pubsub_ok = cfg.pub_sub.on and pubsub_step_key not in failed_steps
        if cfg.cloud_function.on and not pubsub_ok:
            self._step_fail(
                f"Gate 2→3: Pub/Sub Topic ({cfg.pub_sub.topic_name})",
                "not found — Phase 3 skipped",
            )
            failed_steps.append(
                f"Gate 2→3: Pub/Sub Topic ({cfg.pub_sub.topic_name}) not found"
            )
            self._log_summary(failed_steps, "Init")
            return

        # ── Phase 3: Cloud Function ────────────────────────────────────
        if cfg.cloud_function.on:
            success = self._step(
                f"Cloud Function ({cfg.cloud_function.function_name})",
                self._ensure_cloud_function,
            )
            if not success:
                failed_steps.append(
                    f"Cloud Function ({cfg.cloud_function.function_name})"
                )
        else:
            self._step_skip(f"Cloud Function ({cfg.cloud_function.function_name})")

        self._log_summary(failed_steps, "Init")

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
            logger.info(
                f"Service account ({self.aigear_config.gcp.iam.account_name}) created successfully."
            )
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
            self.model_bucket.add_permissions_to_gcs(
                sa_email=self.service_accounts.sa_email
            )
            logger.info(
                f"Model bucket ({self.aigear_config.gcp.bucket.bucket_name}) created successfully."
            )
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
            self.release_model_bucket.add_permissions_to_gcs(
                sa_email=self.service_accounts.sa_email
            )
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
            self.pubsub.add_permissions_to_pubsub(
                sa_email=self.service_accounts.sa_email
            )
            logger.info(
                f"Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) created successfully."
            )
        else:
            logger.info(
                f"Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) already exists in project "
                f"({self.project_id}). Skipping creation."
            )

    def _ensure_kms(self):
        if not self.cloud_kms.describe_keyring():
            logger.info(
                f"KMS keyring ({self.aigear_config.gcp.kms.keyring_name}) not found in location "
                f"({self.location}). Creating keyring..."
            )
            self.cloud_kms.create_keyring()
            logger.info(
                f"KMS keyring ({self.aigear_config.gcp.kms.keyring_name}) created successfully."
            )
        else:
            logger.info(
                f"KMS keyring ({self.aigear_config.gcp.kms.keyring_name}) already exists in location "
                f"({self.location}). Skipping creation."
            )

        if not self.cloud_kms.describe_key():
            logger.info(
                f"KMS key ({self.aigear_config.gcp.kms.key_name}) not found. Creating key..."
            )
            self.cloud_kms.create_key()
            self.cloud_kms.add_permissions(sa_email=self.service_account)
            logger.info(
                f"KMS key ({self.aigear_config.gcp.kms.key_name}) created successfully."
            )
        elif not self.cloud_kms.describe_enabled_key_version():
            logger.info(
                f"KMS key ({self.aigear_config.gcp.kms.key_name}) exists but has no enabled versions. "
                f"Restoring and enabling latest available version..."
            )
            self.cloud_kms.enable_primary_key_version()
            logger.info(
                f"KMS key ({self.aigear_config.gcp.kms.key_name}) re-enabled successfully."
            )
        else:
            logger.info(
                f"KMS key ({self.aigear_config.gcp.kms.key_name}) already exists. Skipping creation."
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

    def _update_cloud_build(self):
        exists = self.cloud_build.describe()
        if not exists:
            logger.warning(
                f"Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) not found in region "
                f"({self.location}). Skipping update — run --create first."
            )
            return
        logger.info(
            f"Updating Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name})..."
        )
        self.cloud_build.update()
        logger.info(
            f"Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) updated successfully."
        )

    def _ensure_cloud_function(self):
        exists = self.cloud_function.describe()
        if not exists:
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) not found in region "
                f"({self.location}). Deploying Cloud Function..."
            )
            self.cloud_function.deploy()
            self.cloud_function.add_permissions_to_cloud_function(
                sa_email=self.service_accounts.sa_email
            )
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) deployed successfully."
            )
        else:
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) already exists in region "
                f"({self.location}). Skipping deployment."
            )

    def _ensure_pre_vm_image(self):
        from aigear.infrastructure.gcp.pre_vm_image import PreVMImage

        self.pre_vm_image = PreVMImage(
            project_id=self.project_id, zone=f"{self.location}-a"
        )
        tasks = {}

        if not self.pre_vm_image.cpu_image_exists():
            logger.info(
                f"Pre-VM custom cpu image ({self.pre_vm_image.cpu_image_name}) not found in project "
                f"({self.project_id}). Will bake in parallel..."
            )
            tasks["cpu"] = self.pre_vm_image.create_cpu_image
        else:
            logger.info(
                f"Pre-VM custom cpu image ({self.pre_vm_image.cpu_image_name}) already exists in "
                f"project ({self.project_id}). Skipping creation."
            )

        if not self.pre_vm_image.gpu_image_exists():
            logger.info(
                f"Pre-VM custom gpu image ({self.pre_vm_image.gpu_image_name}) not found in project "
                f"({self.project_id}). Will bake in parallel..."
            )
            tasks["gpu"] = self.pre_vm_image.create_gpu_image
        else:
            logger.info(
                f"Pre-VM custom gpu image ({self.pre_vm_image.gpu_image_name}) already exists in "
                f"project ({self.project_id}). Skipping creation."
            )

        if not tasks:
            return

        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {executor.submit(fn): label for label, fn in tasks.items()}
            for future in as_completed(futures):
                label = futures[future]
                future.result()  # re-raise any exception
                logger.info(
                    f"Pre-VM custom {label} image ({getattr(self.pre_vm_image, f'{label}_image_name')}) "
                    f"created successfully."
                )

    def _ensure_kubernetes_cluster(self):
        exists = self.kubernetes_cluster.describe()
        if not exists:
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) not found in region "
                f"({self.location}). Creating Kubernetes Cluster..."
            )
            self.kubernetes_cluster.create()
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) created successfully."
            )
        else:
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) already exists in region "
                f"({self.location})."
            )

    def _update_kubernetes(self):
        exists = self.kubernetes_cluster.describe()
        if not exists:
            logger.warning(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) not found in region "
                f"({self.location}). Skipping update — run --create first."
            )
            return
        logger.info(
            f"Updating Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name})..."
        )
        self.kubernetes_cluster.update()
        logger.info(
            f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) updated successfully."
        )

    # ================================================================
    # Public API: update
    # ================================================================
    def update(self):
        self._preflight_check()

        logger.info("===================================================")
        logger.info("             Aigear GCP Infra Updating             ")
        logger.info("===================================================")

        failed_steps = []
        cfg = self.aigear_config.gcp

        # ── Phase 1: Service Account ───────────────────────────────────
        self._step_no_update(f"Service Account ({cfg.iam.account_name})")

        # ── Phase 2: Independent resources (parallel) ──────────────────
        phase2_tasks = {}

        # Resources that support update
        if cfg.cloud_build.on:
            phase2_tasks[f"Cloud Build Trigger ({cfg.cloud_build.trigger_name})"] = (
                self._update_cloud_build
            )
        else:
            self._step_skip(f"Cloud Build Trigger ({cfg.cloud_build.trigger_name})")

        if cfg.kubernetes.on:
            phase2_tasks[f"Kubernetes Cluster ({cfg.kubernetes.cluster_name})"] = (
                self._update_kubernetes
            )
        else:
            self._step_skip(f"Kubernetes Cluster ({cfg.kubernetes.cluster_name})")

        # Resources that do not support update
        if cfg.bucket.on:
            self._step_no_update(f"Model Bucket ({cfg.bucket.bucket_name})")
            self._step_no_update(
                f"Release Model Bucket ({cfg.bucket.bucket_name_for_release})"
            )
        else:
            self._step_skip(f"Model Bucket ({cfg.bucket.bucket_name})")
            self._step_skip(
                f"Release Model Bucket ({cfg.bucket.bucket_name_for_release})"
            )

        if cfg.artifacts.on:
            self._step_no_update(
                f"Artifact Registry ({cfg.artifacts.repository_name})"
            )
        else:
            self._step_skip(f"Artifact Registry ({cfg.artifacts.repository_name})")

        if cfg.pub_sub.on:
            self._step_no_update(f"Pub/Sub Topic ({cfg.pub_sub.topic_name})")
        else:
            self._step_skip(f"Pub/Sub Topic ({cfg.pub_sub.topic_name})")

        if cfg.kms.on:
            self._step_no_update(
                f"Cloud KMS ({cfg.kms.keyring_name}/{cfg.kms.key_name})"
            )
        else:
            self._step_skip(
                f"Cloud KMS ({cfg.kms.keyring_name}/{cfg.kms.key_name})"
            )

        if cfg.pre_vm_image.on:
            self._step_no_update("Pre-VM Image (pre_vm_image)")
        else:
            self._step_skip("Pre-VM Image (pre_vm_image)")

        if phase2_tasks:
            with ThreadPoolExecutor(max_workers=len(phase2_tasks)) as executor:
                futures = {
                    executor.submit(self._step, title, fn): title
                    for title, fn in phase2_tasks.items()
                }
                for future in as_completed(futures):
                    title = futures[future]
                    if not future.result():
                        failed_steps.append(title)

        # ── Phase 3: Cloud Function ────────────────────────────────────
        if cfg.cloud_function.on:
            self._step_no_update(
                f"Cloud Function ({cfg.cloud_function.function_name})"
            )
        else:
            self._step_skip(f"Cloud Function ({cfg.cloud_function.function_name})")

        self._log_summary(failed_steps, "Update")

    # ================================================================
    # Public API: delete
    # ================================================================
    def delete(self):
        self._preflight_check()

        logger.info("===================================================")
        logger.info("             Aigear GCP Infra Deleting             ")
        logger.info("===================================================")

        failed_steps = []
        cfg = self.aigear_config.gcp

        # ── Phase 1: Cloud Function (reverse of creation phase 3) ────
        if cfg.cloud_function.on:
            success = self._step(
                f"Cloud Function ({cfg.cloud_function.function_name})",
                self._delete_cloud_function,
            )
            if not success:
                failed_steps.append(
                    f"Cloud Function ({cfg.cloud_function.function_name})"
                )
        else:
            self._step_skip(f"Cloud Function ({cfg.cloud_function.function_name})")

        # ── Phase 2: Independent resources (parallel) ─────────────────
        phase2_tasks = {}

        if cfg.bucket.on:
            phase2_tasks[f"Model Bucket ({cfg.bucket.bucket_name})"] = (
                self._delete_model_bucket
            )
            phase2_tasks[
                f"Release Model Bucket ({cfg.bucket.bucket_name_for_release})"
            ] = self._delete_release_bucket
        else:
            self._step_skip(f"Model Bucket ({cfg.bucket.bucket_name})")
            self._step_skip(
                f"Release Model Bucket ({cfg.bucket.bucket_name_for_release})"
            )

        if cfg.artifacts.on:
            phase2_tasks[f"Artifact Registry ({cfg.artifacts.repository_name})"] = (
                self._delete_artifacts
            )
        else:
            self._step_skip(f"Artifact Registry ({cfg.artifacts.repository_name})")

        if cfg.pub_sub.on:
            phase2_tasks[f"Pub/Sub Topic ({cfg.pub_sub.topic_name})"] = (
                self._delete_pubsub
            )
        else:
            self._step_skip(f"Pub/Sub Topic ({cfg.pub_sub.topic_name})")

        if cfg.kms.on:
            phase2_tasks[f"Cloud KMS ({cfg.kms.keyring_name}/{cfg.kms.key_name})"] = (
                self._delete_kms
            )
        else:
            self._step_skip(f"Cloud KMS ({cfg.kms.keyring_name}/{cfg.kms.key_name})")

        if cfg.cloud_build.on:
            phase2_tasks[f"Cloud Build Trigger ({cfg.cloud_build.trigger_name})"] = (
                self._delete_cloud_build
            )
        else:
            self._step_skip(f"Cloud Build Trigger ({cfg.cloud_build.trigger_name})")

        if cfg.pre_vm_image.on:
            phase2_tasks["Pre-VM Image (pre_vm_image)"] = self._delete_pre_vm_image
        else:
            self._step_skip("Pre-VM Image (pre_vm_image)")

        if cfg.kubernetes.on:
            phase2_tasks[f"Kubernetes Cluster ({cfg.kubernetes.cluster_name})"] = (
                self._delete_kubernetes_cluster
            )
        else:
            self._step_skip(f"Kubernetes Cluster ({cfg.kubernetes.cluster_name})")

        if phase2_tasks:
            with ThreadPoolExecutor(max_workers=len(phase2_tasks)) as executor:
                futures = {
                    executor.submit(self._step, title, fn): title
                    for title, fn in phase2_tasks.items()
                }
                for future in as_completed(futures):
                    title = futures[future]
                    if not future.result():
                        failed_steps.append(title)

        # ── Phase 3: Service Account (reverse of creation phase 1) ───
        if cfg.iam.on:
            success = self._step(
                f"Service Account ({cfg.iam.account_name})",
                self._delete_service_account,
            )
            if not success:
                failed_steps.append(f"Service Account ({cfg.iam.account_name})")
        else:
            self._step_skip(f"Service Account ({cfg.iam.account_name})")

        self._log_summary(failed_steps, "Delete")

    # ================================================================
    # Actual delete actions
    # ================================================================
    def _delete_cloud_function(self):
        exists = self.cloud_function.describe()
        if exists:
            logger.info(
                f"Deleting Cloud Function ({self.aigear_config.gcp.cloud_function.function_name})..."
            )
            self.cloud_function.delete()
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) deletion initiated (async)."
            )
        else:
            logger.info(
                f"Cloud Function ({self.aigear_config.gcp.cloud_function.function_name}) not found. Skipping."
            )

    def _delete_model_bucket(self):
        exists = self.model_bucket.describe()
        if exists:
            logger.info(
                f"Deleting model bucket ({self.aigear_config.gcp.bucket.bucket_name})..."
            )
            self.model_bucket.delete()
            logger.info(
                f"Model bucket ({self.aigear_config.gcp.bucket.bucket_name}) deleted successfully."
            )
        else:
            logger.info(
                f"Model bucket ({self.aigear_config.gcp.bucket.bucket_name}) not found. Skipping."
            )

    def _delete_release_bucket(self):
        exists = self.release_model_bucket.describe()
        if exists:
            logger.info(
                f"Deleting release model bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release})..."
            )
            self.release_model_bucket.delete()
            logger.info(
                f"Release model bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release}) deleted successfully."
            )
        else:
            logger.info(
                f"Release model bucket ({self.aigear_config.gcp.bucket.bucket_name_for_release}) not found. Skipping."
            )

    def _delete_artifacts(self):
        exists = self.artifacts.describe()
        if exists:
            logger.info(
                f"Deleting Artifact Registry ({self.aigear_config.gcp.artifacts.repository_name})..."
            )
            self.artifacts.delete()
            logger.info(
                f"Artifact Registry ({self.aigear_config.gcp.artifacts.repository_name}) deleted successfully."
            )
        else:
            logger.info(
                f"Artifact Registry ({self.aigear_config.gcp.artifacts.repository_name}) not found. Skipping."
            )

    def _delete_pubsub(self):
        exists = self.pubsub.describe()
        if exists:
            logger.info(
                f"Deleting Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name})..."
            )
            self.pubsub.delete()
            logger.info(
                f"Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) deleted successfully."
            )
        else:
            logger.info(
                f"Pub/Sub topic ({self.aigear_config.gcp.pub_sub.topic_name}) not found. Skipping."
            )

    def _delete_kms(self):
        if self.cloud_kms.describe_key():
            logger.info(
                f"Scheduling KMS key versions for destruction "
                f"({self.aigear_config.gcp.kms.keyring_name}/{self.aigear_config.gcp.kms.key_name})..."
            )
            self.cloud_kms.delete()
        else:
            logger.info(
                f"KMS key ({self.aigear_config.gcp.kms.key_name}) not found. Skipping."
            )

    def _delete_cloud_build(self):
        exists = self.cloud_build.describe()
        if exists:
            logger.info(
                f"Deleting Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name})..."
            )
            self.cloud_build.delete()
            logger.info(
                f"Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) deleted successfully."
            )
        else:
            logger.info(
                f"Cloud Build trigger ({self.aigear_config.gcp.cloud_build.trigger_name}) not found. Skipping."
            )

    def _delete_pre_vm_image(self):
        from aigear.infrastructure.gcp.pre_vm_image import PreVMImage

        self.pre_vm_image = PreVMImage(
            project_id=self.project_id, zone=f"{self.location}-a"
        )
        logger.info("Deleting Pre-VM Images (CPU and GPU)...")
        self.pre_vm_image.delete()

    def _delete_kubernetes_cluster(self):
        exists = self.kubernetes_cluster.describe()
        if exists:
            logger.info(
                f"Deleting Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name})..."
            )
            self.kubernetes_cluster.delete()
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) deletion initiated (async)."
            )
        else:
            logger.info(
                f"Kubernetes Cluster ({self.aigear_config.gcp.kubernetes.cluster_name}) not found. Skipping."
            )

    def _delete_service_account(self):
        exists = self.service_accounts.describe()
        if exists:
            logger.info(
                f"Deleting service account ({self.aigear_config.gcp.iam.account_name})..."
            )
            self.service_accounts.delete()
            logger.info(
                f"Service account ({self.aigear_config.gcp.iam.account_name}) deleted successfully."
            )
        else:
            logger.info(
                f"Service account ({self.aigear_config.gcp.iam.account_name}) not found. Skipping."
            )

    # ================================================================
    # Public API: status
    # ================================================================
    def status(self):
        self._preflight_check()

        logger.info("===================================================")
        logger.info("           Aigear GCP Infra Status                 ")
        logger.info("===================================================")

        cfg = self.aigear_config.gcp

        tasks = [
            (
                f"Service Account ({cfg.iam.account_name})",
                cfg.iam.on,
                self.service_accounts.describe,
            ),
            (
                f"Model Bucket ({cfg.bucket.bucket_name})",
                cfg.bucket.on,
                self.model_bucket.describe,
            ),
            (
                f"Release Bucket ({cfg.bucket.bucket_name_for_release})",
                cfg.bucket.on,
                self.release_model_bucket.describe,
            ),
            (
                f"Artifact Registry ({cfg.artifacts.repository_name})",
                cfg.artifacts.on,
                self.artifacts.describe,
            ),
            (
                f"Pub/Sub Topic ({cfg.pub_sub.topic_name})",
                cfg.pub_sub.on,
                self.pubsub.describe,
            ),
            (
                f"Cloud KMS ({cfg.kms.keyring_name}/{cfg.kms.key_name})",
                cfg.kms.on,
                self._status_kms,
            ),
            (
                f"Cloud Build Trigger ({cfg.cloud_build.trigger_name})",
                cfg.cloud_build.on,
                self.cloud_build.describe,
            ),
            ("Pre-VM Image", cfg.pre_vm_image.on, self._status_pre_vm),
            (
                f"Kubernetes Cluster ({cfg.kubernetes.cluster_name})",
                cfg.kubernetes.on,
                self.kubernetes_cluster.describe,
            ),
            (
                f"Cloud Function ({cfg.cloud_function.function_name})",
                cfg.cloud_function.on,
                self.cloud_function.describe,
            ),
        ]

        results = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            future_to_idx = {
                executor.submit(self._status_check, title, on, fn): i
                for i, (title, on, fn) in enumerate(tasks)
            }
            for future in as_completed(future_to_idx):
                results[future_to_idx[future]] = future.result()

        col_w = max(len(title) for title, _, _ in tasks) + 2
        sep = "-" * (col_w + 10 + 30)
        print(f"\n{'Resource':<{col_w}} {'Config':<10} Live State")
        print(sep)

        exist_count = missing_count = partial_count = skipped_count = error_count = 0

        for title, config_on, live in results:
            if not config_on:
                config_str, live_str = "DISABLED", "⚠️  SKIPPED"
                skipped_count += 1
            elif live.startswith("EXISTS"):
                config_str, live_str = "ENABLED", f"✅ {live}"
                exist_count += 1
            elif live.startswith("NOT_FOUND"):
                config_str, live_str = "ENABLED", f"❌ {live}"
                missing_count += 1
            elif live.startswith("PARTIAL"):
                config_str, live_str = "ENABLED", f"⚠️  {live}"
                partial_count += 1
            else:
                config_str, live_str = "ENABLED", f"❌ {live}"
                error_count += 1

            print(f"{title:<{col_w}} {config_str:<10} {live_str}")

        print(sep)
        parts = [f"{exist_count} exist", f"{missing_count} missing"]
        if partial_count:
            parts.append(f"{partial_count} partial")
        if skipped_count:
            parts.append(f"{skipped_count} skipped")
        if error_count:
            parts.append(f"{error_count} error")
        logger.info(f"Summary: {' | '.join(parts)}")
        logger.info("===================================================")

    # ================================================================
    # Status helpers
    # ================================================================
    def _status_check(self, title, config_on, check_fn):
        if not config_on:
            return (title, False, None)
        try:
            result = check_fn()
            status = (
                result
                if isinstance(result, str)
                else ("EXISTS" if result else "NOT_FOUND")
            )
        except Exception as e:
            status = f"ERROR: {e}"
        return (title, True, status)

    def _status_kms(self) -> str:
        if not self.cloud_kms.describe_keyring():
            return "NOT_FOUND [keyring ❌]"
        if not self.cloud_kms.describe_key():
            return "EXISTS [keyring ✅  key ❌]"
        ver = "ENABLED" if self.cloud_kms.describe_enabled_key_version() else "DISABLED"
        return f"EXISTS [keyring ✅  key ✅  version {ver}]"

    def _status_pre_vm(self) -> str:
        from aigear.infrastructure.gcp.pre_vm_image import PreVMImage

        pre_vm = PreVMImage(project_id=self.project_id, zone=f"{self.location}-a")
        cpu = pre_vm.cpu_image_exists()
        gpu = pre_vm.gpu_image_exists()
        cs, gs = ("✅" if cpu else "❌"), ("✅" if gpu else "❌")
        if cpu and gpu:
            return f"EXISTS [CPU {cs}  GPU {gs}]"
        if cpu or gpu:
            return f"PARTIAL [CPU {cs}  GPU {gs}]"
        return f"NOT_FOUND [CPU {cs}  GPU {gs}]"

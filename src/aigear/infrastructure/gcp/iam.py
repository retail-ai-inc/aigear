import json
import time
from concurrent.futures import ThreadPoolExecutor

from aigear.common import run_sh
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class ServiceAccounts:
    def __init__(
        self,
        project_id: str,
        account_name: str,
        description: str = None,
        display_name: str = None,
    ):
        self.project_id = project_id
        self.account_name = account_name
        self.description = description
        self.display_name = display_name
        self.sa_email = f"{self.account_name}@{self.project_id}.iam.gserviceaccount.com"

    def create(self):
        if self.account_name and self.project_id:
            command = [
                "gcloud",
                "iam",
                "service-accounts",
                "create",
                self.account_name,
                f"--project={self.project_id}",
            ]
            if self.description:
                command.append(f"--description={self.description}")
            if self.display_name:
                command.append(f"--display-name={self.display_name}")
            run_sh(command, check=True)

    def delete(self):
        command = [
            "gcloud",
            "iam",
            "service-accounts",
            "delete",
            self.sa_email,
            f"--project={self.project_id}",
            "--quiet",
        ]
        event = run_sh(command, check=True)
        if event.strip():
            logger.info(event)

    def _wait_for_sa_ready(self, retries: int = 10, interval: int = 6):
        """Wait until the service account is visible to GCP IAM (propagation delay)."""
        for i in range(retries):
            if self.describe():
                return
            logger.info(
                f"Waiting for service account propagation... ({i + 1}/{retries})"
            )
            time.sleep(interval)
        raise RuntimeError(
            f"Service account {self.sa_email} did not become available after {retries * interval}s."
        )

    def add_iam_policy_binding(self):
        self._wait_for_sa_ready()
        roles = [
            "roles/compute.instanceAdmin.v1",
            "roles/artifactregistry.reader",
            "roles/container.developer",
            "roles/logging.logWriter",
        ]

        # Project-level bindings share the same IAM policy ETag, so they must
        # run sequentially to avoid optimistic-concurrency conflicts.
        def _bind_project_roles():
            for role in roles:
                command = [
                    "gcloud",
                    "projects",
                    "add-iam-policy-binding",
                    self.project_id,
                    f"--member=serviceAccount:{self.sa_email}",
                    f"--role={role}",
                    "--condition=None",
                ]
                run_sh(command, check=True)
                logger.info(f"✅ Successfully granted: {role}")

        # SA self-binding operates on a different resource (SA IAM policy, not
        # project IAM policy), so it can safely run in parallel.
        def _bind_sa_self():
            command = [
                "gcloud",
                "iam",
                "service-accounts",
                "add-iam-policy-binding",
                self.sa_email,
                f"--member=serviceAccount:{self.sa_email}",
                "--role=roles/iam.serviceAccountUser",
                f"--project={self.project_id}",
            ]
            run_sh(command, check=True)
            logger.info(
                "✅ Successfully granted: roles/iam.serviceAccountUser (self-binding)"
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            f_project = executor.submit(_bind_project_roles)
            f_sa = executor.submit(_bind_sa_self)
            f_project.result()
            f_sa.result()

    def describe(self):
        command = ["gcloud", "iam", "service-accounts", "describe", self.sa_email]
        try:
            event = run_sh(command, check=True)
        except RuntimeError as exc:
            if "NOT_FOUND" in str(exc):
                return False
            if "PERMISSION_DENIED" not in str(exc):
                raise
            # gcloud describe uses projects/- and can return PERMISSION_DENIED
            # for a missing account. A project-scoped list disambiguates this;
            # real list permission failures still propagate.
            accounts = json.loads(run_sh([
                "gcloud", "iam", "service-accounts", "list",
                f"--project={self.project_id}",
                f"--filter=email={self.sa_email}",
                "--format=json",
            ], check=True))
            return any(account.get("email") == self.sa_email for account in accounts)
        if "name: projects" not in event:
            raise RuntimeError(f"Unexpected service account describe output: {event}")
        return True

    def check_iam(self):
        is_owner = False
        account_cmd = run_sh(["gcloud", "config", "get-value", "account"]).strip()
        command = [
            "gcloud",
            "projects",
            "get-iam-policy",
            self.project_id,
            "--flatten=bindings[].members",
            "--format=table(bindings.role)",
            f"--filter=bindings.members:{account_cmd}",
        ]
        event = run_sh(command)
        if "roles/owner" in event:
            is_owner = True
        else:
            logger.info(event or "No owner role found.")
        return is_owner

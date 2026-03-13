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
                "gcloud", "iam", "service-accounts", "create",
                self.account_name,
                f"--project={self.project_id}",
            ]
            if self.description:
                command.append(f"--description={self.description}")
            if self.display_name:
                command.append(f"--display-name={self.display_name}")
            event = run_sh(command)
            if event == "":
                logger.info("The currently logged in GCP account does not have owner privileges.")
            else:
                logger.info(event)

    def delete(self):
        command = [
            "gcloud", "iam", "service-accounts", "delete",
            self.sa_email,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if event == "":
            logger.info("The currently logged in GCP account does not have owner privileges.")
        else:
            logger.info(event)

    def add_iam_policy_binding(self):
        roles = [
            "roles/compute.instanceAdmin.v1",
            "roles/artifactregistry.reader",
            "roles/container.developer",
        ]

        for role in roles:
            command = [
                "gcloud", "projects", "add-iam-policy-binding", self.project_id,
                f"--member=serviceAccount:{self.sa_email}",
                f"--role={role}",
                "--condition=None"
            ]
            event = run_sh(command)
            if "Updated IAM policy" in event:
                logger.info(f"✅ Successfully granted: {role}")
            else:
                logger.error(f"❌ Failed: {event}")

        # SA level self binding, precise authorization
        command = [
            "gcloud", "iam", "service-accounts", "add-iam-policy-binding",
            self.sa_email,
            f"--member=serviceAccount:{self.sa_email}",
            "--role=roles/iam.serviceAccountUser",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if "Updated IAM policy" in event:
            logger.info("✅ Successfully granted: roles/iam.serviceAccountUser (self-binding)")
        else:
            logger.error(f"❌ Failed self-binding: {event}")

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "iam", "service-accounts", "describe", self.sa_email
        ]
        event = run_sh(command)
        if "name: projects" in event:
            is_exist = True
            logger.info(f"Find resources: {event}")
        elif "NOT_FOUND" in event:
            logger.info(f"NOT_FOUND: Resource not found (resource={self.sa_email})")
        else:
            logger.info(event)
        return is_exist

    def check_iam(self):
        is_owner = False
        account_cmd = run_sh(["gcloud", "config", "get-value", "account"]).strip()
        command = [
            "gcloud", "projects", "get-iam-policy", self.project_id,
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

if __name__ == "__main__":
    service_accounts = ServiceAccounts(
        project_id="",
        account_name="test-pipelines",
    )
    service_accounts.add_iam_policy_binding()

from aigear import aigear_logger
from aigear.common import run_sh


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
                aigear_logger.info("The currently logged in GCP account does not have owner privileges.")
            else:
                aigear_logger.info(event)

    def delete(self):
        command = [
            "gcloud", "iam", "service-accounts", "delete",
            self.sa_email,
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if event == "":
            aigear_logger.info("The currently logged in GCP account does not have owner privileges.")
        else:
            aigear_logger.info(event)

    def add_iam_policy_binding(self, roles: list):
        for role in roles:
            command = [
                "gcloud", "projects", "add-iam-policy-binding", self.project_id,
                f"--member=serviceAccount:{self.sa_email}",
                f"--role={role}",
            ]
            event = run_sh(command)
            if "Updated IAM policy" in event:
                aigear_logger.info(f"✅Successfully granted: {role}")
            else:
                aigear_logger.error(f"❌Failed: {event}")

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "iam", "service-accounts", "describe", self.sa_email
        ]
        event = run_sh(command)
        if "name: projects" in event:
            is_exist = True
            aigear_logger.info(f"Find resources: {event}")
        elif "NOT_FOUND" in event:
            aigear_logger.info(f"NOT_FOUND: Resource not found (resource={self.sa_email})")
        else:
            aigear_logger.info(event)
        return is_exist

    def check_iam(self):
        is_owner = False
        command = [
            "gcloud", "projects", "get-iam-policy",
            self.project_id,
            "--flatten=bindings[].members",
            "--format=table(bindings.role)",
            "--filter=bindings.members:$(gcloud config get-value account)",
        ]
        event = run_sh(command)
        if "roles/owner" in event:
            is_owner = True
        elif event == "":
            aigear_logger.info("The currently logged in GCP account does not have owner privileges.")
        else:
            aigear_logger.info(event)
        return is_owner


if __name__ == "__main__":
    roles = [
        "roles/storage.admin",
        "roles/compute.admin",
        "roles/pubsub.admin",
        "roles/artifactregistry.reader",
        "roles/secretmanager.secretAccessor"
    ]
    service_accounts = ServiceAccounts(
        project_id="ssc-ape-staging",
        account_name="ml-test",
    )
    service_accounts.add_iam_policy_binding(roles)

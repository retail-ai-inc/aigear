import logging
from common.sh import run_sh


def check_iam(project_id: str):
    is_owner = False
    command = [
        "gcloud", "projects", "get-iam-policy",
        project_id,
        "--flatten=bindings[].members",
        "--format=table(bindings.role)",
        "--filter=bindings.members:$(gcloud config get-value account)",
    ]
    event = run_sh(command)
    if "roles/owner" in event:
        is_owner = True
    elif event == "":
        logging.info("The currently logged in GCP account does not have owner privileges.")
    else:
        logging.info(event)
    return is_owner

class ServiceAccounts:
    def __init__(
        self, 
        project_id: str,
        account_name: str,
        description: str=None,
        display_name: str=None,
    ):
        self.project_id=project_id
        self.account_name=account_name,
        self.description=description,
        self.display_name=display_name,

    def create(self):
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
            logging.info("The currently logged in GCP account does not have owner privileges.")
        else:
            logging.info(event)

    def add_iam_policy_binding(self, roles: list):
        sa_email=f"{self.account_name}@{self.project_id}.iam.gserviceaccount.com"

        roles = [
            "roles/storage.admin",
            "roles/logging.logWriter",
            "roles/monitoring.metricWriter"
        ]

        for role in roles:
            command = [
                "gcloud", "projects", "add-iam-policy-binding", self.project_id,
                "--member", f"serviceAccount:{sa_email}",
                "--role", role,
                "--quiet"  # 可选，不提示确认
            ]
            event = run_sh(command)
            if event.returncode == 0:
                logging.info(f"✅Successfully granted: {role}")
            else:
                logging.error(f"❌Failed: {event.stderr}")

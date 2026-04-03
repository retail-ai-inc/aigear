import platform

from aigear.common import run_sh
from aigear.common.logger import Logging


def _escape_pattern(pattern: str) -> str:
    """Escape shell-special characters in regex patterns for Windows cmd.exe."""
    if platform.system() == "Windows":
        return pattern.replace("^", "^^")
    return pattern

logger = Logging(log_name=__name__).console_logging()


# Events that use a GitHub repository as source
_GITHUB_EVENTS = ("push", "tag", "pull_request")

# Mapping from event name to gcloud trigger subcommand
_CLOUD_BUILD_CONFIG = "/cloudbuild/cloudbuild.yaml"

_EVENT_SUBCOMMAND = {
    "push":         "github",
    "tag":          "github",
    "pull_request": "github",
    "manual":       "manual",
    "pubsub":       "pubsub",
    "webhook":      "webhook",
}


class CloudBuild:
    def __init__(
        self,
        project_id: str,
        region: str,
        trigger_name: str,
        description: str = None,
        repo_owner: str = None,
        repo_name: str = None,
        event: str = "push",
        branch_pattern: str = None,
        tag_pattern: str = None,
        substitutions: str = None,
    ):
        self.project_id = project_id
        self.region = region
        self.trigger_name = trigger_name
        self.description = description
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.event = event
        self.branch_pattern = branch_pattern
        self.tag_pattern = tag_pattern
        self.substitutions = substitutions

    def _event_args(self) -> list:
        """Return gcloud args that define the trigger event."""
        if self.event == "push":
            return [f"--branch-pattern={_escape_pattern(self.branch_pattern or '.*')}"]
        if self.event == "tag":
            return [f"--tag-pattern={_escape_pattern(self.tag_pattern or '.*')}"]
        if self.event == "pull_request":
            return [f"--pull-request-pattern={_escape_pattern(self.branch_pattern or '.*')}"]
        # manual / pubsub / webhook have no extra event args here
        return []

    # ------------------------------------------------------------------ #
    #  Trigger                                                             #
    # ------------------------------------------------------------------ #

    def create(self):
        subcommand = _EVENT_SUBCOMMAND.get(self.event, "github")
        command = [
            "gcloud", "builds", "triggers", "create", subcommand,
            f"--name={self.trigger_name}",
            f"--build-config={_CLOUD_BUILD_CONFIG}",
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        if self.event in _GITHUB_EVENTS:
            command += [
                f"--repo-owner={self.repo_owner}",
                f"--repo-name={self.repo_name}",
            ]
        command += self._event_args()
        if self.description:
            command.append(f"--description={self.description}")
        if self.substitutions:
            command.append(f"--substitutions={self.substitutions}")
        event = run_sh(command)
        if "ERROR" in event:
            raise RuntimeError(event)
        logger.info(event)

    def update(self):
        subcommand = _EVENT_SUBCOMMAND.get(self.event, "github")
        command = [
            "gcloud", "builds", "triggers", "update", subcommand,
            self.trigger_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        if self.event in _GITHUB_EVENTS:
            if self.repo_owner:
                command.append(f"--repo-owner={self.repo_owner}")
            if self.repo_name:
                command.append(f"--repo-name={self.repo_name}")
        command += self._event_args()
        if self.description:
            command.append(f"--description={self.description}")
        command.append(f"--build-config={_CLOUD_BUILD_CONFIG}")
        if self.substitutions:
            command.append(f"--substitutions={self.substitutions}")
        event = run_sh(command)
        if "ERROR" in event:
            raise RuntimeError(event)
        logger.info(event)

    def describe(self) -> bool:
        is_exist = False
        command = [
            "gcloud", "builds", "triggers", "describe",
            self.trigger_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if self.trigger_name in event and "ERROR" not in event:
            is_exist = True
        elif "ERROR" in event and "NOT_FOUND" not in event:
            logger.error(f"Unexpected error describing trigger ({self.trigger_name}): {event}")
        return is_exist

    def delete(self):
        command = [
            "gcloud", "builds", "triggers", "delete",
            self.trigger_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)

    def run(self, branch: str = None):
        command = [
            "gcloud", "builds", "triggers", "run",
            self.trigger_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        if branch:
            command.append(f"--branch={branch}")
        elif self.branch_pattern:
            command.append(f"--branch={self.branch_pattern}")
        event = run_sh(command)
        logger.info(event)

    # ------------------------------------------------------------------ #
    #  Build                                                               #
    # ------------------------------------------------------------------ #

    def submit(self, source: str = ".", config: str = None):
        command = [
            "gcloud", "builds", "submit",
            source,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        if config:
            command.append(f"--config={config}")
        else:
            command.append(f"--config={_CLOUD_BUILD_CONFIG}")
        if self.substitutions:
            command.append(f"--substitutions={self.substitutions}")
        event = run_sh(command)
        logger.info(event)

    def list_builds(self, limit: int = 10):
        command = [
            "gcloud", "builds", "list",
            f"--limit={limit}",
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(event)
        return event


import shutil
from pathlib import Path

from aigear.common import run_sh
from aigear.common.constant import VENV_BASE_DIR
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class CloudFunction:
    def __init__(
        self,
        function_name,
        region,
        entry_point,
        topic_name,
        project_id,
        service_account,
    ):
        self.function_name = function_name
        self.region = region
        self.entry_point = entry_point
        self.topic_name = topic_name
        self.project_id = project_id
        self.service_account = service_account

    def _function_path(self):
        source_path = Path(__file__).resolve().parent / "function"
        destination_path = Path.cwd() / "cloud_function"
        destination_path.mkdir(parents=True, exist_ok=True)

        function_path_src = source_path / "index.js"
        function_file_dst = destination_path / "index.js"
        if not function_file_dst.exists():
            content = Path(function_path_src).read_text(encoding="utf-8")
            content = (
                content.replace("{{PROJECTID}}", self.project_id)
                .replace("{{REGION}}", self.region)
                .replace("{{TOPICSNAME}}", self.topic_name)
                .replace("{{VENVBASEDIR}}", VENV_BASE_DIR)
            )
            function_file_dst.write_text(content, encoding="utf-8")

        package_file_src = source_path / "package.json"
        package_file_dst = destination_path / "package.json"
        if not package_file_dst.exists():
            shutil.copy(package_file_src, package_file_dst)

        return destination_path.as_posix()

    def deploy(self):
        source_path = self._function_path()
        command = [
            "gcloud",
            "functions",
            "deploy",
            self.function_name,
            "--gen2",
            "--runtime=nodejs24",
            f"--region={self.region}",
            f"--entry-point={self.entry_point}",
            f"--trigger-topic={self.topic_name}",
            f"--source={source_path}",
            f"--project={self.project_id}",
            f"--service-account={self.service_account}",
            "--quiet",
            "--no-allow-unauthenticated",
        ]
        run_sh(command, timeout=600, check=True)

    def add_permissions_to_cloud_function(self, sa_email):
        command = [
            "gcloud",
            "run",
            "services",
            "add-iam-policy-binding",
            self.function_name,
            f"--region={self.region}",
            f"--member=serviceAccount:{sa_email}",
            "--role=roles/run.invoker",
        ]
        run_sh(command, check=True)
        logger.info(f"✅ run.invoker granted on {self.function_name}")

    def describe(self):
        is_exist = False
        command = [
            "gcloud",
            "run",
            "services",
            "describe",
            self.function_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if f"Service {self.function_name} in region {self.region}" in event:
            is_exist = True
        elif "ERROR" in event and "Cannot find service" not in event:
            logger.error(
                f"Unexpected error describing cloud function ({self.function_name}): {event}"
            )
        return is_exist

    def list(self):
        command = [
            "gcloud",
            "run",
            "services",
            "list",
            f"--region={self.region}",
            f"--filter={self.function_name}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")

    def delete(self):
        command = [
            "gcloud",
            "run",
            "services",
            "delete",
            self.function_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
            "--async",
            "--quiet",
        ]
        event = run_sh(command)
        if "ERROR" in event:
            logger.error(
                f"Failed to delete cloud function ({self.function_name}): {event}"
            )
        else:
            logger.info(
                f"Cloud Function '{self.function_name}' deletion initiated (async)."
            )

import shutil
from pathlib import Path
from aigear.common import run_sh
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
        service_account
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
        content = Path(function_path_src).read_text(encoding="utf-8")
        content = content.replace("PROJECTID", self.project_id
            ).replace("ZONE", f"{self.region}-a"
            ).replace("REGION", self.region
            ).replace("TOPICSNAME", self.topic_name
            ).replace("SERVICEACCOUNT", self.service_account)
        function_file_dst.write_text(content, encoding="utf-8")

        package_file_src = source_path / "package.js"
        package_file_dst = destination_path / "package.json"
        if not package_file_dst.exists():
            shutil.copy(package_file_src, package_file_dst)

        return destination_path

    def deploy(self):
        source_path = self._function_path()
        command = [
            "gcloud", "functions", "deploy",
            self.function_name,
            "--gen2",
            "--runtime=nodejs20",
            f"--region={self.region}",
            f"--entry-point={self.entry_point}",
            f"--trigger-topic={self.topic_name}",
            f"--source={source_path}",
            f"--project={self.project_id}",
            f"--service-account={self.service_account}"
        ]
        event = run_sh(command)
        logger.info(event)
        if "ERROR" in event:
            logger.error("Error occurred while creating cloud function.")

    def describe(self):
        is_exist = False
        command = [
            "gcloud", "run", "services", "describe",
            self.function_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        if f"Service {self.function_name} in region {self.region}" in event:
            is_exist = True
            logger.info(f"Find resources: {event}")
        elif "ERROR" in event and "Cannot find service" in event:
            logger.info(f"Resource not found: {event}")
        else:
            logger.info(event)
        return is_exist

    def list(self):
        command = [
            "gcloud", "run", "services", "list",
            f"--region={self.region}",
            f"--filter={self.function_name}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")

    def delete(self):
        command = [
            "gcloud", "run", "services", "delete",
            self.function_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command, "yes\n")
        logger.info(f"\n{event}")

if __name__ == "__main__":
    cloud_function = CloudFunction(
        function_name="ml-test-run",
        region="asia-northeast1",
        entry_point="cronjobProcessPubSub",
        topic_name="ml-test-pubsub",
        project_id="",
        service_account=""
    )
    cloud_function_exist = cloud_function.describe()
    print("cloud_function: ", cloud_function_exist)
    if not cloud_function_exist:
        cloud_function.deploy()

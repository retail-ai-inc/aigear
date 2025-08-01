from pathlib import Path
from aigear import aigear_logger
from aigear.common import run_sh


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
        self.source_path = Path(__file__).resolve().parent / "function"
        self.project_id = project_id
        self.service_account = service_account
    
    def deploy(self):
        command = [
            "gcloud", "functions", "deploy",
            self.function_name,
            "--gen2",
            "--runtime=nodejs20",
            f"--region={self.region}",
            f"--entry-point={self.entry_point}",
            f"--trigger-topic={self.topic_name}",
            f"--source={self.source_path}",
            f"--project={self.project_id}",
            f"--service-account={self.service_account}"
        ]
        event = run_sh(command)
        aigear_logger.info(event)
        if "ERROR" in event:
            aigear_logger.error("Error occurred while creating cloud function.")
    
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
            aigear_logger.info(f"Find resources: {event}")
        elif "ERROR" in event and "Cannot find service" in event:
            aigear_logger.info(f"Resource not found: {event}")
        else:
            aigear_logger.info(event)
        return is_exist
    
    def list(self):
        command = [
            "gcloud", "run", "services", "list",
            f"--region={self.region}",
            f"--filter={self.function_name}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command)
        aigear_logger.info(f"\n{event}")
    
    def delete(self):
        command = [
            "gcloud", "run", "services", "delete",
            self.function_name,
            f"--region={self.region}",
            f"--project={self.project_id}",
        ]
        event = run_sh(command, "yes\n")
        aigear_logger.info(f"\n{event}")


if __name__=="__main__":
    cloud_function = CloudFunction(
        function_name="ml-test-run",
        region="asia-northeast1",
        entry_point="cronjobProcessPubSub",
        topic_name = "ml-test-pubsub",
        project_id="ssc-ape-staging",
        service_account="ml-test@ssc-ape-staging.iam.gserviceaccount.com"
    )
    cloud_function_exist = cloud_function.describe()
    print("cloud_function: ", cloud_function_exist)
    if not cloud_function_exist:
        cloud_function.deploy()

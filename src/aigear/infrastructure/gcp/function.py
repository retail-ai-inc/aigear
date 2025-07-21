from pathlib import Path
from ...common.logger import logger
from ...common import run_sh


class CloudFunction:
    def __init__(
        self,
        function_name,
        region,
        entry_point,
        topic_name,
    ):
        self.function_name = function_name
        self.region = region
        self.entry_point = entry_point
        self.topic_name = topic_name
        self.source_path = Path(__file__).resolve().parent / "function_config"
    
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
        ]
        event = run_sh(command)
        logger.info(event)
        if "ERROR" in event:
            logger.info("Error occurred while creating cloud function.")
    
    def logs(self, limit=5):
        command = [
            "gcloud", "functions", "logs", "read",
            "--gen2",
            f"--region={self.region}",
            f"--limit={limit}",
            self.function_name,
        ]
        event = run_sh(command)
        logger.info(event)
    
    def describe(self):
        is_exist = False
        command = [
            "gcloud", "functions", "describe",
            self.function_name,
            f"--region={self.region}",
        ]
        event = run_sh(command)
        if "ACTIVE" in event:
            is_exist = True
            logger.info(f"Find resources: {event}")
        elif "ERROR" in event and "not found" in event:
            logger.info(f"NOT_FOUND: Resource not found: {event}")
        else:
            logger.info(event)
        return is_exist
    
    def list(self):
        command = [
            "gcloud", "functions", "list",
            f"--regions={self.region}",
            "--v2",
            f"--filter={self.function_name}",
        ]
        event = run_sh(command)
        logger.info(f"\n{event}")
    
    def delete(self):
        command = [
            "gcloud", "functions", "delete",
            self.function_name,
            "--gen2",
            f"--region={self.region}",
        ]
        event = run_sh(command, "yes\n")
        logger.info(f"\n{event}")

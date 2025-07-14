from .pipeline import workflow
from .task import task
from .executor import TaskRunner

__all__ = [
    "workflow",
    "task",
    "TaskRunner"
]

from aigear.bucket import BucketClient
from aigear.cloud_logging import Logging
from aigear.config import read_config
from aigear.dynamic_type import (
    DataModelType,
    InputFileType,
    generate_schema,
    generate_schema_for_json,
)
from aigear.mongodb import MDBClient
from aigear.secretmanager import SecretManager
from aigear.slack import SlackNotifier
from aigear.task_scheduler import task_run

config = read_config()

bucket = BucketClient(
    config.aigear.gcp.gcp_project_id,
    config.aigear.gcp.bucket.bucket_name,
    config.aigear.gcp.bucket.on
)

bucket_for_release = BucketClient(
    config.aigear.gcp.gcp_project_id,
    config.aigear.gcp.bucket.bucket_name_for_release,
    config.aigear.gcp.bucket.on
)

mongodb = MDBClient(config.aigear.gcp.gcp_project_id)
secret_manager = SecretManager(config.aigear.gcp.gcp_project_id)
slack_notifier = SlackNotifier(config.aigear.slack.webhook_url, config.aigear.slack.on)

if config.aigear.gcp.logging:
    logger = Logging(project_id=config.aigear.gcp.gcp_project_id).cloud_logging()
else:
    logger = Logging(project_id=config.aigear.gcp.gcp_project_id).console_logging()

# Function switch log
logger.info(f"Basket enabled: {config.aigear.gcp.bucket.on}.")
logger.info(f"Slack notifier enabled: {config.aigear.slack.on}.")
logger.info(f"GCP logging enabled: {config.aigear.gcp.logging}.")

__all__.extend([
    task_run,
    config,
    bucket,
    mongodb,
    secret_manager,
    slack_notifier,
    bucket_for_release,
    generate_schema,
    generate_schema_for_json,
    InputFileType,
    DataModelType,
    logger,
])

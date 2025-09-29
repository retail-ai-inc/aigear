from google.cloud import secretmanager_v1
from .stage_logger import create_stage_logger, PipelineStage

# Use preprocessing stage logger for secret management
secret_logger = create_stage_logger(
    stage=PipelineStage.PREPROCESSING,
    module_name=__name__,
    cpu_count=1,
    memory_limit="1GB",
    enable_cloud_logging=False  # Secrets should not be logged to cloud
)


class SecretManager:
    def __init__(self, project_id):
        self.project_id = project_id

    def get_secret_val(
        self,
        secret_id: str,
        secret_version: str = "latest",
        secret_decoding: str="utf-8",
    ) -> str:
        with secret_logger.stage_context() as logger:
            logger.info(f"Retrieving secret: {secret_id} (version: {secret_version})")
            sm_client = (secretmanager_v1.sm_client) = secretmanager_v1.SecretManagerServiceClient()
            secret_name = sm_client.secret_version_path(self.project_id, secret_id, secret_version)
            result = sm_client.access_secret_version(name=secret_name)
            logger.info(f"Secret {secret_id} retrieved successfully")

            return result.payload.data.decode(secret_decoding)

from pathlib import Path
from sklearn.datasets import load_breast_cancer
import pickle
from aigear.common.logger import Logging
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch

logger = Logging(log_name=__name__).console_logging()


def get_data(save_path: Path) -> None:
    data = load_breast_cancer()
    with open(save_path, "wb") as f:
        pickle.dump(data, f)


def fetch_data(pipeline_version: str) -> None:
    logger.info("-----fetch data-----")
    env_config = EnvConfig.get_config_with_schema(EnvSchema)
    asset_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="dataset",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    data_file_name = env_config.pipelines.logistic_regression.fetch_data.parameters.data_file_name
    save_path = asset_management.get_local_path(
        local_file_name=data_file_name
    )
    get_data(save_path)
    asset_management.upload(data_file_name)
    logger.info("-----fetch data completed-----")


if __name__ == "__main__":
    fetch_data(
        pipeline_version="logistic_regression"
    )

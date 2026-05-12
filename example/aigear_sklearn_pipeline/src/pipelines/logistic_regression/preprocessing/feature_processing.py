import pickle
from pathlib import Path
from typing import Any
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from aigear.common.logger import Logging
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch

logger = Logging(log_name=__name__).console_logging()


def save_data(
    dataset: Any,
    save_path: Path,
) -> None:
    with open(save_path, "wb") as f:
        pickle.dump(dataset, f)


def feature_processing(pipeline_version: str) -> None:
    logger.info("-----feature processing-----")
    env_config = EnvConfig.get_config_with_schema(EnvSchema)
    dataset_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="dataset",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    data_file_name = (
        env_config.pipelines.logistic_regression.fetch_data.parameters.data_file_name
    )
    data_local_path = dataset_management.download(
        file_name=data_file_name,
    )

    with open(data_local_path, "rb") as f:
        dataset = pickle.load(f)
    x_dataset = dataset.data
    y_dataset = dataset.target

    x_train, x_test, y_train, y_test = train_test_split(
        x_dataset, y_dataset, test_size=0.2, random_state=42, stratify=y_dataset
    )
    scaler = StandardScaler()
    scaler.fit(x_train)
    x_train_scaled = scaler.transform(x_train)
    x_test_scaled = scaler.transform(x_test)

    dataset = [x_train_scaled, x_test_scaled, y_train, y_test]

    feature_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="feature",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    feature_file_name = env_config.pipelines.logistic_regression.preprocessing.parameters.feature_file_name
    feature_path = feature_management.get_local_path(local_file_name=feature_file_name)
    save_data(
        dataset=dataset,
        save_path=feature_path,
    )
    feature_management.upload(feature_file_name)

    scaler_file_name = (
        env_config.pipelines.logistic_regression.preprocessing.parameters.scaler_model
    )
    scaler_file_path = feature_management.get_local_path(
        local_file_name=scaler_file_name
    )
    save_data(
        dataset=scaler,
        save_path=scaler_file_path,
    )
    feature_management.upload(scaler_file_name)
    logger.info("-----feature processing completed-----")


if __name__ == "__main__":
    feature_processing(pipeline_version="logistic_regression")

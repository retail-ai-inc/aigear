from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
import pickle
from aigear.common.logger import Logging
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch

logger = Logging(log_name=__name__).console_logging()


def save_model(
    model: LogisticRegression,
    save_path: Path,
) -> None:
    with open(save_path, "wb") as f:
        pickle.dump(model, f)


def train_model(pipeline_version: str) -> None:
    logger.info("-----train model-----")
    env_config = EnvConfig.get_config_with_schema(EnvSchema)
    feature_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="feature",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    feature_file_name = env_config.pipelines.logistic_regression.preprocessing.parameters.feature_file_name
    features_path = feature_management.download(feature_file_name)
    with open(features_path, "rb") as f:
        features = pickle.load(f)
    x_train, x_test, y_train, y_test = features

    model = LogisticRegression(
        max_iter=1000,
        random_state=42
    )
    model.fit(x_train, y_train)
    y_pred = model.predict(x_test)
    print("Accuracy:", accuracy_score(y_test, y_pred))
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred))

    training_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="training",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    model_name = env_config.pipelines.logistic_regression.training.parameters.logistic_model
    model_path = training_management.get_local_path(model_name)
    save_model(model, model_path)
    training_management.upload(model_name)
    logger.info("-----train model completed-----")


if __name__ == "__main__":
    train_model(pipeline_version="logistic_regression")

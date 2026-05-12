import pickle
from pathlib import Path
from typing import Any
import numpy as np
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch


class ModelService:
    def __init__(self):
        self.scaler_model, self.logistic_model = self.load_all_model()

    def predict(self, data: dict) -> list:
        features = np.array([data["features"]])
        features_scaled = self.scaler_model.transform(features)
        predict_class = self.logistic_model.predict(features_scaled)
        print("Model prediction results:", predict_class[0])
        return predict_class.tolist()

    @staticmethod
    def _load_model(model_path: Path) -> Any:
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        return model

    def load_all_model(
        self,
    ) -> tuple:
        env_config = EnvConfig.get_config_with_schema(EnvSchema)
        feature_management = AssetManagement(
            pipeline_version="logistic_regression",
            data_type="feature",
            project_id=env_config.aigear.gcp.gcp_project_id,
            bucket_name=env_config.aigear.gcp.bucket.bucket_name,
            bucket_on=gcs_switch,
        )
        scaler_model_name = env_config.pipelines.logistic_regression.preprocessing.parameters.scaler_model
        scaler_model_path = feature_management.download(scaler_model_name)
        scaler_model = self._load_model(scaler_model_path)

        training_management = AssetManagement(
            pipeline_version="logistic_regression",
            data_type="training",
            project_id=env_config.aigear.gcp.gcp_project_id,
            bucket_name=env_config.aigear.gcp.bucket.bucket_name,
            bucket_on=gcs_switch,
        )
        model_name = (
            env_config.pipelines.logistic_regression.training.parameters.logistic_model
        )
        model_path = training_management.download(model_name)
        logistic_model = self._load_model(model_path)
        return scaler_model, logistic_model

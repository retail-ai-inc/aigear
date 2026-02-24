import json
from aigear.common.config import PipelinesConfig


class GrpcConfig:
    def __init__(self):
        self.pipelines_config = PipelinesConfig.get_config()

    def extract_grpc_config(self, pipeline_version, grpc_env_path):
        grpc_config = self.pipelines_config.get(pipeline_version, {}).get("release", {}).get("grpc", {})
        grpc_config["environment"] = self.pipelines_config.get("environment")
        self._save_json({"grpc": grpc_config}, grpc_env_path)

    @staticmethod
    def _save_json(data: dict, grpc_env_path: str):
        with open(grpc_env_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

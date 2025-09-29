import json
import os
from typing import Optional
from aigear.common.schema.config_schema import Config
from aigear.common.stage_logger import create_stage_logger, PipelineStage


# Configuration module uses preprocessing stage logger (configuration usually used in initialization stage)
config_logger = create_stage_logger(
    stage=PipelineStage.PREPROCESSING,
    module_name=__name__,
    cpu_count=1,
    memory_limit="1GB",
    enable_cloud_logging=False  # Configuration errors usually only need local logging
)

def read_config(env_path) -> Optional[Config]:
    with config_logger.stage_context() as logger:
        if not os.path.exists(env_path):
            logger.error(f"Configuration file {env_path} not found.")
            return None

        logger.info(f"Loading configuration from: {env_path}")
        with open(env_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        logger.info("Configuration loaded successfully")
        return Config(**cfg)

class AigearConfig:
    _config = None

    @classmethod
    def load(cls):
        if cls._config:
            return cls._config
        
        cls._config = read_config(env_path=os.path.join(os.getcwd(), "env.json"))
        return cls._config

    @classmethod
    def get_config(cls) -> Config:
        if not cls._config:
            cls.load()
        return cls._config

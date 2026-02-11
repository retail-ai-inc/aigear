import json
import os
from typing import Optional
from aigear.common.schema.config_schema import Config
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()

def read_config(env_path) -> Optional[Config]:
    if not os.path.exists(env_path):
        logger.error(f"Configuration file {env_path} not found.")
        return None

    with open(env_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    return cfg

class AigearConfig:
    _config = None

    @classmethod
    def load(cls):
        if cls._config:
            return cls._config
        
        cfg = read_config(env_path=os.path.join(os.getcwd(), "env.json"))
        cls._config = Config(**cfg["aigear"])
        return cls._config

    @classmethod
    def get_config(cls) -> Config:
        if not cls._config:
            cls.load()
        return cls._config

class PipelinesConfig:
    _config = None

    @classmethod
    def load(cls):
        if cls._config:
            return cls._config

        cfg = read_config(env_path=os.path.join(os.getcwd(), "env.json"))
        cls._config = cfg.get("pipelines", {})
        return cls._config

    @classmethod
    def get_config(cls) -> Config:
        if not cls._config:
            cls.load()
        return cls._config

def get_project_name() -> str:
    cfg = read_config(env_path=os.path.join(os.getcwd(), "env.json"))
    project_name = cfg.get("project_name")
    return project_name

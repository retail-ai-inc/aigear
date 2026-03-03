from __future__ import annotations
import json
from pathlib import Path
from pydantic import BaseModel
from typing import Optional, TypeVar, Type
from aigear.common.schema.config_schema import Config
from aigear.common.logger import Logging
from aigear.common.dynamic_type import generate_schema, DataModelType, InputFileType

T = TypeVar("T", bound=BaseModel)
logger = Logging(log_name=__name__).console_logging()

ENV_PATH = Path.cwd() / "env.json"


def read_config(env_path: str | Path = None) -> Optional[Config]:
    if env_path is None:
        logger.error("The `env_path` parameter of `read_config` is empty.")
        return None

    if isinstance(env_path, str):
        env_path = Path(env_path)
    if not env_path.exists():
        logger.error(f"Configuration file {env_path} not found.")
        return None

    with open(env_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    return cfg


class AigearConfig:
    _config = None

    @classmethod
    def _load(cls):
        if cls._config:
            return cls._config
        cls._config = read_config(ENV_PATH)
        return cls._config

    @classmethod
    def get_config(cls) -> Config:
        if not cls._config:
            cls._load()
        aigear_config = cls._config.get("aigear")
        return Config.model_validate(aigear_config)


class PipelinesConfig:
    _config = None

    @classmethod
    def _load(cls):
        if cls._config:
            return cls._config

        cls._config = read_config(ENV_PATH)
        return cls._config

    @classmethod
    def get_config(cls):
        if not cls._config:
            cls._load()
        return cls._config.get("pipelines", {})

    @classmethod
    def get_version_config(cls, pipeline_version: Optional[str] = None):
        if not cls._config:
            cls._load()
        version_config = {}
        if pipeline_version:
            version_config = cls.get_config().get(pipeline_version, {})
        else:
            logger.info("The parameter 'pipeline_version' is empty.")
        return version_config


class EnvConfig:
    _config = None

    @classmethod
    def _load(cls):
        if cls._config:
            return cls._config
        cls._config = read_config(ENV_PATH)
        return cls._config

    @classmethod
    def get_config_with_json(cls):
        if not cls._config:
            cls._load()
        return cls._config

    @classmethod
    def generative_env_schema(cls, forced_generate):
        output_path = Path.cwd() / "config_schema/env_schema.py"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        init_file = output_path.parent / "__init__.py"
        init_file.touch(exist_ok=True)
        if output_path.exists() and not forced_generate:
            logger.info(f"The 'env_schema' already exists: {output_path}.")
        else:
            generate_schema(
                input_path=ENV_PATH,
                input_file_type=InputFileType.Json,
                output=output_path,
                output_model_type=DataModelType.PydanticBaseModel,
                class_name="EnvSchema",
                forced_generate=forced_generate
            )

    @classmethod
    def get_config_with_schema(cls, model: Type[T]) -> T:
        if not cls._config:
            cls._load()
        return model.model_validate(cls._config)


def get_project_name() -> str | None:
    cfg = read_config(ENV_PATH)
    project_name = cfg.get("project_name")
    return project_name

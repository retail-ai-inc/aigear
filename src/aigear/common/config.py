from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

from aigear.common.dynamic_type import DataModelType, InputFileType, generate_schema
from aigear.common.logger import Logging
from aigear.common.schema.config_schema import Config

T = TypeVar("T", bound=BaseModel)
logger = Logging(log_name=__name__).console_logging()

_env_override = os.environ.get("AIGEAR_ENV_PATH")
ENV_PATH = Path(_env_override) if _env_override else Path.cwd() / "env.json"


# ─── Raw config loader ───────────────────────────────────────────────────────


def _load_raw(env_path: Path = ENV_PATH) -> dict:
    """
    Load and return the raw env.json as a dict.
    Raises FileNotFoundError if the file does not exist.
    """
    if not env_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {env_path}")
    with open(env_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Unified config ──────────────────────────────────────────────────────────


class AppConfig:
    """
    Single entry point for all configuration access.

    Loads env.json once and exposes typed accessors for each section.
    All methods are classmethods — no instantiation needed.

    Sections:
        AppConfig.project_name()          → str
        AppConfig.environment()           → str
        AppConfig.aigear()                → Config  (validated Pydantic model)
        AppConfig.pipelines()             → dict    (full pipelines block)
        AppConfig.pipeline(version)       → dict    (single pipeline version)
        AppConfig.raw()                   → dict    (entire env.json, unvalidated)
        AppConfig.raw_as(model)           → T       (entire env.json as Pydantic model)
        AppConfig.generate_env_schema(…)  → None    (generates Pydantic schema file)
    """

    _raw: dict | None = None
    _aigear: "Config | None" = None

    @classmethod
    def _ensure_loaded(cls) -> dict:
        if cls._raw is None:
            cls._raw = _load_raw()
        return cls._raw

    # ── Top-level fields ─────────────────────────────────────────────────────

    @classmethod
    def project_name(cls) -> str | None:
        return cls._ensure_loaded().get("project_name")

    @classmethod
    def environment(cls) -> str | None:
        return cls._ensure_loaded().get("environment")

    # ── aigear section ───────────────────────────────────────────────────────

    @classmethod
    def aigear(cls) -> Config:
        """Return the validated aigear config as a typed Pydantic model."""
        if cls._aigear is None:
            cls._aigear = Config.model_validate(cls._ensure_loaded().get("aigear", {}))
        return cls._aigear

    # ── pipelines section ────────────────────────────────────────────────────

    @classmethod
    def pipelines(cls) -> dict:
        """Return the full pipelines block."""
        return cls._ensure_loaded().get("pipelines", {})

    @classmethod
    def pipeline(cls, version: str) -> dict:
        """
        Return the config for a single pipeline version.

        Example:
            AppConfig.pipeline("logistic_regression")
            → {"scheduler": {...}, "fetch_data": {...}, ...}
        """
        cfg = cls.pipelines().get(version)
        if cfg is None:
            logger.warning(f"Pipeline version '{version}' not found in config.")
            return {}
        return cfg

    # ── Raw access ───────────────────────────────────────────────────────────

    @classmethod
    def raw(cls) -> dict:
        """Return the entire env.json as an unvalidated dict."""
        return cls._ensure_loaded()

    @classmethod
    def raw_as(cls, model: Type[T]) -> T:
        """Validate and return the entire env.json as a Pydantic model."""
        return model.model_validate(cls._ensure_loaded())

    # ── Schema generation ────────────────────────────────────────────────────

    @classmethod
    def generate_env_schema(cls, forced_generate: bool = False) -> None:
        """Generate a Pydantic schema file from env.json."""
        output_path = Path.cwd() / "config_schema/env_schema.py"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        (output_path.parent / "__init__.py").touch(exist_ok=True)

        if output_path.exists() and not forced_generate:
            logger.info(f"The 'env_schema' already exists: {output_path}.")
            return

        generate_schema(
            input_path=ENV_PATH,
            input_file_type=InputFileType.Json,
            output=output_path,
            output_model_type=DataModelType.PydanticBaseModel,
            class_name="EnvSchema",
            forced_generate=forced_generate,
        )


# ─── Backwards-compatible aliases ────────────────────────────────────────────
# These allow existing code that imports AigearConfig / PipelinesConfig / EnvConfig
# to keep working without modification.


class AigearConfig:
    @classmethod
    def get_config(cls) -> Config:
        return AppConfig.aigear()


class PipelinesConfig:
    @classmethod
    def get_config(cls) -> dict:
        return AppConfig.pipelines()

    @classmethod
    def get_version_config(cls, pipeline_version: str | None = None) -> dict:
        if not pipeline_version:
            logger.info("The parameter 'pipeline_version' is empty.")
            return {}
        return AppConfig.pipeline(pipeline_version)


class EnvConfig:
    @classmethod
    def get_config_with_json(cls) -> dict:
        return AppConfig.raw()

    @classmethod
    def get_config_with_schema(cls, model: Type[T]) -> T:
        return AppConfig.raw_as(model)

    @classmethod
    def generative_env_schema(cls, forced_generate: bool = False) -> None:
        logger.info("Generating env schema...")
        AppConfig.generate_env_schema(forced_generate)
        logger.info("Env schema generation complete.")


# ─── Module-level helpers (backwards compatible) ─────────────────────────────


def get_project_name() -> str | None:
    return AppConfig.project_name()


def get_environment() -> str | None:
    return AppConfig.environment()

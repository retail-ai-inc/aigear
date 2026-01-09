"""
Enhanced Configuration Parser

Supports new configuration structure, including version management, validation, and auto-inference features.
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, List, Any
from copy import deepcopy

from aigear.common.logger import Logging
from aigear.common.config_version import validate_config_version, is_version_supported
from aigear.common.config_validator import (
    validate_config_structure,
    get_companies_from_config,
    get_versions_from_config,
    get_all_versions_from_config,
)


logger = Logging(log_name=__name__).console_logging()


class ConfigParser:
    """
    Enhanced Configuration Parser

    Features:
    - Support configuration version validation
    - Automatically search upward for config files (up to 3 parent directories)
    - Provide convenient configuration access methods
    - Automatically infer companies and versions
    """

    def __init__(self, config_path: str = None, auto_find: bool = True):
        """
        Initialize configuration parser

        Args:
            config_path: Configuration file path (defaults to env.json)
            auto_find: Whether to automatically search upward for config file
        """
        self._config = None
        self._config_path = None

        if config_path:
            self._config_path = config_path
        elif auto_find:
            self._config_path = self._find_config_file()
        else:
            self._config_path = os.path.join(os.getcwd(), "env.json")

    def _find_config_file(self, filename: str = "env.json", max_levels: int = 3) -> Optional[str]:
        """
        Search upward for configuration file

        Args:
            filename: Configuration file name
            max_levels: Maximum levels to search upward

        Returns:
            str: Configuration file path, or None if not found
        """
        current_dir = Path.cwd()

        for _ in range(max_levels + 1):
            config_file = current_dir / filename
            if config_file.exists():
                logger.info(f"Found configuration file: {config_file}")
                return str(config_file)

            # Move up one level
            parent = current_dir.parent
            if parent == current_dir:  # Reached root directory
                break
            current_dir = parent

        # Not found, return default path in current directory
        default_path = Path.cwd() / filename
        logger.warning(f"Configuration file not found, using default path: {default_path}")
        return str(default_path)

    def load(self, validate: bool = True) -> bool:
        """
        Load configuration file

        Args:
            validate: Whether to validate configuration

        Returns:
            bool: Whether successfully loaded
        """
        if not os.path.exists(self._config_path):
            logger.error(f"Configuration file does not exist: {self._config_path}")
            return False

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)

            logger.info(f"Successfully loaded configuration file: {self._config_path}")

            # Validate configuration
            if validate:
                return self._validate()

            return True

        except json.JSONDecodeError as e:
            logger.error(f"Configuration file JSON format error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Failed to load configuration file: {str(e)}")
            return False

    def _validate(self) -> bool:
        """
        Validate configuration file

        Returns:
            bool: Whether validation passed
        """
        # Validate version
        is_valid, error_msg = validate_config_version(self._config)
        if not is_valid:
            logger.error(f"Configuration version validation failed: {error_msg}")
            return False

        # Validate structure
        is_valid, errors = validate_config_structure(self._config)
        if not is_valid:
            logger.error("Configuration structure validation failed:")
            for error in errors:
                logger.error(f"  - {error}")
            return False

        logger.info("Configuration validation passed")
        return True

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value (supports dot notation path)

        Args:
            key: Configuration key (supports "grpc.servers.demo.port" format)
            default: Default value

        Returns:
            Any: Configuration value
        """
        if not self._config:
            self.load()

        if not self._config:
            return default

        keys = key.split(".")
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def get_config(self) -> Dict[str, Any]:
        """
        Get complete configuration

        Returns:
            dict: Configuration dictionary
        """
        if not self._config:
            self.load()

        return deepcopy(self._config) if self._config else {}

    def get_config_path(self) -> str:
        """Get configuration file path"""
        return self._config_path

    def get_config_version(self) -> str:
        """Get configuration version"""
        return self.get("config_version", "Unknown")

    # ==================== Pipeline Configuration Related Methods ====================

    def get_pipelines(self) -> List[str]:
        """
        Get all pipeline list

        Returns:
            List[str]: List of pipeline names
        """
        if not self._config:
            self.load()

        pipelines = self.get("pipelines", {})
        return list(pipelines.keys())

    def get_pipeline_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        Get configuration for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            dict: Pipeline configuration
        """
        return self.get(f"pipelines.{pipeline_name}")

    # ==================== gRPC Configuration Related Methods ====================

    def get_grpc_server_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        Get gRPC server configuration for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            dict: gRPC server configuration
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.server")

    def get_grpc_deployment_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        Get gRPC deployment configuration for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            dict: gRPC deployment configuration
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.deployment")

    def get_grpc_gke_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        Get GKE configuration for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            dict: GKE configuration
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.deployment.gke")

    def is_grpc_enabled(self, pipeline_name: str) -> bool:
        """
        Check if gRPC is enabled for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            bool: Whether enabled
        """
        grpc_config = self.get(f"pipelines.{pipeline_name}.grpc")
        return grpc_config is not None

    def is_gke_enabled(self, pipeline_name: str) -> bool:
        """
        Check if GKE deployment is enabled for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            bool: Whether enabled
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.deployment.gke.enabled", False)

    def get_grpc_sentry_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        Get Sentry configuration for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            dict: Sentry configuration
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.sentry")

    def is_sentry_enabled(self, pipeline_name: str) -> bool:
        """
        Check if Sentry is enabled for specified pipeline

        Args:
            pipeline_name: Pipeline name

        Returns:
            bool: Whether enabled
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.sentry.on", False)

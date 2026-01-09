"""
Configuration Migration Tool

Provides functionality to migrate configuration files from old versions to new versions.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
from copy import deepcopy

from .config_version import CURRENT_VERSION, compare_versions


class ConfigMigrator:
    """Configuration Migration Tool"""

    def __init__(self):
        self.migrations = {
            "0.0.0": self.migrate_from_legacy,  # Migrate from legacy version to v1.0.0
            # Future version migration functions can be added here
            # "1.0.0": self.migrate_1_0_to_1_1,
        }

    def migrate(
        self, config: dict, target_version: str = None, backup: bool = True
    ) -> dict:
        """
        Migrate configuration to target version

        Args:
            config: Original configuration
            target_version: Target version (defaults to latest version)
            backup: Whether to backup original configuration

        Returns:
            dict: Migrated configuration
        """
        current_version = config.get("config_version", "0.0.0")
        target_version = target_version or CURRENT_VERSION

        # If already at target version, return directly
        if current_version == target_version:
            return config

        # Execute migration chain
        migrated_config = deepcopy(config)

        migration_path = self._get_migration_path(current_version, target_version)

        for version in migration_path:
            if version in self.migrations:
                migrated_config = self.migrations[version](migrated_config)

        # Update version number and timestamp
        migrated_config["config_version"] = target_version
        migrated_config["last_updated"] = datetime.now().isoformat()

        return migrated_config

    def _get_migration_path(self, from_version: str, to_version: str) -> List[str]:
        """
        Get migration path

        Args:
            from_version: Starting version
            to_version: Target version

        Returns:
            List[str]: List of migration versions to execute
        """
        # Simplified implementation: if migrating from legacy version, only need to execute 0.0.0 migration
        if from_version == "0.0.0" or "config_version" not in from_version:
            return ["0.0.0"]

        # More complex migration paths can be implemented in the future
        return []

    def migrate_from_legacy(self, config: dict) -> dict:
        """
        Migrate from legacy configuration to v1.0.0

        Args:
            config: Legacy configuration

        Returns:
            dict: v1.0.0 configuration
        """
        new_config = {
            "config_version": "1.0.0",
            "config_schema": "aigear-grpc",
            "last_updated": datetime.now().isoformat(),
        }

        # Preserve basic fields
        if "project_name" in config:
            new_config["project_name"] = config["project_name"]

        if "environment" in config:
            new_config["environment"] = config["environment"]

        # Preserve aigear configuration
        if "aigear" in config:
            new_config["aigear"] = config["aigear"]

        # Migrate pipelines configuration
        if "pipelines" in config:
            new_config["pipelines"] = self._migrate_pipelines(config["pipelines"])

        # Migrate grpc configuration
        new_config["grpc"] = self._migrate_grpc_config(config)

        return new_config

    def _migrate_pipelines(self, pipelines: dict) -> dict:
        """
        Migrate pipelines configuration

        Args:
            pipelines: Old pipelines configuration

        Returns:
            dict: New pipelines configuration
        """
        new_pipelines = {}

        for pipeline_name, pipeline_config in pipelines.items():
            new_pipeline = deepcopy(pipeline_config)

            # Handle release configuration
            if "release" in new_pipeline:
                release_config = new_pipeline["release"]

                # Remove old grpc_service configuration
                if "grpc_service" in release_config:
                    del release_config["grpc_service"]

                # Simplify release configuration
                new_pipeline["release"] = {
                    "on": release_config.get("on", True),
                    "to_bucket": True,
                    "bucket_path": "models/releases/",
                }

            new_pipelines[pipeline_name] = new_pipeline

        return new_pipelines

    def _migrate_grpc_config(self, config: dict) -> dict:
        """
        Migrate grpc configuration

        Args:
            config: Complete old configuration

        Returns:
            dict: New grpc configuration
        """
        old_grpc = config.get("grpc", {})
        new_grpc = {
            "servers": old_grpc.get("servers", {}),
            "sentry": old_grpc.get("sentry", {"on": False}),
        }

        # Extract deployment configuration from old pipelines.*.release.grpc_service
        deployment_config = self._extract_deployment_config(config)

        if deployment_config:
            new_grpc["deployment"] = deployment_config

        return new_grpc

    def _extract_deployment_config(self, config: dict) -> Dict[str, Any]:
        """
        Extract deployment configuration from old configuration

        Args:
            config: Old configuration

        Returns:
            dict: Deployment configuration
        """
        # Extract from pipelines.*.release.grpc_service
        for pipeline_config in config.get("pipelines", {}).values():
            grpc_service = pipeline_config.get("release", {}).get("grpc_service", {})

            if grpc_service:
                deployment = {
                    "enabled": grpc_service.get("enabled", True),
                    "preset": grpc_service.get("preset", ""),
                }

                # Extract model_source
                if "model_source" in grpc_service:
                    deployment["model_source"] = grpc_service["model_source"]

                # Extract GKE configuration
                if "gke" in grpc_service:
                    deployment["gke"] = grpc_service["gke"]

                # Add docker configuration (default values)
                deployment["docker"] = {
                    "enabled": True,
                    "base_image": "python:3.9-slim",
                    "working_dir": "/app",
                    "expose_ports": [50051],
                }

                return deployment

        return None


def migrate_config_file(
    input_file: str,
    output_file: str = None,
    target_version: str = None,
    backup: bool = True,
) -> bool:
    """
    Migrate configuration file

    Args:
        input_file: Input file path
        output_file: Output file path (defaults to input file)
        target_version: Target version (defaults to latest version)
        backup: Whether to backup original file

    Returns:
        bool: Whether successful
    """
    try:
        # Read original configuration
        with open(input_file, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Backup original file
        if backup and output_file is None:
            backup_file = f"{input_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"✅ Backed up original configuration to: {backup_file}")

        # Execute migration
        migrator = ConfigMigrator()
        migrated_config = migrator.migrate(config, target_version, backup)

        # Write new configuration
        output_path = output_file or input_file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(migrated_config, f, indent=2, ensure_ascii=False)

        print(f"✅ Configuration migrated to version {migrated_config['config_version']}")
        print(f"✅ New configuration saved to: {output_path}")

        return True

    except Exception as e:
        print(f"❌ Migration failed: {str(e)}")
        return False

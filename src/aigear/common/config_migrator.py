"""
配置迁移工具

提供配置文件从旧版本迁移到新版本的功能。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
from copy import deepcopy

from .config_version import CURRENT_VERSION, compare_versions


class ConfigMigrator:
    """配置迁移工具"""

    def __init__(self):
        self.migrations = {
            "0.0.0": self.migrate_from_legacy,  # 从旧版本迁移到 v1.0.0
            # 未来版本的迁移函数在这里添加
            # "1.0.0": self.migrate_1_0_to_1_1,
        }

    def migrate(
        self, config: dict, target_version: str = None, backup: bool = True
    ) -> dict:
        """
        迁移配置到目标版本

        Args:
            config: 原始配置
            target_version: 目标版本（默认为最新版本）
            backup: 是否备份原配置

        Returns:
            dict: 迁移后的配置
        """
        current_version = config.get("config_version", "0.0.0")
        target_version = target_version or CURRENT_VERSION

        # 如果已经是目标版本，直接返回
        if current_version == target_version:
            return config

        # 执行迁移链
        migrated_config = deepcopy(config)

        migration_path = self._get_migration_path(current_version, target_version)

        for version in migration_path:
            if version in self.migrations:
                migrated_config = self.migrations[version](migrated_config)

        # 更新版本号和时间戳
        migrated_config["config_version"] = target_version
        migrated_config["last_updated"] = datetime.now().isoformat()

        return migrated_config

    def _get_migration_path(self, from_version: str, to_version: str) -> List[str]:
        """
        获取迁移路径

        Args:
            from_version: 起始版本
            to_version: 目标版本

        Returns:
            List[str]: 需要执行的迁移版本列表
        """
        # 简化实现：如果是从旧版本迁移，只需要执行 0.0.0 迁移
        if from_version == "0.0.0" or "config_version" not in from_version:
            return ["0.0.0"]

        # 未来可以实现更复杂的迁移路径
        return []

    def migrate_from_legacy(self, config: dict) -> dict:
        """
        从旧版本配置迁移到 v1.0.0

        Args:
            config: 旧版本配置

        Returns:
            dict: v1.0.0 配置
        """
        new_config = {
            "config_version": "1.0.0",
            "config_schema": "aigear-grpc",
            "last_updated": datetime.now().isoformat(),
        }

        # 保留基础字段
        if "project_name" in config:
            new_config["project_name"] = config["project_name"]

        if "environment" in config:
            new_config["environment"] = config["environment"]

        # 保留 aigear 配置
        if "aigear" in config:
            new_config["aigear"] = config["aigear"]

        # 迁移 pipelines 配置
        if "pipelines" in config:
            new_config["pipelines"] = self._migrate_pipelines(config["pipelines"])

        # 迁移 grpc 配置
        new_config["grpc"] = self._migrate_grpc_config(config)

        return new_config

    def _migrate_pipelines(self, pipelines: dict) -> dict:
        """
        迁移 pipelines 配置

        Args:
            pipelines: 旧的 pipelines 配置

        Returns:
            dict: 新的 pipelines 配置
        """
        new_pipelines = {}

        for pipeline_name, pipeline_config in pipelines.items():
            new_pipeline = deepcopy(pipeline_config)

            # 处理 release 配置
            if "release" in new_pipeline:
                release_config = new_pipeline["release"]

                # 删除旧的 grpc_service 配置
                if "grpc_service" in release_config:
                    del release_config["grpc_service"]

                # 简化 release 配置
                new_pipeline["release"] = {
                    "on": release_config.get("on", True),
                    "to_bucket": True,
                    "bucket_path": "models/releases/",
                }

            new_pipelines[pipeline_name] = new_pipeline

        return new_pipelines

    def _migrate_grpc_config(self, config: dict) -> dict:
        """
        迁移 grpc 配置

        Args:
            config: 完整的旧配置

        Returns:
            dict: 新的 grpc 配置
        """
        old_grpc = config.get("grpc", {})
        new_grpc = {
            "servers": old_grpc.get("servers", {}),
            "sentry": old_grpc.get("sentry", {"on": False}),
        }

        # 从旧的 pipelines.*.release.grpc_service 提取部署配置
        deployment_config = self._extract_deployment_config(config)

        if deployment_config:
            new_grpc["deployment"] = deployment_config

        return new_grpc

    def _extract_deployment_config(self, config: dict) -> Dict[str, Any]:
        """
        从旧配置中提取部署配置

        Args:
            config: 旧配置

        Returns:
            dict: 部署配置
        """
        # 从 pipelines.*.release.grpc_service 中提取
        for pipeline_config in config.get("pipelines", {}).values():
            grpc_service = pipeline_config.get("release", {}).get("grpc_service", {})

            if grpc_service:
                deployment = {
                    "enabled": grpc_service.get("enabled", True),
                    "preset": grpc_service.get("preset", ""),
                }

                # 提取 model_source
                if "model_source" in grpc_service:
                    deployment["model_source"] = grpc_service["model_source"]

                # 提取 GKE 配置
                if "gke" in grpc_service:
                    deployment["gke"] = grpc_service["gke"]

                # 添加 docker 配置（默认值）
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
    迁移配置文件

    Args:
        input_file: 输入文件路径
        output_file: 输出文件路径（默认为输入文件）
        target_version: 目标版本（默认为最新版本）
        backup: 是否备份原文件

    Returns:
        bool: 是否成功
    """
    try:
        # 读取原配置
        with open(input_file, "r", encoding="utf-8") as f:
            config = json.load(f)

        # 备份原文件
        if backup and output_file is None:
            backup_file = f"{input_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"✅ 已备份原配置到: {backup_file}")

        # 执行迁移
        migrator = ConfigMigrator()
        migrated_config = migrator.migrate(config, target_version, backup)

        # 写入新配置
        output_path = output_file or input_file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(migrated_config, f, indent=2, ensure_ascii=False)

        print(f"✅ 配置已迁移到版本 {migrated_config['config_version']}")
        print(f"✅ 新配置已保存到: {output_path}")

        return True

    except Exception as e:
        print(f"❌ 迁移失败: {str(e)}")
        return False

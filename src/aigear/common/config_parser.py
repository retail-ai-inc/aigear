"""
增强的配置解析器

支持新的配置结构，包括版本管理、验证和自动推断功能。
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
    增强的配置解析器

    功能：
    - 支持配置版本验证
    - 自动向上查找配置文件（最多3级父目录）
    - 提供便捷的配置访问方法
    - 自动推断 companies 和 versions
    """

    def __init__(self, config_path: str = None, auto_find: bool = True):
        """
        初始化配置解析器

        Args:
            config_path: 配置文件路径（默认为 env.json）
            auto_find: 是否自动向上查找配置文件
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
        向上查找配置文件

        Args:
            filename: 配置文件名
            max_levels: 最多向上查找的层级

        Returns:
            str: 配置文件路径，如果未找到则返回 None
        """
        current_dir = Path.cwd()

        for _ in range(max_levels + 1):
            config_file = current_dir / filename
            if config_file.exists():
                logger.info(f"找到配置文件: {config_file}")
                return str(config_file)

            # 向上一级
            parent = current_dir.parent
            if parent == current_dir:  # 已到根目录
                break
            current_dir = parent

        # 未找到，返回当前目录的默认路径
        default_path = Path.cwd() / filename
        logger.warning(f"未找到配置文件，使用默认路径: {default_path}")
        return str(default_path)

    def load(self, validate: bool = True) -> bool:
        """
        加载配置文件

        Args:
            validate: 是否验证配置

        Returns:
            bool: 是否成功加载
        """
        if not os.path.exists(self._config_path):
            logger.error(f"配置文件不存在: {self._config_path}")
            return False

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)

            logger.info(f"成功加载配置文件: {self._config_path}")

            # 验证配置
            if validate:
                return self._validate()

            return True

        except json.JSONDecodeError as e:
            logger.error(f"配置文件 JSON 格式错误: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"加载配置文件失败: {str(e)}")
            return False

    def _validate(self) -> bool:
        """
        验证配置文件

        Returns:
            bool: 是否验证通过
        """
        # 验证版本
        is_valid, error_msg = validate_config_version(self._config)
        if not is_valid:
            logger.error(f"配置版本验证失败: {error_msg}")
            return False

        # 验证结构
        is_valid, errors = validate_config_structure(self._config)
        if not is_valid:
            logger.error("配置结构验证失败:")
            for error in errors:
                logger.error(f"  - {error}")
            return False

        logger.info("配置验证通过")
        return True

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值（支持点号路径）

        Args:
            key: 配置键（支持 "grpc.servers.demo.port" 格式）
            default: 默认值

        Returns:
            Any: 配置值
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
        获取完整配置

        Returns:
            dict: 配置字典
        """
        if not self._config:
            self.load()

        return deepcopy(self._config) if self._config else {}

    def get_config_path(self) -> str:
        """获取配置文件路径"""
        return self._config_path

    def get_config_version(self) -> str:
        """获取配置版本"""
        return self.get("config_version", "未知")

    # ==================== Pipeline 配置相关方法 ====================

    def get_pipelines(self) -> List[str]:
        """
        获取所有 pipeline 列表

        Returns:
            List[str]: pipeline 名称列表
        """
        if not self._config:
            self.load()

        pipelines = self.get("pipelines", {})
        return list(pipelines.keys())

    def get_pipeline_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定 pipeline 的配置

        Args:
            pipeline_name: pipeline 名称

        Returns:
            dict: pipeline 配置
        """
        return self.get(f"pipelines.{pipeline_name}")

    # ==================== gRPC 配置相关方法 ====================

    def get_grpc_server_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定 pipeline 的 gRPC 服务器配置

        Args:
            pipeline_name: pipeline 名称

        Returns:
            dict: gRPC 服务器配置
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.server")

    def get_grpc_deployment_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定 pipeline 的 gRPC 部署配置

        Args:
            pipeline_name: pipeline 名称

        Returns:
            dict: gRPC 部署配置
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.deployment")

    def get_grpc_gke_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定 pipeline 的 GKE 配置

        Args:
            pipeline_name: pipeline 名称

        Returns:
            dict: GKE 配置
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.deployment.gke")

    def is_grpc_enabled(self, pipeline_name: str) -> bool:
        """
        检查指定 pipeline 是否启用 gRPC

        Args:
            pipeline_name: pipeline 名称

        Returns:
            bool: 是否启用
        """
        grpc_config = self.get(f"pipelines.{pipeline_name}.grpc")
        return grpc_config is not None

    def is_gke_enabled(self, pipeline_name: str) -> bool:
        """
        检查指定 pipeline 是否启用 GKE 部署

        Args:
            pipeline_name: pipeline 名称

        Returns:
            bool: 是否启用
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.deployment.gke.enabled", False)

    def get_grpc_sentry_config(self, pipeline_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定 pipeline 的 Sentry 配置

        Args:
            pipeline_name: pipeline 名称

        Returns:
            dict: Sentry 配置
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.sentry")

    def is_sentry_enabled(self, pipeline_name: str) -> bool:
        """
        检查指定 pipeline 是否启用 Sentry

        Args:
            pipeline_name: pipeline 名称

        Returns:
            bool: 是否启用
        """
        return self.get(f"pipelines.{pipeline_name}.grpc.sentry.on", False)

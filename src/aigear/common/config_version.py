"""
配置版本管理模块

提供配置文件的版本验证、比较和管理功能。
"""

from typing import Tuple
from packaging import version as pkg_version


# 版本常量
MIN_SUPPORTED_VERSION = "1.0.0"
CURRENT_VERSION = "1.0.0"
MAX_SUPPORTED_VERSION = "1.999.999"  # 支持所有 1.x 版本

CONFIG_SCHEMA = "aigear-grpc"


def is_version_supported(config_version: str) -> bool:
    """
    检查配置版本是否被当前代码支持

    Args:
        config_version: 配置文件版本号

    Returns:
        bool: 是否支持该版本
    """
    try:
        v = pkg_version.parse(config_version)
        min_v = pkg_version.parse(MIN_SUPPORTED_VERSION)
        max_v = pkg_version.parse(MAX_SUPPORTED_VERSION)

        return min_v <= v <= max_v
    except Exception:
        return False


def compare_versions(version1: str, version2: str) -> int:
    """
    比较两个版本号

    Args:
        version1: 版本号1
        version2: 版本号2

    Returns:
        int: -1 (version1 < version2), 0 (相等), 1 (version1 > version2)
    """
    v1 = pkg_version.parse(version1)
    v2 = pkg_version.parse(version2)

    if v1 < v2:
        return -1
    elif v1 > v2:
        return 1
    else:
        return 0


def get_version_info(config_version: str) -> dict:
    """
    获取版本详细信息

    Args:
        config_version: 配置文件版本号

    Returns:
        dict: 版本信息
    """
    v = pkg_version.parse(config_version)

    return {
        "version": config_version,
        "major": v.major,
        "minor": v.minor,
        "micro": v.micro,
        "is_supported": is_version_supported(config_version),
        "is_current": config_version == CURRENT_VERSION,
        "needs_upgrade": compare_versions(config_version, CURRENT_VERSION) < 0,
    }


def validate_config_version(config: dict) -> Tuple[bool, str]:
    """
    验证配置文件的版本信息

    Args:
        config: 配置字典

    Returns:
        Tuple[bool, str]: (是否有效, 错误信息)
    """
    # 检查 config_version 字段
    if "config_version" not in config:
        return False, "配置文件缺少 config_version 字段"

    config_version = config["config_version"]

    # 检查版本格式
    try:
        pkg_version.parse(config_version)
    except Exception as e:
        return False, f"config_version 格式无效: {config_version}"

    # 检查版本支持
    if not is_version_supported(config_version):
        return False, (
            f"不支持的配置版本: {config_version}\n"
            f"当前代码支持的版本范围: {MIN_SUPPORTED_VERSION} - {MAX_SUPPORTED_VERSION}"
        )

    # 检查 config_schema 字段
    if "config_schema" not in config:
        return False, "配置文件缺少 config_schema 字段"

    if config["config_schema"] != CONFIG_SCHEMA:
        return False, (
            f"config_schema 不匹配: {config['config_schema']}\n"
            f"期望值: {CONFIG_SCHEMA}"
        )

    return True, ""

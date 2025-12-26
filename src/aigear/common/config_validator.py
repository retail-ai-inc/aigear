"""
配置验证模块

提供配置文件的结构验证和一致性检查功能。
"""

import json
from pathlib import Path
from typing import Tuple, List, Dict, Any
from jsonschema import validate, ValidationError, Draft7Validator

from .config_version import validate_config_version, CONFIG_SCHEMA


def load_schema(version: str = "1.0.0") -> dict:
    """
    加载指定版本的配置 Schema

    Args:
        version: 配置版本号

    Returns:
        dict: JSON Schema
    """
    schema_file = Path(__file__).parent.parent / "schemas" / f"config_v{version}.json"

    if not schema_file.exists():
        raise FileNotFoundError(f"Schema 文件不存在: {schema_file}")

    with open(schema_file, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_config_structure(config: dict) -> Tuple[bool, List[str]]:
    """
    验证配置文件的结构

    Args:
        config: 配置字典

    Returns:
        Tuple[bool, List[str]]: (是否有效, 错误列表)
    """
    errors = []

    # 1. 验证版本信息
    is_valid, error_msg = validate_config_version(config)
    if not is_valid:
        errors.append(error_msg)
        return False, errors

    # 2. 使用 JSON Schema 验证
    try:
        config_version = config.get("config_version", "1.0.0")
        schema = load_schema(config_version)

        validator = Draft7Validator(schema)
        validation_errors = list(validator.iter_errors(config))

        for error in validation_errors:
            path = ".".join(str(p) for p in error.path)
            errors.append(f"{path}: {error.message}")

    except FileNotFoundError as e:
        errors.append(str(e))
    except Exception as e:
        errors.append(f"Schema 验证失败: {str(e)}")

    # 3. 自定义验证规则
    custom_errors = _validate_custom_rules(config)
    errors.extend(custom_errors)

    return len(errors) == 0, errors


def _validate_custom_rules(config: dict) -> List[str]:
    """
    自定义验证规则

    Args:
        config: 配置字典

    Returns:
        List[str]: 错误列表
    """
    errors = []

    # 验证 grpc.servers
    grpc_config = config.get("grpc", {})
    servers = grpc_config.get("servers", {})

    if not servers:
        errors.append("grpc.servers 不能为空")
        return errors

    # 验证端口唯一性
    ports = []
    for company, server_config in servers.items():
        port = server_config.get("port")
        if port:
            if port in ports:
                errors.append(f"端口 {port} 重复使用")
            ports.append(port)

    # 验证 modelPaths
    for company, server_config in servers.items():
        model_paths = server_config.get("modelPaths", {})

        if not model_paths:
            errors.append(f"公司 '{company}' 的 modelPaths 不能为空")
            continue

        # 验证每个版本的配置
        for version, path_config in model_paths.items():
            mode = path_config.get("mode")
            base_path = path_config.get("base_path")

            if not mode:
                errors.append(f"公司 '{company}' 版本 '{version}' 缺少 mode 配置")

            if not base_path:
                errors.append(f"公司 '{company}' 版本 '{version}' 缺少 base_path 配置")

            if mode not in ["manifest", "explicit"]:
                errors.append(
                    f"公司 '{company}' 版本 '{version}' 的 mode 必须是 'manifest' 或 'explicit'"
                )

    # 验证 deployment 配置
    deployment = grpc_config.get("deployment", {})
    if deployment.get("enabled"):
        gke_config = deployment.get("gke", {})

        if gke_config.get("enabled"):
            cluster = gke_config.get("cluster", {})

            required_cluster_fields = ["name", "location"]
            for field in required_cluster_fields:
                if not cluster.get(field):
                    errors.append(f"GKE 集群配置缺少必填字段: {field}")

            # 验证 autoscaling 配置
            if cluster.get("enable_autoscaling"):
                min_nodes = cluster.get("min_nodes", 0)
                max_nodes = cluster.get("max_nodes", 0)

                if min_nodes > max_nodes:
                    errors.append(
                        f"GKE 集群的 min_nodes ({min_nodes}) 不能大于 max_nodes ({max_nodes})"
                    )

    return errors


def validate_config_file(file_path: str) -> Tuple[bool, List[str]]:
    """
    验证配置文件

    Args:
        file_path: 配置文件路径

    Returns:
        Tuple[bool, List[str]]: (是否有效, 错误列表)
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        return validate_config_structure(config)

    except FileNotFoundError:
        return False, [f"配置文件不存在: {file_path}"]
    except json.JSONDecodeError as e:
        return False, [f"JSON 格式错误: {str(e)}"]
    except Exception as e:
        return False, [f"验证失败: {str(e)}"]


def get_companies_from_config(config: dict) -> List[str]:
    """
    从配置中提取公司列表

    Args:
        config: 配置字典

    Returns:
        List[str]: 公司列表
    """
    servers = config.get("grpc", {}).get("servers", {})
    return list(servers.keys())


def get_versions_from_config(config: dict, company: str = None) -> Dict[str, List[str]]:
    """
    从配置中提取版本列表

    Args:
        config: 配置字典
        company: 公司名称（可选，如果指定则只返回该公司的版本）

    Returns:
        Dict[str, List[str]]: {公司: [版本列表]}
    """
    servers = config.get("grpc", {}).get("servers", {})
    versions_map = {}

    for comp, server_config in servers.items():
        if company and comp != company:
            continue

        model_paths = server_config.get("modelPaths", {})
        versions_map[comp] = list(model_paths.keys())

    return versions_map


def get_all_versions_from_config(config: dict) -> List[str]:
    """
    从配置中提取所有唯一的版本号

    Args:
        config: 配置字典

    Returns:
        List[str]: 版本列表（去重）
    """
    versions_map = get_versions_from_config(config)
    all_versions = []

    for versions in versions_map.values():
        all_versions.extend(versions)

    return list(set(all_versions))

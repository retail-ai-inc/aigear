"""
Configuration Validation Module

Provides structure validation and consistency checking for configuration files.
"""

import json
from pathlib import Path
from typing import Tuple, List, Dict, Any
from jsonschema import validate, ValidationError, Draft7Validator

from .config_version import validate_config_version, CONFIG_SCHEMA


def load_schema(version: str = "1.0.0") -> dict:
    """
    Load configuration Schema for specified version

    Args:
        version: Configuration version number

    Returns:
        dict: JSON Schema
    """
    schema_file = Path(__file__).parent.parent / "schemas" / f"config_v{version}.json"

    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file does not exist: {schema_file}")

    with open(schema_file, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_config_structure(config: dict) -> Tuple[bool, List[str]]:
    """
    Validate configuration file structure

    Args:
        config: Configuration dictionary

    Returns:
        Tuple[bool, List[str]]: (Whether valid, Error list)
    """
    errors = []

    # 1. Validate version information
    is_valid, error_msg = validate_config_version(config)
    if not is_valid:
        errors.append(error_msg)
        return False, errors

    # 2. Validate using JSON Schema
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
        errors.append(f"Schema validation failed: {str(e)}")

    # 3. Custom validation rules
    custom_errors = _validate_custom_rules(config)
    errors.extend(custom_errors)

    return len(errors) == 0, errors


def _validate_custom_rules(config: dict) -> List[str]:
    """
    Custom validation rules

    Args:
        config: Configuration dictionary

    Returns:
        List[str]: Error list
    """
    errors = []

    # Validate grpc.servers
    grpc_config = config.get("grpc", {})
    servers = grpc_config.get("servers", {})

    if not servers:
        errors.append("grpc.servers cannot be empty")
        return errors

    # Validate port uniqueness
    ports = []
    for company, server_config in servers.items():
        port = server_config.get("port")
        if port:
            if port in ports:
                errors.append(f"Port {port} is duplicated")
            ports.append(port)

    # Validate modelPaths
    for company, server_config in servers.items():
        model_paths = server_config.get("modelPaths", {})

        if not model_paths:
            errors.append(f"modelPaths for company '{company}' cannot be empty")
            continue

        # Validate configuration for each version
        for version, path_config in model_paths.items():
            mode = path_config.get("mode")
            base_path = path_config.get("base_path")

            if not mode:
                errors.append(f"Company '{company}' version '{version}' is missing mode configuration")

            if not base_path:
                errors.append(f"Company '{company}' version '{version}' is missing base_path configuration")

            if mode not in ["manifest", "explicit"]:
                errors.append(
                    f"Company '{company}' version '{version}' mode must be 'manifest' or 'explicit'"
                )

    # Validate deployment configuration
    deployment = grpc_config.get("deployment", {})
    if deployment.get("enabled"):
        gke_config = deployment.get("gke", {})

        if gke_config.get("enabled"):
            cluster = gke_config.get("cluster", {})

            required_cluster_fields = ["name", "location"]
            for field in required_cluster_fields:
                if not cluster.get(field):
                    errors.append(f"GKE cluster configuration is missing required field: {field}")

            # Validate autoscaling configuration
            if cluster.get("enable_autoscaling"):
                min_nodes = cluster.get("min_nodes", 0)
                max_nodes = cluster.get("max_nodes", 0)

                if min_nodes > max_nodes:
                    errors.append(
                        f"GKE cluster min_nodes ({min_nodes}) cannot be greater than max_nodes ({max_nodes})"
                    )

    return errors


def validate_config_file(file_path: str) -> Tuple[bool, List[str]]:
    """
    Validate configuration file

    Args:
        file_path: Configuration file path

    Returns:
        Tuple[bool, List[str]]: (Whether valid, Error list)
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        return validate_config_structure(config)

    except FileNotFoundError:
        return False, [f"Configuration file does not exist: {file_path}"]
    except json.JSONDecodeError as e:
        return False, [f"JSON format error: {str(e)}"]
    except Exception as e:
        return False, [f"Validation failed: {str(e)}"]


def get_companies_from_config(config: dict) -> List[str]:
    """
    Extract company list from configuration

    Args:
        config: Configuration dictionary

    Returns:
        List[str]: Company list
    """
    servers = config.get("grpc", {}).get("servers", {})
    return list(servers.keys())


def get_versions_from_config(config: dict, company: str = None) -> Dict[str, List[str]]:
    """
    Extract version list from configuration

    Args:
        config: Configuration dictionary
        company: Company name (optional, if specified only returns versions for that company)

    Returns:
        Dict[str, List[str]]: {company: [version list]}
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
    Extract all unique version numbers from configuration

    Args:
        config: Configuration dictionary

    Returns:
        List[str]: Version list (deduplicated)
    """
    versions_map = get_versions_from_config(config)
    all_versions = []

    for versions in versions_map.values():
        all_versions.extend(versions)

    return list(set(all_versions))

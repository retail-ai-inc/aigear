"""
Configuration Version Management Module

Provides version validation, comparison, and management functionality for configuration files.
"""

from typing import Tuple
from packaging import version as pkg_version


# Version constants
MIN_SUPPORTED_VERSION = "1.0.0"
CURRENT_VERSION = "1.0.0"
MAX_SUPPORTED_VERSION = "1.999.999"  # Support all 1.x versions

CONFIG_SCHEMA = "aigear-grpc"


def is_version_supported(config_version: str) -> bool:
    """
    Check if the configuration version is supported by current code

    Args:
        config_version: Configuration file version number

    Returns:
        bool: Whether the version is supported
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
    Compare two version numbers

    Args:
        version1: Version number 1
        version2: Version number 2

    Returns:
        int: -1 (version1 < version2), 0 (equal), 1 (version1 > version2)
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
    Get detailed version information

    Args:
        config_version: Configuration file version number

    Returns:
        dict: Version information
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
    Validate configuration file version information

    Args:
        config: Configuration dictionary

    Returns:
        Tuple[bool, str]: (Whether valid, Error message)
    """
    # Check config_version field
    if "config_version" not in config:
        return False, "Configuration file is missing config_version field"

    config_version = config["config_version"]

    # Check version format
    try:
        pkg_version.parse(config_version)
    except Exception as e:
        return False, f"config_version format is invalid: {config_version}"

    # Check version support
    if not is_version_supported(config_version):
        return False, (
            f"Unsupported configuration version: {config_version}\n"
            f"Current code supports version range: {MIN_SUPPORTED_VERSION} - {MAX_SUPPORTED_VERSION}"
        )

    # Check config_schema field
    if "config_schema" not in config:
        return False, "Configuration file is missing config_schema field"

    if config["config_schema"] != CONFIG_SCHEMA:
        return False, (
            f"config_schema does not match: {config['config_schema']}\n"
            f"Expected value: {CONFIG_SCHEMA}"
        )

    return True, ""

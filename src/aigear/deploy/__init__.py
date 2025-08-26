# Deploy module - lazy imports to avoid dependency issues

def _import_docker():
    """Lazy import docker module"""
    from . import docker
    return docker

def _import_gcp():
    """Lazy import gcp module"""
    from . import gcp
    return gcp

# Export available modules
__all__ = [
    "docker",
    "gcp",
]

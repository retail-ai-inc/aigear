# Version info
from ._version import __version__

# Core project class
from .project import Project

# Lazy imports to avoid dependency issues
def _import_microservices():
    """Lazy import microservices module"""
    from . import microservices
    return microservices

def _import_pipeline():
    """Lazy import pipeline module"""
    from . import pipeline
    return pipeline

# Export core items
__all__ = [
    "Project",
    "__version__",
    "microservices",
    "pipeline",
]

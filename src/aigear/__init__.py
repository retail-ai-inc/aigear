from importlib.metadata import version
from aigear.infrastructure import Infra
from .common import (
    Logging,
    SecretManager,
    generate_schema,
    generate_schema_for_json,
)
from .project import Project

__all__ = [
    "Logging",
    "generate_schema",
    "generate_schema_for_json",
    "SecretManager",
    "Infra",
]

__version__ = version("aigear")
__all__.extend(
    [
        Project,
        __version__,
    ]
)

import os
from .project import Project
from ._version import __version__
from .common import (
    run_sh,
    Logging,
    read_config,
    generate_schema,
    generate_schema_for_json,
    SecretManager,
)
from aigear.infrastructure import Infra
from aigear.deploy.gcp import Scheduler

aigear_config = read_config(env_path=os.path.join(os.getcwd(), "env.json"))
aigear_logger = Logging(log_name='aigear_logging').console_logging()

__all__ = [
    "run_sh",
    "Logging",
    "aigear_config",
    "generate_schema",
    "generate_schema_for_json",
    "SecretManager",
    "Infra",
    "Scheduler",
]

__all__.extend(
    [
        Project,
        __version__,
    ]
)

from aigear.common.sh import run_sh
from aigear.common.logger import Logging, get_logger_for_stage
from aigear.common.config import AigearConfig
from aigear.common.dynamic_type import generate_schema, generate_schema_for_json
from aigear.common.secretmanager import SecretManager

# Import new stage-aware logging functionality
try:
    from aigear.common.stage_logger import (
        create_stage_logger,
        PipelineStage,
        StageResourceConfig,
        StageAwareLogger
    )
    __all__ = [
        "run_sh",
        "Logging",
        "get_logger_for_stage",
        "create_stage_logger",
        "PipelineStage",
        "StageResourceConfig",
        "StageAwareLogger",
        "AigearConfig",
        "generate_schema",
        "generate_schema_for_json",
        "SecretManager",
    ]
except ImportError:
    # If new functionality is unavailable, only export traditional features
    __all__ = [
        "run_sh",
        "Logging",
        "get_logger_for_stage",
        "AigearConfig",
        "generate_schema",
        "generate_schema_for_json",
        "SecretManager",
    ]

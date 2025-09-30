from aigear.common.sh import run_sh
from aigear.common.config import AigearConfig
from aigear.common.dynamic_type import generate_schema, generate_schema_for_json
from aigear.common.secretmanager import SecretManager

# Import logging functionality from the new logger module
try:
    from aigear.common.logger import (
        Logging,
        create_stage_logger,
        PipelineStage,
        StageAwareLogger
    )
    __all__ = [
        "run_sh",
        "Logging",
        "create_stage_logger",
        "PipelineStage",
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
        "AigearConfig",
        "generate_schema",
        "generate_schema_for_json",
        "SecretManager",
    ]

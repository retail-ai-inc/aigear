"""
Aigear Logging System

This module provides comprehensive logging capabilities for MLOps pipelines:

1. Traditional Logging (logger.py):
   - Backward compatibility with existing code
   - Console and file-based logging
   - Standard Python logging interface

2. Stage-Aware Logging (stage_logger.py):
   - Pipeline stage-specific logging (TRAINING, INFERENCE, DEPLOYMENT, etc.)
   - MLOps-specific methods (log_epoch, log_prediction, log_checkpoint)
   - Structured JSON output
   - Resource monitoring integration
   - Google Cloud Logging support

Usage Examples:

# Traditional logging
from aigear.common.logger import Logging
logger = Logging(log_name="my_module").console_logging()
logger.info("Traditional log message")

# Stage-aware logging
from aigear.common.logger import create_stage_logger, PipelineStage
training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name=__name__
)
with training_logger.stage_context() as logger:
    logger.log_epoch(1, 0.5, {"accuracy": 0.85})
"""

# Import all logging functionality
from .logger import Logging
from .stage_logger import (
    create_stage_logger,
    PipelineStage,
    StageAwareLogger
)

# Export main components
__all__ = [
    # Traditional logging
    'Logging',

    # Stage-aware logging
    'create_stage_logger',
    'PipelineStage',
    'StageAwareLogger'
]
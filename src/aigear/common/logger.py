import logging
import json
import sys
import os
from typing import Optional
# Optional dependency handling
try:
    from google.cloud.logging_v2.handlers import StructuredLogHandler
    GCP_LOGGING_AVAILABLE = True
except ImportError:
    StructuredLogHandler = None
    GCP_LOGGING_AVAILABLE = False

# Import new stage-aware logger
try:
    from .stage_logger import create_stage_logger, PipelineStage
    STAGE_LOGGER_AVAILABLE = True
except ImportError:
    STAGE_LOGGER_AVAILABLE = False


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'process': record.process,
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'message': record.getMessage(),
        }

        # Add additional context information
        if hasattr(record, 'stage'):
            log_data['stage'] = record.stage
        if hasattr(record, 'resource_usage'):
            log_data['resource_usage'] = record.resource_usage

        return json.dumps(log_data, ensure_ascii=False)


class Logging:
    """
    Traditional logger class, maintains backward compatibility
    Recommended to use create_stage_logger function in MLOps scenarios
    """
    def __init__(
        self,
        log_name: str = None,
        project_id: str = None
    ):
        self.client = None
        self.project_id = project_id
        self.log_name = log_name

    def root_logger(self):
        logger = logging.getLogger(self.log_name)
        # Read log level from environment variable, default to INFO
        log_level = os.getenv('AIGEAR_LOG_LEVEL', 'INFO').upper()
        logger.setLevel(getattr(logging, log_level, logging.INFO))
        return logger

    def gcp_logging_handler(self):
        if not GCP_LOGGING_AVAILABLE:
            print("Warning: Google Cloud Logging not available. Install google-cloud-logging to enable.")
            return None
        try:
            handler = StructuredLogHandler(project_id=self.project_id)
            return handler
        except Exception as e:
            # If GCP connection fails, log warning but do not interrupt program
            print(f"Warning: Failed to create GCP logging handler: {e}")
            return None

    def cloud_logging(self):
        logger = self.root_logger()
        console_handler = self.console_logging_handler()
        gcp_handler = self.gcp_logging_handler()

        logger.addHandler(console_handler)
        if gcp_handler:
            logger.addHandler(gcp_handler)

        return logger

    @staticmethod
    def console_logging_handler():
        handler = logging.StreamHandler(sys.stdout)
        formatter = JsonFormatter()
        handler.setFormatter(formatter)
        return handler

    def console_logging(self):
        logger = self.root_logger()
        console_handler = self.console_logging_handler()
        logger.addHandler(console_handler)
        return logger

    @classmethod
    def create_for_stage(cls,
                        stage: str,
                        module_name: str,
                        cpu_count: int = 1,
                        memory_limit: str = "1GB",
                        gpu_enabled: bool = False,
                        project_id: Optional[str] = None) -> 'Logging':
        """
        Create optimized logger for specific stage
        This is a bridge method between new and old APIs
        """
        if STAGE_LOGGER_AVAILABLE:
            # If new stage logger is available, return a wrapper
            stage_logger = create_stage_logger(
                stage=stage,
                module_name=module_name,
                cpu_count=cpu_count,
                memory_limit=memory_limit,
                gpu_enabled=gpu_enabled,
                project_id=project_id
            )
            return LoggingWrapper(stage_logger)
        else:
            # Fallback to traditional logger
            return cls(log_name=f"{module_name}.{stage}", project_id=project_id)


class LoggingWrapper:
    """
    Wrap new stage-aware logger, providing interface compatible with old API
    """
    def __init__(self, stage_logger):
        self.stage_logger = stage_logger

    def console_logging(self):
        # Return a context manager
        return self.stage_logger.stage_context()


# Convenience function: backward compatible API
def get_logger_for_stage(stage: str,
                        module_name: str,
                        **kwargs) -> logging.Logger:
    """
    Convenience function: get stage-specific logger
    """
    if STAGE_LOGGER_AVAILABLE:
        stage_logger = create_stage_logger(
            stage=stage,
            module_name=module_name,
            **kwargs
        )
        return stage_logger.stage_context()
    else:
        # Fallback to traditional method
        traditional_logger = Logging(log_name=f"{module_name}.{stage}")
        return traditional_logger.console_logging()

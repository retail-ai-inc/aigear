"""
Stage-aware logging manager
Supports specific logging requirements and resource configurations for different MLOps stages
"""
import logging
import json
import sys
import time
from typing import Dict, Any, Optional, Union
from contextlib import contextmanager
from enum import Enum

# Optional dependencies
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    from google.cloud.logging_v2.handlers import StructuredLogHandler
    GCP_LOGGING_AVAILABLE = True
except ImportError:
    GCP_LOGGING_AVAILABLE = False

# Import JsonFormatter, create simplified version if import fails
try:
    from aigear.common.logger import JsonFormatter
except ImportError:
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log_data = {
                'process': record.process,
                'timestamp': self.formatTime(record),
                'level': record.levelname,
                'message': record.getMessage(),
            }
            return json.dumps(log_data, ensure_ascii=False)


class PipelineStage(Enum):
    """Pipeline stage enumeration"""
    TRAINING = "training"
    INFERENCE = "inference"
    PREPROCESSING = "preprocessing"
    EVALUATION = "evaluation"
    DEPLOYMENT = "deployment"


class StageResourceConfig:
    """Stage resource configuration"""
    def __init__(self,
                 cpu_count: int = 1,
                 memory_limit: str = "1GB",
                 gpu_enabled: bool = False,
                 io_intensive: bool = False):
        self.cpu_count = cpu_count
        self.memory_limit = memory_limit
        self.gpu_enabled = gpu_enabled
        self.io_intensive = io_intensive


class MLOpsFormatter(JsonFormatter):
    """MLOps-specific formatter"""

    def __init__(self, stage: PipelineStage, resource_config: StageResourceConfig):
        super().__init__()
        self.stage = stage
        self.resource_config = resource_config

    def format(self, record):
        log_data = {
            'timestamp': self.formatTime(record),
            'stage': self.stage.value,
            'level': record.levelname,
            'message': record.getMessage(),
            'module': record.name,
            'process_id': record.process,
            'thread_id': record.thread,
        }

        # Add resource information
        if hasattr(record, 'resource_usage'):
            log_data['resource_usage'] = record.resource_usage

        # Add stage-specific information
        if self.stage == PipelineStage.TRAINING:
            log_data.update(self._get_training_context(record))
        elif self.stage == PipelineStage.INFERENCE:
            log_data.update(self._get_inference_context(record))

        return json.dumps(log_data, ensure_ascii=False)

    def _get_training_context(self, record):
        """Training stage specific context"""
        context = {}
        if hasattr(record, 'epoch'):
            context['epoch'] = record.epoch
        if hasattr(record, 'loss'):
            context['loss'] = record.loss
        if hasattr(record, 'metrics'):
            context['metrics'] = record.metrics
        return context

    def _get_inference_context(self, record):
        """Inference stage specific context"""
        context = {}
        if hasattr(record, 'latency_ms'):
            context['latency_ms'] = record.latency_ms
        if hasattr(record, 'batch_size'):
            context['batch_size'] = record.batch_size
        if hasattr(record, 'prediction_confidence'):
            context['prediction_confidence'] = record.prediction_confidence
        return context


class StageAwareLogger:
    """Stage-aware logging manager"""

    def __init__(self,
                 stage: PipelineStage,
                 resource_config: StageResourceConfig,
                 module_name: str,
                 project_id: Optional[str] = None,
                 enable_cloud_logging: bool = False):
        self.stage = stage
        self.resource_config = resource_config
        self.module_name = module_name
        self.project_id = project_id
        self.enable_cloud_logging = enable_cloud_logging
        self._logger = None
        self._start_time = None
        self._resource_monitor_enabled = False

    def _create_logger(self) -> logging.Logger:
        """Create configured logger"""
        logger_name = f"{self.module_name}.{self.stage.value}"
        logger = logging.getLogger(logger_name)

        # Clear existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

        # Set log level (adjusted by stage)
        logger.setLevel(self._get_stage_log_level())

        # Add console handler
        console_handler = self._create_console_handler()
        logger.addHandler(console_handler)

        # Add cloud logging handler (if enabled)
        if self.enable_cloud_logging and self.project_id:
            cloud_handler = self._create_cloud_handler()
            if cloud_handler:
                logger.addHandler(cloud_handler)

        return logger

    def _get_stage_log_level(self) -> int:
        """Get appropriate log level based on stage"""
        level_map = {
            PipelineStage.TRAINING: logging.DEBUG,      # Training needs detailed logs
            PipelineStage.INFERENCE: logging.INFO,      # Inference focuses on performance
            PipelineStage.PREPROCESSING: logging.INFO,  # Data processing
            PipelineStage.EVALUATION: logging.DEBUG,    # Evaluation needs detailed info
            PipelineStage.DEPLOYMENT: logging.WARNING,  # Deployment only cares about issues
        }
        return level_map.get(self.stage, logging.INFO)

    def _create_console_handler(self) -> logging.StreamHandler:
        """Create console handler"""
        handler = logging.StreamHandler(sys.stdout)
        formatter = MLOpsFormatter(self.stage, self.resource_config)
        handler.setFormatter(formatter)
        return handler

    def _create_cloud_handler(self) -> Optional['StructuredLogHandler']:
        """Create cloud logging handler"""
        if not GCP_LOGGING_AVAILABLE:
            print("Warning: Google Cloud Logging not available. Install google-cloud-logging to enable cloud logging.")
            return None

        try:
            handler = StructuredLogHandler(project_id=self.project_id)
            return handler
        except Exception as e:
            # Fallback to console when cloud logging fails
            print(f"Failed to create cloud logging handler: {e}")
            return None

    @contextmanager
    def stage_context(self):
        """Stage context manager"""
        self._start_time = time.time()
        self._logger = self._create_logger()

        # Enable resource monitoring (only when needed)
        if self._should_monitor_resources():
            self._resource_monitor_enabled = True

        try:
            self._logger.info(f"Starting {self.stage.value} stage",
                            extra={'resource_config': self.resource_config.__dict__})
            yield StageLoggerProxy(self._logger, self.stage)
        finally:
            duration = time.time() - self._start_time
            self._logger.info(f"Completed {self.stage.value} stage in {duration:.2f}s")
            self._cleanup()

    def _should_monitor_resources(self) -> bool:
        """Determine if resource monitoring is needed"""
        return (self.resource_config.gpu_enabled or
                self.stage in [PipelineStage.TRAINING, PipelineStage.PREPROCESSING])

    def _cleanup(self):
        """Clean up resources"""
        if self._logger:
            for handler in self._logger.handlers[:]:
                handler.close()
                self._logger.removeHandler(handler)


class StageLoggerProxy:
    """Logger proxy providing stage-specific convenience methods"""

    def __init__(self, logger: logging.Logger, stage: PipelineStage):
        self._logger = logger
        self.stage = stage

    def info(self, message: str, **kwargs):
        self._logger.info(message, extra=kwargs)

    def debug(self, message: str, **kwargs):
        self._logger.debug(message, extra=kwargs)

    def warning(self, message: str, **kwargs):
        self._logger.warning(message, extra=kwargs)

    def error(self, message: str, **kwargs):
        self._logger.error(message, extra=kwargs)

    # Training-specific methods
    def log_epoch(self, epoch: int, loss: float, metrics: Dict[str, float]):
        """Log training epoch"""
        if self.stage == PipelineStage.TRAINING:
            self.info(f"Epoch {epoch} completed",
                     epoch=epoch, loss=loss, metrics=metrics)

    def log_checkpoint(self, checkpoint_path: str, epoch: int):
        """Log checkpoint save"""
        if self.stage == PipelineStage.TRAINING:
            self.info(f"Checkpoint saved",
                     checkpoint_path=checkpoint_path, epoch=epoch)

    # Inference-specific methods
    def log_prediction(self, latency_ms: float, batch_size: int, confidence: float = None):
        """Log prediction performance"""
        if self.stage == PipelineStage.INFERENCE:
            extra = {'latency_ms': latency_ms, 'batch_size': batch_size}
            if confidence:
                extra['prediction_confidence'] = confidence
            self.info(f"Prediction completed in {latency_ms}ms", **extra)

    # Resource monitoring methods
    def log_resource_usage(self):
        """Log current resource usage"""
        if not PSUTIL_AVAILABLE:
            self.warning("Resource monitoring unavailable: psutil not installed")
            return

        try:
            cpu_percent = psutil.cpu_percent()
            memory = psutil.virtual_memory()
            resource_usage = {
                'cpu_percent': cpu_percent,
                'memory_percent': memory.percent,
                'memory_used_gb': memory.used / (1024**3)
            }

            # GPU information (if available)
            try:
                import GPUtil
                gpus = GPUtil.getGPUs()
                if gpus:
                    resource_usage['gpu_usage'] = [
                        {'id': gpu.id, 'load': gpu.load, 'memory_used': gpu.memoryUsed}
                        for gpu in gpus
                    ]
            except ImportError:
                pass

            self.debug("Resource usage", resource_usage=resource_usage)
        except Exception as e:
            self.warning(f"Failed to collect resource usage: {e}")


# Convenience functions
def create_stage_logger(stage: Union[str, PipelineStage],
                       module_name: str,
                       cpu_count: int = 1,
                       memory_limit: str = "1GB",
                       gpu_enabled: bool = False,
                       enable_cloud_logging: bool = False,
                       project_id: Optional[str] = None) -> StageAwareLogger:
    """Create stage-aware logger"""

    if isinstance(stage, str):
        stage = PipelineStage(stage)

    resource_config = StageResourceConfig(
        cpu_count=cpu_count,
        memory_limit=memory_limit,
        gpu_enabled=gpu_enabled
    )

    return StageAwareLogger(
        stage=stage,
        resource_config=resource_config,
        module_name=module_name,
        project_id=project_id,
        enable_cloud_logging=enable_cloud_logging
    )
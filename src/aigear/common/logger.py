import logging
import sys
import json
import threading
from typing import Any, Dict, Optional

# Thread-local log buffer — set to a list to enable buffering for the current thread
_thread_local = threading.local()


# ----------------------------
# Thread-aware handler: buffers output when _thread_local.log_buffer is set
# ----------------------------
class _ThreadAwareStreamHandler(logging.StreamHandler):
    def emit(self, record):
        buf = getattr(_thread_local, "log_buffer", None)
        if buf is not None:
            buf.append(self.format(record))
        else:
            super().emit(record)


# ----------------------------
# Local Formatter: output message and extra (dict)
# ----------------------------
class LocalJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "level": record.levelname,
            "message": record.getMessage(),
        }

        # Include dicts from extra in payload
        for k, v in record.__dict__.items():
            if isinstance(v, dict):
                data[k] = v

        return json.dumps(data, ensure_ascii=False)


# ----------------------------
# Logging factory
# ----------------------------
class Logging:
    def __init__(
        self,
        log_name: str = None,
        project_id: Optional[str] = None,
    ):
        self.log_name = log_name
        self.project_id = project_id
        self._client = None
        self._cloud_logger = None

    # ----------------------------
    # Base logger (local stdout)
    # ----------------------------
    def _base_logger(self) -> logging.Logger:
        logger = logging.getLogger(self.log_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        if not logger.handlers:
            handler = _ThreadAwareStreamHandler(sys.stdout)
            handler.setFormatter(LocalJsonFormatter())
            logger.addHandler(handler)

        return logger

    # ----------------------------
    # Patch cloud logging logic onto the logger
    # ----------------------------
    def _patch_cloud_logging(self, logger: logging.Logger) -> logging.Logger:
        if not self.project_id:
            return logger

        if self._client is None:
            from google.cloud import logging as gcp_logging

            self._client = gcp_logging.Client(project=self.project_id)
            self._cloud_logger = self._client.logger(self.log_name)

        # Prevent duplicate patching
        if getattr(logger, "_cloud_patched", False):
            return logger

        original_info = logger.info

        def info(
            msg: str,
            *args,
            extra: Dict[str, Any] | None = None,
            **kwargs,
        ):
            # Local logging (preserve original behavior)
            original_info(msg, *args, extra=extra, **kwargs)

            # Cloud logging: send a single jsonPayload
            payload = {"message": msg}
            if extra:
                payload.update(extra)

            self._cloud_logger.log_struct(
                payload,
                severity="INFO",
            )

        logger.info = info  # monkey patch
        logger._cloud_patched = True

        return logger

    # ----------------------------
    # Public interface (methods you typically call)
    # ----------------------------
    def console_logging(self) -> logging.Logger:
        return self._base_logger()

    def cloud_logging(self) -> logging.Logger:
        logger = self._base_logger()
        return self._patch_cloud_logging(logger)

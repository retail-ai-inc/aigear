import logging
import os
import sys
import json
import threading
from typing import Any, Dict, Optional

from aigear.common.run_log_context import RunLogContext

# Thread-local log buffer — set to a list to enable buffering for the current thread
_thread_local = threading.local()


class _ThreadAwareStreamHandler(logging.StreamHandler):
    def emit(self, record):
        buf = getattr(_thread_local, "log_buffer", None)
        if buf is not None:
            buf.append(self.format(record))
        else:
            super().emit(record)


class LocalJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "level": record.levelname,
            "message": record.getMessage(),
        }
        ctx = RunLogContext.current()
        if ctx is not None:
            data.update(ctx.as_log_fields())

        for k, v in record.__dict__.items():
            if isinstance(v, dict):
                data[k] = v

        return json.dumps(data, ensure_ascii=False)


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

    def _base_logger(self) -> logging.Logger:
        logger = logging.getLogger(self.log_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        if not logger.handlers:
            handler = _ThreadAwareStreamHandler(sys.stdout)
            handler.setFormatter(LocalJsonFormatter())
            logger.addHandler(handler)

        return logger

    def _merge_payload(
        self,
        msg: str,
        extra: Dict[str, Any] | None,
        severity: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"message": msg, "severity": severity}
        ctx = RunLogContext.current()
        if ctx is not None:
            payload.update(ctx.as_log_fields())
        if extra:
            payload.update(extra)
        return payload

    def _patch_cloud_logging(
        self,
        logger: logging.Logger,
        *,
        cloud_enabled: bool,
    ) -> logging.Logger:
        project_id = self.project_id
        if cloud_enabled and not project_id:
            return logger

        if cloud_enabled and self._client is None:
            from google.cloud import logging as gcp_logging

            self._client = gcp_logging.Client(project=project_id)
            self._cloud_logger = self._client.logger(self.log_name)

        if getattr(logger, "_cloud_patched", False):
            return logger

        originals = {
            "info": logger.info,
            "warning": logger.warning,
            "error": logger.error,
        }

        def _make_method(original, severity: str):
            def method(msg: str, *args, extra: Dict[str, Any] | None = None, **kwargs):
                original(msg, *args, extra=extra, **kwargs)
                if not cloud_enabled or self._cloud_logger is None:
                    return
                payload = self._merge_payload(msg, extra, severity)
                payload.pop("severity", None)
                self._cloud_logger.log_struct(payload, severity=severity)

            return method

        logger.info = _make_method(originals["info"], "INFO")
        logger.warning = _make_method(originals["warning"], "WARNING")
        logger.error = _make_method(originals["error"], "ERROR")
        logger._cloud_patched = True
        return logger

    def console_logging(self) -> logging.Logger:
        return self._base_logger()

    def cloud_logging(self) -> logging.Logger:
        logger = self._base_logger()
        return self._patch_cloud_logging(logger, cloud_enabled=True)

    def for_task(self) -> logging.Logger:
        if os.environ.get("AIGEAR_RUN_ID"):
            ctx = RunLogContext.current() or RunLogContext.from_env()
            project_id = (ctx.project_id if ctx else None) or self.project_id
            if not project_id:
                try:
                    from aigear.common.config import AigearConfig

                    project_id = AigearConfig.get_config().gcp.gcp_project_id
                except Exception:
                    project_id = None
            self.project_id = project_id
            cloud_enabled = bool(ctx and ctx.gcp_logging)
            logger = self._base_logger()
            return self._patch_cloud_logging(logger, cloud_enabled=cloud_enabled)
        return self.console_logging()

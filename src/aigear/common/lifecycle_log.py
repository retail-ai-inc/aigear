from __future__ import annotations

from typing import Any

from aigear.common.logger import Logging
from aigear.common.run_log_context import RunLogContext

_fallback_logger = Logging(log_name=__name__).console_logging()

_MAX_ERROR_MESSAGE_LEN = 2048

_PROTECTED_PAYLOAD_KEYS = frozenset(
    {
        "log_source",
        "event",
        "message",
        "run_id",
        "run_started_at_utc",
        "pipeline_version",
        "step_name",
        "project_name",
    }
)


def _normalize_extra(extra: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(extra)
    if "error_message" in normalized and normalized["error_message"] is not None:
        normalized["error_message"] = str(normalized["error_message"])[
            :_MAX_ERROR_MESSAGE_LEN
        ]
    if "error_type" in normalized and normalized["error_type"] is not None:
        normalized["error_type"] = str(normalized["error_type"])
    return normalized


def emit_lifecycle_log(
    event: str,
    ctx: RunLogContext,
    extra: dict[str, Any] | None = None,
) -> None:
    if not ctx.project_id:
        return
    from google.cloud import logging as gcp_logging

    payload = ctx.as_log_fields()
    payload["event"] = event
    payload["message"] = event
    if extra:
        for key, value in _normalize_extra(extra).items():
            if key in _PROTECTED_PAYLOAD_KEYS:
                continue
            payload[key] = value

    severity = "ERROR" if event == "pipeline_step_failed" else "INFO"
    try:
        client = gcp_logging.Client(project=ctx.project_id)
        cloud_logger = client.logger("aigear-task")
        cloud_logger.log_struct(payload, severity=severity)
    except Exception as err:
        _fallback_logger.warning(
            "Failed to write lifecycle log to Cloud Logging (%s): %s",
            event,
            err,
        )


def emit_step_result(ctx: RunLogContext, result: dict[str, Any]) -> None:
    emit_lifecycle_log("pipeline_step_result", ctx, extra={"result": result})

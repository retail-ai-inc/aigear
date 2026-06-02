from __future__ import annotations

from aigear.common.logger import Logging
from aigear.common.run_log_context import RunLogContext

_fallback_logger = Logging(log_name=__name__).console_logging()


def emit_lifecycle_log(event: str, ctx: RunLogContext) -> None:
    if not ctx.project_id:
        return
    from google.cloud import logging as gcp_logging

    payload = ctx.as_log_fields()
    payload["event"] = event
    payload["message"] = event
    try:
        client = gcp_logging.Client(project=ctx.project_id)
        cloud_logger = client.logger("aigear-task")
        cloud_logger.log_struct(payload, severity="INFO")
    except Exception as err:
        _fallback_logger.warning(
            "Failed to write lifecycle log to Cloud Logging (%s): %s",
            event,
            err,
        )

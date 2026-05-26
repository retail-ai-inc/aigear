from __future__ import annotations

from aigear.common.run_log_context import RunLogContext


def emit_lifecycle_log(event: str, ctx: RunLogContext) -> None:
    if not ctx.project_id:
        return
    from google.cloud import logging as gcp_logging

    client = gcp_logging.Client(project=ctx.project_id)
    cloud_logger = client.logger("aigear-task")
    payload = ctx.as_log_fields()
    payload["event"] = event
    payload["message"] = event
    cloud_logger.log_struct(payload, severity="INFO")

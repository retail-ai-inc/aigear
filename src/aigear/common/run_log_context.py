from __future__ import annotations

import os
import threading
from dataclasses import dataclass

_thread_local = threading.local()


@dataclass
class RunLogContext:
    run_id: str
    run_started_at_utc: str
    pipeline_version: str
    step_name: str
    project_name: str | None = None
    gcp_logging: bool = False
    project_id: str | None = None
    log_source: str = "ml_pipeline"

    def as_log_fields(self) -> dict[str, str]:
        fields = {
            "log_source": self.log_source,
            "run_id": self.run_id,
            "run_started_at_utc": self.run_started_at_utc,
            "pipeline_version": self.pipeline_version,
            "step_name": self.step_name,
        }
        if self.project_name:
            fields["project_name"] = self.project_name
        return fields

    @classmethod
    def from_env(cls) -> RunLogContext | None:
        run_id = os.environ.get("AIGEAR_RUN_ID", "").strip()
        if not run_id:
            return None
        run_started_at_utc = os.environ.get("AIGEAR_RUN_STARTED_AT_UTC", "").strip()
        pipeline_version = os.environ.get("AIGEAR_PIPELINE_VERSION", "").strip()
        step_name = os.environ.get("AIGEAR_STEP_NAME", "").strip() or "model_service"
        if not run_started_at_utc or not pipeline_version:
            return None
        project_name = os.environ.get("AIGEAR_PROJECT_NAME", "").strip() or None
        return cls(
            run_id=run_id,
            run_started_at_utc=run_started_at_utc,
            pipeline_version=pipeline_version,
            step_name=step_name,
            project_name=project_name,
        )

    @classmethod
    def install_from_env(cls, *, gcp_logging: bool, project_id: str) -> RunLogContext | None:
        ctx = cls.from_env()
        if ctx is None:
            _thread_local.run_log_context = None
            return None
        ctx.gcp_logging = gcp_logging
        ctx.project_id = project_id
        _thread_local.run_log_context = ctx
        return ctx

    @classmethod
    def current(cls) -> RunLogContext | None:
        return getattr(_thread_local, "run_log_context", None)

    @classmethod
    def clear(cls) -> None:
        _thread_local.run_log_context = None

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.deploy.gcp.logs_discovery_cache import load_runs_from_cache, save_runs_to_cache
from aigear.infrastructure.gcp.logging import read_logs

_RUN_ID_IN_TEXT = re.compile(r'"run_id"\s*:\s*"([^"]+)"')


@dataclass
class RunSummary:
    run_id: str
    run_started_at_utc: str
    pipeline_version: str
    step_name: str
    project_name: str | None = None

    def to_cache_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_started_at_utc": self.run_started_at_utc,
            "pipeline_version": self.pipeline_version,
            "step_name": self.step_name,
            "project_name": self.project_name,
        }

    @classmethod
    def from_cache_dict(cls, item: dict[str, Any]) -> RunSummary:
        return cls(
            run_id=item["run_id"],
            run_started_at_utc=item["run_started_at_utc"],
            pipeline_version=item["pipeline_version"],
            step_name=item.get("step_name") or "model_service",
            project_name=item.get("project_name"),
        )


def extract_log_fields(entry: dict[str, Any]) -> dict[str, Any] | None:
    payload = entry.get("jsonPayload")
    if isinstance(payload, dict) and payload:
        return payload

    text_payload = entry.get("textPayload")
    if not isinstance(text_payload, str) or not text_payload.strip():
        return None

    stripped = text_payload.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    match = _RUN_ID_IN_TEXT.search(stripped)
    if match:
        return {"run_id": match.group(1)}
    return None


def to_utc_window(run_date: str, tz_name: str) -> tuple[str, str]:
    local_start = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz_name))
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = local_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return start_utc, end_utc


def scheduler_timezone(version: str) -> str:
    pipeline_cfg = PipelinesConfig.get_version_config(version)
    return pipeline_cfg.get("scheduler", {}).get("time_zone", "Etc/UTC")


def _summary_from_payload(payload: dict[str, Any], version: str) -> RunSummary | None:
    run_id = payload.get("run_id")
    run_started_at_utc = payload.get("run_started_at_utc")
    if not run_id or not run_started_at_utc:
        return None
    return RunSummary(
        run_id=run_id,
        run_started_at_utc=run_started_at_utc,
        pipeline_version=payload.get("pipeline_version", version),
        step_name=payload.get("step_name", "model_service"),
        project_name=payload.get("project_name"),
    )


def _query_fields(entry: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    payload = extract_log_fields(entry) or {}
    labels = entry.get("labels")

    for source in (labels, payload):
        if not isinstance(source, dict):
            continue
        for key in ("run_id", "step_name", "log_source"):
            value = source.get(key)
            if isinstance(value, str) and value:
                fields[key] = value
    return fields


def _matches_query(entry: dict[str, Any], run_id: str, step: str | None, log_source: str | None) -> bool:
    fields = _query_fields(entry)
    if fields.get("run_id") != run_id:
        return False
    if step and fields.get("step_name") != step:
        return False
    if log_source and fields.get("log_source") != log_source:
        return False
    return True


def discover_runs(
    version: str,
    run_date: str,
    tz_name: str,
    limit: int,
    no_cache: bool,
) -> list[RunSummary]:
    if not no_cache:
        cached = load_runs_from_cache(version, run_date, tz_name)
        if cached:
            return [RunSummary.from_cache_dict(item) for item in cached]

    start_utc, end_utc = to_utc_window(run_date, tz_name)
    filter_expr = (
        f'timestamp>="{start_utc}" '
        f'AND timestamp<"{end_utc}" '
        f'AND (jsonPayload.log_source="cloud_function" OR textPayload:"log_source") '
        f'AND (jsonPayload.pipeline_version="{version}" OR textPayload:"pipeline_version") '
        f'AND (jsonPayload.run_id:* OR textPayload:"run_id")'
    )
    project_id = AigearConfig.get_config().gcp.gcp_project_id
    entries = read_logs(filter_expr=filter_expr, project_id=project_id, limit=limit)
    by_run_id: dict[str, RunSummary] = {}
    for entry in entries:
        payload = extract_log_fields(entry)
        if not payload:
            continue
        if payload.get("pipeline_version") not in (None, version):
            continue
        summary = _summary_from_payload(payload, version)
        if summary and summary.run_id not in by_run_id:
            by_run_id[summary.run_id] = summary

    summaries = sorted(by_run_id.values(), key=lambda item: item.run_started_at_utc, reverse=True)
    save_runs_to_cache(version, run_date, tz_name, [item.to_cache_dict() for item in summaries])
    return summaries


def query_logs_by_run_id(
    run_id: str,
    step: str | None,
    log_source: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = [
        f'(jsonPayload.run_id="{run_id}" OR labels.run_id="{run_id}" OR textPayload:"run_id")'
    ]
    if step:
        clauses.append(
            f'(jsonPayload.step_name="{step}" OR labels.step_name="{step}" OR textPayload:"step_name")'
        )
    if log_source:
        clauses.append(
            f'(jsonPayload.log_source="{log_source}" OR labels.log_source="{log_source}" OR textPayload:"log_source")'
        )
    filter_expr = " AND ".join(clauses)
    project_id = AigearConfig.get_config().gcp.gcp_project_id
    entries = read_logs(filter_expr=filter_expr, project_id=project_id, limit=limit)
    return [entry for entry in entries if _matches_query(entry, run_id, step, log_source)]

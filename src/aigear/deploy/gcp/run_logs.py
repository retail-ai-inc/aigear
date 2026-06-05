from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.deploy.gcp.logs_discovery_cache import load_logs_from_cache, save_logs_to_cache
from aigear.infrastructure.gcp.logging import read_logs

_RUN_ID_IN_TEXT = re.compile(r'"run_id"\s*:\s*"([^"]+)"')
_CF_LEGACY_FAIL_TEXT = re.compile(r"Pipeline step failed", re.I)

# Discovery scans log *entries* (not runs). Use a higher cap than query --limit.
DISCOVERY_LOG_LIMIT = 1000

_DISCOVER_FAILURE_EVENTS = (
    "vm_step_failed",
    "vm_creation_failed",
)


@dataclass
class RunSummary:
    run_id: str
    run_started_at_utc: str
    pipeline_version: str
    step_name: str
    project_name: str | None = None
    exit_code: str | None = None
    last_event: str | None = None

    def to_cache_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "run_id": self.run_id,
            "run_started_at_utc": self.run_started_at_utc,
            "pipeline_version": self.pipeline_version,
            "step_name": self.step_name,
            "project_name": self.project_name,
        }
        if self.exit_code:
            data["exit_code"] = self.exit_code
        if self.last_event:
            data["last_event"] = self.last_event
        return data

    @classmethod
    def from_cache_dict(cls, item: dict[str, Any]) -> RunSummary:
        return cls(
            run_id=item["run_id"],
            run_started_at_utc=item["run_started_at_utc"],
            pipeline_version=item["pipeline_version"],
            step_name=item.get("step_name") or "model_service",
            project_name=item.get("project_name"),
            exit_code=item.get("exit_code"),
            last_event=item.get("last_event"),
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


def _summary_from_payload(
    payload: dict[str, Any],
    version: str,
    *,
    entry_timestamp: str = "",
) -> RunSummary | None:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return None

    run_started_at_utc = payload.get("run_started_at_utc")
    if not isinstance(run_started_at_utc, str) or not run_started_at_utc:
        run_started_at_utc = entry_timestamp or ""

    if not run_started_at_utc:
        return None

    exit_code = payload.get("exit_code")
    event = payload.get("event")
    return RunSummary(
        run_id=run_id,
        run_started_at_utc=run_started_at_utc,
        pipeline_version=payload.get("pipeline_version", version),
        step_name=payload.get("step_name", "model_service"),
        project_name=payload.get("project_name"),
        exit_code=exit_code if isinstance(exit_code, str) else None,
        last_event=event if isinstance(event, str) else None,
    )


def _merge_run_summary(
    by_run_id: dict[str, RunSummary],
    summary: RunSummary,
) -> None:
    existing = by_run_id.get(summary.run_id)
    if existing is None:
        by_run_id[summary.run_id] = summary
        return
    if summary.run_started_at_utc >= existing.run_started_at_utc:
        winner, loser = summary, existing
    else:
        winner, loser = existing, summary
    by_run_id[summary.run_id] = replace(
        winner,
        exit_code=winner.exit_code or loser.exit_code,
        last_event=winner.last_event or loser.last_event,
    )


def _ingest_discover_entries(
    by_run_id: dict[str, RunSummary],
    entries: list[dict[str, Any]],
    version: str,
) -> None:
    for entry in entries:
        payload = extract_log_fields(entry)
        if not payload:
            continue
        if payload.get("pipeline_version") not in (None, version):
            continue
        timestamp = entry.get("timestamp", "")
        summary = _summary_from_payload(
            payload,
            version,
            entry_timestamp=timestamp if isinstance(timestamp, str) else "",
        )
        if summary:
            _merge_run_summary(by_run_id, summary)


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


def _matches_cloud_function_legacy(entry: dict[str, Any], run_id: str) -> bool:
    text_payload = entry.get("textPayload")
    if isinstance(text_payload, str):
        if run_id in text_payload and _CF_LEGACY_FAIL_TEXT.search(text_payload):
            return True

    resource = entry.get("resource")
    if isinstance(resource, dict) and resource.get("type") == "cloud_run_revision":
        fields = _query_fields(entry)
        if fields.get("run_id") == run_id:
            return True

    payload = extract_log_fields(entry) or {}
    if payload.get("exit_code") and payload.get("run_id") == run_id:
        return payload.get("log_source") in (None, "cloud_function")
    return False


def _matches_query(entry: dict[str, Any], run_id: str, step: str | None, log_source: str | None) -> bool:
    fields = _query_fields(entry)
    if fields.get("run_id") != run_id:
        if not (log_source == "cloud_function" and _matches_cloud_function_legacy(entry, run_id)):
            return False
    if step and fields.get("step_name") != step:
        return False
    if not log_source:
        return True
    if fields.get("log_source") == log_source:
        return True
    if log_source == "cloud_function" and _matches_cloud_function_legacy(entry, run_id):
        return True
    return False


def _discover_filter_base(start_utc: str, end_utc: str, version: str) -> str:
    return (
        f'timestamp>="{start_utc}" '
        f'AND timestamp<"{end_utc}" '
        f'AND (jsonPayload.pipeline_version="{version}" OR textPayload:"pipeline_version") '
        f'AND (jsonPayload.run_id:* OR textPayload:"run_id")'
    )


def discover_runs(
    version: str,
    run_date: str,
    tz_name: str,
    limit: int = DISCOVERY_LOG_LIMIT,
) -> list[RunSummary]:
    """Discover runs for a date/version. Always scans Cloud Logging (never uses cache)."""
    by_run_id: dict[str, RunSummary] = {}
    start_utc, end_utc = to_utc_window(run_date, tz_name)
    project_id = AigearConfig.get_config().gcp.gcp_project_id
    base = _discover_filter_base(start_utc, end_utc, version)

    initialized_filter = (
        f"{base} "
        f'AND (jsonPayload.log_source="cloud_function" OR textPayload:"log_source") '
        f'AND (jsonPayload.event="run_context_initialized" OR textPayload:"run_context_initialized")'
    )
    _ingest_discover_entries(
        by_run_id,
        read_logs(filter_expr=initialized_filter, project_id=project_id, limit=limit),
        version,
    )

    failure_events = " OR ".join(f'jsonPayload.event="{event}"' for event in _DISCOVER_FAILURE_EVENTS)
    failure_filter = (
        f"{base} AND ({failure_events} "
        f'OR (jsonPayload.event="pipeline_step_failed" AND jsonPayload.log_source="cloud_function"))'
    )
    _ingest_discover_entries(
        by_run_id,
        read_logs(filter_expr=failure_filter, project_id=project_id, limit=limit),
        version,
    )

    return sorted(by_run_id.values(), key=lambda item: item.run_started_at_utc, reverse=True)


def query_logs_by_run_id(
    run_id: str,
    step: str | None,
    log_source: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    cached = load_logs_from_cache(run_id, step, log_source, limit)
    if cached:
        return cached

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
    matched = [entry for entry in entries if _matches_query(entry, run_id, step, log_source)]
    save_logs_to_cache(run_id, step, log_source, limit, matched)
    return matched


@dataclass
class StepTimelineRow:
    step_name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    detail: str | None = None
    result: str | None = None


def _format_cf_fail_detail(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, str) and exit_code:
        parts.append(exit_code)
    docker_image = payload.get("docker_image")
    detail = payload.get("detail")
    if isinstance(docker_image, str) and docker_image:
        suffix = docker_image if not parts else f"({docker_image})"
        parts.append(suffix)
    elif isinstance(detail, str) and detail:
        suffix = detail if not parts else f"({detail})"
        parts.append(suffix)
    return " ".join(parts) if parts else "infrastructure failure"


def _format_result_value(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=True, sort_keys=True)
    return str(result)


def _format_duration_cell(duration_ms: int | None, started_at: str | None, finished_at: str | None) -> str:
    if isinstance(duration_ms, int):
        if duration_ms < 1000:
            return f"{duration_ms}ms"
        return f"{duration_ms // 1000}s"
    if started_at and finished_at:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            seconds = max(0, int((end - start).total_seconds()))
            return f"{seconds}s"
        except ValueError:
            pass
    return "—"


def _timeline_timestamp(entry: dict[str, Any]) -> str:
    timestamp = entry.get("timestamp", "")
    return timestamp if isinstance(timestamp, str) else ""


def build_step_timeline(
    entries: list[dict[str, Any]],
    *,
    step_filter: str | None = None,
) -> list[StepTimelineRow]:
    states: dict[str, StepTimelineRow] = {}

    for entry in sorted(entries, key=_timeline_timestamp):
        payload = extract_log_fields(entry) or {}
        step_name = payload.get("step_name")
        if not isinstance(step_name, str) or not step_name:
            continue
        if step_filter and step_name != step_filter:
            continue

        event = payload.get("event")
        if not isinstance(event, str) or not event:
            continue

        timestamp = _timeline_timestamp(entry)
        log_source = payload.get("log_source")
        row = states.get(step_name)
        if row is None:
            row = StepTimelineRow(step_name=step_name, status="PENDING")
            states[step_name] = row

        if event == "pipeline_step_started":
            if timestamp:
                row.started_at = timestamp
            row.status = "RUNNING"
        elif event == "pipeline_step_finished":
            if timestamp:
                row.finished_at = timestamp
            row.status = "OK"
            duration_ms = payload.get("duration_ms")
            if isinstance(duration_ms, int):
                row.duration_ms = duration_ms
        elif event == "pipeline_step_result":
            row.result = _format_result_value(payload.get("result"))
        elif event in ("pipeline_step_failed", "vm_step_failed", "vm_creation_failed"):
            if timestamp:
                row.finished_at = timestamp
            row.status = "FAILED"
            duration_ms = payload.get("duration_ms")
            if isinstance(duration_ms, int):
                row.duration_ms = duration_ms
            if event == "vm_creation_failed":
                row.detail = "vm_creation_failed"
            elif event == "vm_step_failed" or log_source == "cloud_function" or payload.get("exit_code"):
                row.detail = _format_cf_fail_detail(payload)
            else:
                error_message = payload.get("error_message")
                row.detail = str(error_message) if error_message is not None else "step failed"

    rows = list(states.values())
    if step_filter:
        rows = [row for row in rows if row.step_name == step_filter]
    rows.sort(key=lambda item: item.started_at or "9999")
    return rows


def format_step_timeline(
    rows: list[StepTimelineRow],
    *,
    run_id: str,
    show_hint: bool = True,
) -> list[str]:
    if not rows:
        return ["No step timeline entries found."]

    lines = [f"=== Step timeline (run_id={run_id}) ==="]
    header = (
        f"{'STEP':<15} {'STATUS':<8} {'STARTED (UTC)':<26} "
        f"{'FINISHED (UTC)':<26} {'DURATION':<9} DETAIL"
    )
    lines.append(header)

    for row in rows:
        started = row.started_at or "—"
        finished = row.finished_at or "—"
        duration = _format_duration_cell(row.duration_ms, row.started_at, row.finished_at)
        detail = row.detail or (row.result if row.status == "OK" else "—")
        lines.append(
            f"{row.step_name:<15} {row.status:<8} {started:<26} "
            f"{finished:<26} {duration:<9} {detail}"
        )

    if show_hint:
        lines.append("")
        lines.append("Hint: default shows all step logs; use --step <name> to filter one step.")
    return lines

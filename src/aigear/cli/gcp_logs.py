from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aigear.common import run_sh
from aigear.common.config import AigearConfig, PipelinesConfig

CACHE_TTL_SECONDS = 3 * 60 * 60
CACHE_FILE_PATH = Path.home() / ".aigear" / "cache" / "logs_discovery_cache.json"


@dataclass
class RunSummary:
    run_id: str
    run_started_at_utc: str
    pipeline_version: str
    step_name: str
    project_name: str | None = None


def _to_utc_window(run_date: str, tz_name: str) -> tuple[str, str]:
    local_start = datetime.strptime(run_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz_name))
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = local_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return start_utc, end_utc


def _load_cache() -> dict[str, Any]:
    if not CACHE_FILE_PATH.exists():
        return {"runs": {}, "indexes": {}}
    try:
        return json.loads(CACHE_FILE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"runs": {}, "indexes": {}}


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE_PATH.write_text(json.dumps(cache, ensure_ascii=True, indent=2), encoding="utf-8")


def clear_discovery_cache() -> None:
    if CACHE_FILE_PATH.exists():
        CACHE_FILE_PATH.unlink()
    print("Discovery cache cleared.")


def _prune_expired_cache(cache: dict[str, Any]) -> dict[str, Any]:
    now = int(datetime.now(timezone.utc).timestamp())
    runs = cache.get("runs", {})
    valid_run_ids = {
        run_id
        for run_id, item in runs.items()
        if isinstance(item, dict) and int(item.get("expires_at_epoch", 0)) > now
    }
    cache["runs"] = {run_id: runs[run_id] for run_id in valid_run_ids}
    indexes = {}
    for key, run_ids in cache.get("indexes", {}).items():
        kept = [run_id for run_id in run_ids if run_id in valid_run_ids]
        if kept:
            indexes[key] = kept
    cache["indexes"] = indexes
    return cache


def _cache_index_key(version: str, run_date: str, tz_name: str) -> str:
    return f"{version}|{run_date}|{tz_name}"


def _load_runs_from_cache(version: str, run_date: str, tz_name: str) -> list[RunSummary]:
    cache = _prune_expired_cache(_load_cache())
    key = _cache_index_key(version, run_date, tz_name)
    run_ids = cache.get("indexes", {}).get(key, [])
    runs = cache.get("runs", {})
    summaries: list[RunSummary] = []
    for run_id in run_ids:
        item = runs.get(run_id)
        if not item:
            continue
        summaries.append(
            RunSummary(
                run_id=item["run_id"],
                run_started_at_utc=item["run_started_at_utc"],
                pipeline_version=item["pipeline_version"],
                step_name=item.get("step_name") or "model_service",
                project_name=item.get("project_name"),
            )
        )
    _save_cache(cache)
    return summaries


def _save_runs_to_cache(version: str, run_date: str, tz_name: str, summaries: list[RunSummary]) -> None:
    cache = _prune_expired_cache(_load_cache())
    now = int(datetime.now(timezone.utc).timestamp())
    expires = now + CACHE_TTL_SECONDS
    for item in summaries:
        cache.setdefault("runs", {})[item.run_id] = {
            "run_id": item.run_id,
            "run_started_at_utc": item.run_started_at_utc,
            "pipeline_version": item.pipeline_version,
            "step_name": item.step_name,
            "project_name": item.project_name,
            "expires_at_epoch": expires,
        }
    key = _cache_index_key(version, run_date, tz_name)
    cache.setdefault("indexes", {})[key] = [item.run_id for item in summaries]
    _save_cache(cache)


def _read_gcp_logs(filter_expr: str, limit: int) -> list[dict[str, Any]]:
    project_id = AigearConfig.get_config().gcp.gcp_project_id
    output = run_sh(
        [
            "gcloud",
            "logging",
            "read",
            filter_expr,
            "--project",
            project_id,
            "--limit",
            str(limit),
            "--format=json",
        ]
    )
    if not output.strip():
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def discover_runs(version: str, run_date: str, tz_name: str, limit: int, no_cache: bool) -> list[RunSummary]:
    if not no_cache:
        cached = _load_runs_from_cache(version, run_date, tz_name)
        if cached:
            return cached

    start_utc, end_utc = _to_utc_window(run_date, tz_name)
    filter_expr = (
        f'timestamp>="{start_utc}" '
        f'AND timestamp<"{end_utc}" '
        f'AND jsonPayload.log_source="cloud_function" '
        f'AND jsonPayload.pipeline_version="{version}" '
        f"AND jsonPayload.run_id:*"
    )
    entries = _read_gcp_logs(filter_expr=filter_expr, limit=limit)
    by_run_id: dict[str, RunSummary] = {}
    for entry in entries:
        payload = entry.get("jsonPayload")
        if not isinstance(payload, dict):
            continue
        run_id = payload.get("run_id")
        run_started_at_utc = payload.get("run_started_at_utc")
        if not run_id or not run_started_at_utc:
            continue
        if run_id in by_run_id:
            continue
        by_run_id[run_id] = RunSummary(
            run_id=run_id,
            run_started_at_utc=run_started_at_utc,
            pipeline_version=payload.get("pipeline_version", version),
            step_name=payload.get("step_name", "model_service"),
            project_name=payload.get("project_name"),
        )
    summaries = sorted(by_run_id.values(), key=lambda item: item.run_started_at_utc, reverse=True)
    _save_runs_to_cache(version, run_date, tz_name, summaries)
    return summaries


def query_logs_by_run_id(run_id: str, step: str | None, log_source: str | None, limit: int) -> list[dict[str, Any]]:
    clauses = [f'jsonPayload.run_id="{run_id}"']
    if step:
        clauses.append(f'jsonPayload.step_name="{step}"')
    if log_source:
        clauses.append(f'jsonPayload.log_source="{log_source}"')
    filter_expr = " AND ".join(clauses)
    return _read_gcp_logs(filter_expr=filter_expr, limit=limit)


def _default_timezone(version: str) -> str:
    pipeline_cfg = PipelinesConfig.get_version_config(version)
    return pipeline_cfg.get("scheduler", {}).get("time_zone", "Etc/UTC")


def _select_run_interactively(runs: list[RunSummary]) -> RunSummary | None:
    print("Multiple runs found. Select one run_id:")
    for idx, item in enumerate(runs, start=1):
        print(
            f"{idx}. run_id={item.run_id} "
            f"run_started_at_utc={item.run_started_at_utc} "
            f"step={item.step_name}"
        )
    raw = input("Enter selection number: ").strip()
    try:
        selected_index = int(raw)
    except ValueError:
        print("Invalid selection.")
        return None
    if not (1 <= selected_index <= len(runs)):
        print("Selection out of range.")
        return None
    return runs[selected_index - 1]


def _print_logs(entries: list[dict[str, Any]]) -> None:
    if not entries:
        print("No logs found.")
        return
    for entry in entries:
        payload = entry.get("jsonPayload")
        timestamp = entry.get("timestamp", "")
        if isinstance(payload, dict):
            print(f"[{timestamp}] {json.dumps(payload, ensure_ascii=True)}")
            continue
        text_payload = entry.get("textPayload")
        if text_payload:
            print(f"[{timestamp}] {text_payload}")
            continue
        print(f"[{timestamp}] {json.dumps(entry, ensure_ascii=True)}")


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover and query GCP logs by run metadata.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", default="", help="Pipeline version. Required for discovery.")
    parser.add_argument("--run-date", default="", help="Run date in scheduler timezone (YYYY-MM-DD).")
    parser.add_argument("--run-id", default="", help="Directly query logs by run_id.")
    parser.add_argument("--step", default="", help="Optional step_name filter.")
    parser.add_argument(
        "--log-source",
        default="",
        choices=["", "cloud_function", "ml_pipeline"],
        help="Optional source filter.",
    )
    parser.add_argument("--time-zone", default="", help="Timezone for --run-date. Defaults to env.json scheduler.time_zone.")
    parser.add_argument("--limit", type=int, default=200, help="Max logs to return.")
    parser.add_argument("--no-cache", action="store_true", help="Skip discovery cache.")
    parser.add_argument("--clear-cache", action="store_true", help="Clear local discovery cache and exit.")
    return parser.parse_args()


def gcp_logs() -> None:
    args = get_argument()
    if args.clear_cache:
        clear_discovery_cache()
        return

    if args.run_id:
        entries = query_logs_by_run_id(
            run_id=args.run_id,
            step=args.step or None,
            log_source=args.log_source or None,
            limit=args.limit,
        )
        _print_logs(entries)
        return

    if not args.version:
        print("Missing required argument: --version")
        return
    if not args.run_date:
        print("Missing required argument: --run-date when --run-id is not provided")
        return

    tz_name = args.time_zone or _default_timezone(args.version)
    try:
        runs = discover_runs(
            version=args.version,
            run_date=args.run_date,
            tz_name=tz_name,
            limit=args.limit,
            no_cache=args.no_cache,
        )
    except ValueError:
        print("Invalid --run-date format. Expected YYYY-MM-DD.")
        return
    except ZoneInfoNotFoundError:
        print(f"Unknown timezone: {tz_name}")
        return

    if not runs:
        print("No runs discovered for the given date/version.")
        return

    selected: RunSummary | None
    if len(runs) == 1:
        selected = runs[0]
        print(f"Discovered one run: {selected.run_id}")
    else:
        if not sys.stdin.isatty():
            print("Multiple runs found. Re-run with --run-id in non-interactive mode.")
            return
        selected = _select_run_interactively(runs)
        if not selected:
            return

    entries = query_logs_by_run_id(
        run_id=selected.run_id,
        step=args.step or None,
        log_source=args.log_source or None,
        limit=args.limit,
    )
    _print_logs(entries)

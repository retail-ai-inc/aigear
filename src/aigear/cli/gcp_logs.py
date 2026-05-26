from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from aigear.deploy.gcp.logs_discovery_cache import clear_discovery_cache
from aigear.deploy.gcp.run_logs import (
    RunSummary,
    discover_runs,
    query_logs_by_run_id,
    scheduler_timezone,
)


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
    parser.add_argument(
        "--time-zone",
        default="",
        help="Timezone for --run-date. Defaults to env.json scheduler.time_zone.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Max logs to return.")
    parser.add_argument("--no-cache", action="store_true", help="Skip discovery cache.")
    parser.add_argument("--clear-cache", action="store_true", help="Clear local discovery cache and exit.")
    return parser.parse_args()


def gcp_logs() -> None:
    args = get_argument()
    if args.clear_cache:
        clear_discovery_cache()
        print("Discovery cache cleared.")
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

    tz_name = args.time_zone or scheduler_timezone(args.version)
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

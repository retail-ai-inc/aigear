from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_TTL_SECONDS = 3 * 60 * 60
CACHE_FILE_PATH = Path.home() / ".aigear" / "cache" / "logs_discovery_cache.json"


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


def load_runs_from_cache(version: str, run_date: str, tz_name: str) -> list[dict[str, Any]]:
    cache = _prune_expired_cache(_load_cache())
    key = _cache_index_key(version, run_date, tz_name)
    run_ids = cache.get("indexes", {}).get(key, [])
    runs = cache.get("runs", {})
    summaries: list[dict[str, Any]] = []
    for run_id in run_ids:
        item = runs.get(run_id)
        if isinstance(item, dict):
            summaries.append(item)
    _save_cache(cache)
    return summaries


def save_runs_to_cache(
    version: str,
    run_date: str,
    tz_name: str,
    summaries: list[dict[str, Any]],
) -> None:
    cache = _prune_expired_cache(_load_cache())
    now = int(datetime.now(timezone.utc).timestamp())
    expires = now + CACHE_TTL_SECONDS
    for item in summaries:
        run_id = item.get("run_id")
        if not run_id:
            continue
        cache.setdefault("runs", {})[run_id] = {**item, "expires_at_epoch": expires}
    key = _cache_index_key(version, run_date, tz_name)
    cache.setdefault("indexes", {})[key] = [
        item["run_id"] for item in summaries if item.get("run_id")
    ]
    _save_cache(cache)

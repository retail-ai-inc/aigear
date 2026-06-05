import json
from pathlib import Path

from aigear.deploy.gcp import logs_discovery_cache


def test_cache_roundtrip(tmp_path, monkeypatch):
    cache_path = Path(tmp_path) / "cache.json"
    monkeypatch.setattr(logs_discovery_cache, "CACHE_FILE_PATH", cache_path)
    sample = [
        {
            "run_id": "abc123",
            "run_started_at_utc": "2026-05-21T00:00:00Z",
            "pipeline_version": "v1",
            "step_name": "fetch_data",
            "project_name": "demo",
        }
    ]
    logs_discovery_cache.save_runs_to_cache("v1", "2026-05-21", "Etc/UTC", sample)
    loaded = logs_discovery_cache.load_runs_from_cache("v1", "2026-05-21", "Etc/UTC")
    assert len(loaded) == 1
    assert loaded[0]["run_id"] == "abc123"


def test_log_query_cache_skips_empty_results(tmp_path, monkeypatch):
    cache_path = Path(tmp_path) / "cache.json"
    monkeypatch.setattr(logs_discovery_cache, "CACHE_FILE_PATH", cache_path)
    logs_discovery_cache.save_logs_to_cache("abc123", None, None, 50, [])
    assert logs_discovery_cache.load_logs_from_cache("abc123", None, None, 50) is None
    if cache_path.exists():
        assert json.loads(cache_path.read_text(encoding="utf-8")).get("queries", {}) == {}


def test_log_query_cache_treats_legacy_empty_entries_as_miss(tmp_path, monkeypatch):
    cache_path = Path(tmp_path) / "cache.json"
    monkeypatch.setattr(logs_discovery_cache, "CACHE_FILE_PATH", cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        '{"runs": {}, "indexes": {}, "queries": {"abc123|||50": {"entries": [], "expires_at_epoch": 9999999999}}}',
        encoding="utf-8",
    )
    assert logs_discovery_cache.load_logs_from_cache("abc123", None, None, 50) is None
    assert json.loads(cache_path.read_text(encoding="utf-8")).get("queries", {}) == {}


def test_log_query_cache_roundtrip(tmp_path, monkeypatch):
    cache_path = Path(tmp_path) / "cache.json"
    monkeypatch.setattr(logs_discovery_cache, "CACHE_FILE_PATH", cache_path)
    entries = [{"jsonPayload": {"run_id": "abc123", "event": "vm_created"}}]
    logs_discovery_cache.save_logs_to_cache("abc123", None, None, 50, entries)
    loaded = logs_discovery_cache.load_logs_from_cache("abc123", None, None, 50)
    assert loaded == entries
    assert logs_discovery_cache.load_logs_from_cache("abc123", "fetch", None, 50) is None

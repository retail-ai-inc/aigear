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

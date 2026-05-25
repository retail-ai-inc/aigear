from pathlib import Path

from aigear.cli import gcp_logs


def test_to_utc_window_uses_timezone():
    start_utc, end_utc = gcp_logs._to_utc_window("2026-05-21", "Asia/Tokyo")
    assert start_utc == "2026-05-20T15:00:00Z"
    assert end_utc == "2026-05-21T15:00:00Z"


def test_cache_roundtrip(tmp_path, monkeypatch):
    cache_path = Path(tmp_path) / "cache.json"
    monkeypatch.setattr(gcp_logs, "CACHE_FILE_PATH", cache_path)
    sample = [
        gcp_logs.RunSummary(
            run_id="abc123",
            run_started_at_utc="2026-05-21T00:00:00Z",
            pipeline_version="v1",
            step_name="fetch_data",
            project_name="demo",
        )
    ]
    gcp_logs._save_runs_to_cache("v1", "2026-05-21", "Etc/UTC", sample)
    loaded = gcp_logs._load_runs_from_cache("v1", "2026-05-21", "Etc/UTC")
    assert len(loaded) == 1
    assert loaded[0].run_id == "abc123"

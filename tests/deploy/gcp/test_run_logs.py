from aigear.deploy.gcp.run_logs import RunSummary, to_utc_window


def test_to_utc_window_uses_timezone():
    start_utc, end_utc = to_utc_window("2026-05-21", "Asia/Tokyo")
    assert start_utc == "2026-05-20T15:00:00Z"
    assert end_utc == "2026-05-21T15:00:00Z"


def test_run_summary_cache_roundtrip():
    summary = RunSummary(
        run_id="abc123",
        run_started_at_utc="2026-05-21T00:00:00Z",
        pipeline_version="v1",
        step_name="fetch_data",
        project_name="demo",
    )
    restored = RunSummary.from_cache_dict(summary.to_cache_dict())
    assert restored == summary

from types import SimpleNamespace

from aigear.deploy.gcp import run_logs
from aigear.deploy.gcp.run_logs import RunSummary, query_logs_by_run_id, to_utc_window


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


def test_query_logs_by_run_id_supports_labels_run_id(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    monkeypatch.setattr(
        run_logs,
        "read_logs",
        lambda **kwargs: [
            {"labels": {"run_id": "target-run"}, "textPayload": "plain"},
            {"labels": {"run_id": "other-run"}, "textPayload": "plain"},
        ],
    )

    entries = query_logs_by_run_id(run_id="target-run", step=None, log_source=None, limit=50)
    assert entries == [{"labels": {"run_id": "target-run"}, "textPayload": "plain"}]


def test_query_logs_by_run_id_filters_text_payload_json_fallback(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    monkeypatch.setattr(
        run_logs,
        "read_logs",
        lambda **kwargs: [
            {"textPayload": '{"run_id":"target-run","step_name":"fetch"}'},
            {"textPayload": '{"run_id":"other-run","step_name":"fetch"}'},
            {"textPayload": '{"run_id":"target-run","step_name":"train"}'},
        ],
    )

    entries = query_logs_by_run_id(run_id="target-run", step="fetch", log_source=None, limit=50)
    assert entries == [{"textPayload": '{"run_id":"target-run","step_name":"fetch"}'}]

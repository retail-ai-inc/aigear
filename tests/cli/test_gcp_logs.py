from __future__ import annotations

from aigear.cli import gcp_logs as gcp_logs_cli
from aigear.deploy.gcp.run_logs import RunSummary


def test_get_argument_accepts_all_log_source(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--version",
            "v1",
            "--run-date",
            "2026-05-21",
            "--log-source",
            "all",
        ],
    )

    args = gcp_logs_cli.get_argument()

    assert args.log_source == "all"


def test_gcp_logs_with_run_id_all_queries_both_sources(monkeypatch, capsys):
    calls: list[str | None] = []

    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        calls.append(log_source)
        return [
            {
                "timestamp": "2026-05-21T00:00:00Z",
                "jsonPayload": {"run_id": run_id, "log_source": log_source or "unknown"},
            }
        ]

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--run-id",
            "run-123",
            "--log-source",
            "all",
            "--limit",
            "3",
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert calls == ["cloud_function", "ml_pipeline"]
    assert "cloud_function logs" in output
    assert "ml_pipeline logs" in output


def test_gcp_logs_discover_path_all_queries_both_sources(monkeypatch, capsys):
    calls: list[str | None] = []

    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        calls.append(log_source)
        return []

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--version",
            "v1",
            "--run-date",
            "2026-05-21",
            "--log-source",
            "all",
        ],
    )
    monkeypatch.setattr(
        gcp_logs_cli,
        "discover_runs",
        lambda **kwargs: [
            RunSummary(
                run_id="run-456",
                run_started_at_utc="2026-05-21T00:00:00Z",
                pipeline_version="v1",
                step_name="model_service",
            )
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "scheduler_timezone", lambda version: "Etc/UTC")
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert calls == ["cloud_function", "ml_pipeline"]
    assert "Discovered one run: run-456" in output


def test_gcp_logs_with_run_id_single_source_queries_once(monkeypatch, capsys):
    calls: list[str | None] = []

    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        calls.append(log_source)
        return [
            {
                "timestamp": "2026-05-21T00:00:00Z",
                "jsonPayload": {"run_id": run_id, "log_source": log_source or "unknown"},
            }
        ]

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--run-id",
            "run-789",
            "--log-source",
            "cloud_function",
            "--limit",
            "5",
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert calls == ["cloud_function"]
    assert "ml_pipeline logs" not in output

from __future__ import annotations

from aigear.cli import gcp_logs as gcp_logs_cli
from aigear.deploy.gcp.run_logs import RunSummary


def test_get_argument_step_defaults_to_all(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["aigear-logs", "--run-id", "run-123"],
    )

    args = gcp_logs_cli.get_argument()

    assert args.step == "all"
    assert args.format == "full"


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


def test_gcp_logs_default_queries_all_steps(monkeypatch, capsys):
    captured: list[str | None] = []

    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        captured.append(step)
        return []

    monkeypatch.setattr(
        "sys.argv",
        ["aigear-logs", "--run-id", "run-default", "--log-source", "ml_pipeline"],
    )
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    assert captured == [None]


def test_gcp_logs_step_all_queries_all_steps(monkeypatch, capsys):
    captured: list[str | None] = []

    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        captured.append(step)
        return []

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--run-id",
            "run-all",
            "--step",
            "all",
            "--log-source",
            "ml_pipeline",
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    assert captured == [None]


def test_gcp_logs_step_training_filters_single_step(monkeypatch, capsys):
    captured: list[str | None] = []

    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        captured.append(step)
        return []

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--run-id",
            "run-training",
            "--step",
            "training",
            "--log-source",
            "ml_pipeline",
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    assert captured == ["training"]


def test_gcp_logs_format_concise_shows_timeline(monkeypatch, capsys):
    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        if log_source == "cloud_function":
            return [
                {
                    "timestamp": "2026-06-03T06:40:00Z",
                    "jsonPayload": {
                        "run_id": run_id,
                        "log_source": "cloud_function",
                        "event": "vm_step_failed",
                        "step_name": "model_service",
                        "exit_code": "docker_image_not_found",
                        "docker_image": "demo:latest",
                    },
                }
            ]
        return [
            {
                "timestamp": "2026-06-03T06:33:06Z",
                "jsonPayload": {
                    "run_id": run_id,
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_started",
                    "step_name": "fetch_data",
                },
            },
            {
                "timestamp": "2026-06-03T06:33:11Z",
                "jsonPayload": {
                    "run_id": run_id,
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_finished",
                    "step_name": "fetch_data",
                    "duration_ms": 5000,
                },
            },
        ]

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--run-id",
            "run-timeline",
            "--format",
            "concise",
            "--log-source",
            "all",
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert "=== Step timeline (run_id=run-timeline) ===" in output
    assert "fetch_data" in output
    assert "OK" in output
    assert "model_service" in output
    assert "FAILED" in output
    assert "docker_image_not_found" in output
    assert "Hint: default shows all step logs" in output


def test_gcp_logs_format_concise_single_step_hides_hint(monkeypatch, capsys):
    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        return [
            {
                "timestamp": "2026-06-03T06:36:11Z",
                "jsonPayload": {
                    "run_id": run_id,
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_started",
                    "step_name": "training",
                },
            },
            {
                "timestamp": "2026-06-03T06:36:16Z",
                "jsonPayload": {
                    "run_id": run_id,
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_finished",
                    "step_name": "training",
                    "duration_ms": 5000,
                },
            },
        ]

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--run-id",
            "run-one-step",
            "--format",
            "concise",
            "--step",
            "training",
            "--log-source",
            "ml_pipeline",
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert "training" in output
    assert "OK" in output
    assert "Hint: default shows all step logs" not in output


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


def test_gcp_logs_interactive_discover_shows_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--version",
            "v1",
            "--run-date",
            "2026-06-03",
        ],
    )
    monkeypatch.setattr(
        gcp_logs_cli,
        "discover_runs",
        lambda **kwargs: [
            RunSummary(
                run_id="run-a",
                run_started_at_utc="2026-06-03T01:00:00Z",
                pipeline_version="v1",
                step_name="fetch_data",
                exit_code="docker_image_not_found",
                last_event="vm_step_failed",
            ),
            RunSummary(
                run_id="run-b",
                run_started_at_utc="2026-06-03T02:00:00Z",
                pipeline_version="v1",
                step_name="fetch_data",
            ),
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "scheduler_timezone", lambda version: "Etc/UTC")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", lambda **kwargs: [])

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert "exit_code=docker_image_not_found" in output
    assert "event=vm_step_failed" in output


def test_gcp_logs_format_full_prints_json_logs(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--run-id",
            "run-json",
            "--log-source",
            "ml_pipeline",
        ],
    )
    monkeypatch.setattr(
        gcp_logs_cli,
        "query_logs_by_run_id",
        lambda **kwargs: [
            {
                "timestamp": "2026-06-03T06:33:06Z",
                "jsonPayload": {
                    "run_id": "run-json",
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_started",
                    "step_name": "fetch_data",
                },
            }
        ],
    )

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert "pipeline_step_started" in output
    assert "fetch_data" in output
    assert "=== Step timeline" not in output


def test_gcp_logs_discover_path_format_concise_shows_timeline(monkeypatch, capsys):
    def _fake_query(run_id: str, step: str | None, log_source: str | None, limit: int):
        return [
            {
                "timestamp": "2026-06-03T06:36:16Z",
                "jsonPayload": {
                    "run_id": run_id,
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_finished",
                    "step_name": "training",
                    "duration_ms": 5000,
                },
            },
            {
                "timestamp": "2026-06-03T06:36:11Z",
                "jsonPayload": {
                    "run_id": run_id,
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_started",
                    "step_name": "training",
                },
            },
        ]

    monkeypatch.setattr(
        "sys.argv",
        [
            "aigear-logs",
            "--version",
            "v1",
            "--run-date",
            "2026-06-03",
            "--format",
            "concise",
            "--log-source",
            "ml_pipeline",
        ],
    )
    monkeypatch.setattr(
        gcp_logs_cli,
        "discover_runs",
        lambda **kwargs: [
            RunSummary(
                run_id="run-discover",
                run_started_at_utc="2026-06-03T01:00:00Z",
                pipeline_version="v1",
                step_name="fetch_data",
            )
        ],
    )
    monkeypatch.setattr(gcp_logs_cli, "scheduler_timezone", lambda version: "Etc/UTC")
    monkeypatch.setattr(gcp_logs_cli, "query_logs_by_run_id", _fake_query)

    gcp_logs_cli.gcp_logs()

    output = capsys.readouterr().out
    assert "Discovered one run: run-discover" in output
    assert "=== Step timeline (run_id=run-discover) ===" in output
    assert "training" in output
    assert "OK" in output


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

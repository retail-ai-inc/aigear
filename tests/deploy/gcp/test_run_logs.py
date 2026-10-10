from types import SimpleNamespace

from aigear.deploy.gcp import run_logs
from aigear.deploy.gcp.run_logs import (
    RunSummary,
    _matches_query,
    build_step_timeline,
    discover_runs,
    format_step_timeline,
    query_logs_by_run_id,
    to_utc_window,
)


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
        exit_code="docker_image_not_found",
        last_event="vm_step_failed",
    )
    restored = RunSummary.from_cache_dict(summary.to_cache_dict())
    assert restored == summary


def test_run_summary_does_not_fabricate_missing_step_name():
    restored = RunSummary.from_cache_dict(
        {
            "run_id": "abc123",
            "run_started_at_utc": "2026-05-21T00:00:00Z",
            "pipeline_version": "v1",
        }
    )

    assert restored.step_name is None


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


def test_discover_runs_dedupes_multiple_run_ids(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    captured: dict[str, int] = {}

    def _fake_read_logs(filter_expr: str, project_id: str, limit: int):
        captured["limit"] = limit
        if "vm_step_failed" in filter_expr or "vm_creation_failed" in filter_expr:
            return []
        assert "run_context_initialized" in filter_expr
        return [
            {
                "jsonPayload": {
                    "run_id": "run-a",
                    "run_started_at_utc": "2026-06-03T01:00:00.000Z",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                }
            },
            {
                "jsonPayload": {
                    "run_id": "run-b",
                    "run_started_at_utc": "2026-06-03T02:00:00.000Z",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                }
            },
            {
                "jsonPayload": {
                    "run_id": "run-a",
                    "run_started_at_utc": "2026-06-03T01:00:00.000Z",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                }
            },
        ]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)

    runs = discover_runs("v1", "2026-06-03", "Etc/UTC", limit=100)

    assert captured["limit"] == 100
    assert [item.run_id for item in runs] == ["run-b", "run-a"]


def test_discover_runs_ignores_discovery_cache(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    monkeypatch.setattr(
        run_logs,
        "load_logs_from_cache",
        lambda *args, **kwargs: [{"textPayload": "stale cached log"}],
    )
    def _fake_read_logs(**kwargs):
        filter_expr = kwargs.get("filter_expr", "")
        if "vm_step_failed" in filter_expr:
            return []
        return [
            {
                "jsonPayload": {
                    "run_id": "fresh-run",
                    "run_started_at_utc": "2026-06-03T03:00:00.000Z",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                }
            }
        ]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)

    runs = discover_runs("v1", "2026-06-03", "Etc/UTC", limit=50)

    assert [item.run_id for item in runs] == ["fresh-run"]


def test_discover_runs_keeps_missing_step_name_empty(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )

    def _fake_read_logs(filter_expr: str, project_id: str, limit: int):
        if "vm_step_failed" in filter_expr:
            return []
        return [
            {
                "jsonPayload": {
                    "run_id": "no-step-run",
                    "run_started_at_utc": "2026-06-03T03:00:00.000Z",
                    "pipeline_version": "v1",
                }
            }
        ]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)

    runs = discover_runs("v1", "2026-06-03", "Etc/UTC", limit=50)

    assert len(runs) == 1
    assert runs[0].run_id == "no-step-run"
    assert runs[0].step_name is None


def test_query_logs_by_run_id_uses_cache_before_gcp(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    calls: list[str] = []

    def _fake_read_logs(**kwargs):
        calls.append("gcp")
        return [{"labels": {"run_id": "target-run"}, "textPayload": "from-gcp"}]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)
    monkeypatch.setattr(
        run_logs,
        "load_logs_from_cache",
        lambda *args, **kwargs: [{"labels": {"run_id": "target-run"}, "textPayload": "from-cache"}],
    )
    monkeypatch.setattr(run_logs, "save_logs_to_cache", lambda *args, **kwargs: None)

    entries = query_logs_by_run_id(run_id="target-run", step=None, log_source=None, limit=50)
    assert entries == [{"labels": {"run_id": "target-run"}, "textPayload": "from-cache"}]
    assert calls == []


def test_query_logs_by_run_id_does_not_cache_empty_gcp_results(tmp_path, monkeypatch):
    import json

    from aigear.deploy.gcp import logs_discovery_cache

    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(logs_discovery_cache, "CACHE_FILE_PATH", cache_path)
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    monkeypatch.setattr(run_logs, "read_logs", lambda **kwargs: [])

    entries = query_logs_by_run_id(run_id="target-run", step=None, log_source=None, limit=50)
    assert entries == []
    if cache_path.exists():
        assert json.loads(cache_path.read_text(encoding="utf-8")).get("queries", {}) == {}


def test_query_logs_by_run_id_empty_cache_hits_gcp(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    calls: list[str] = []

    def _fake_read_logs(**kwargs):
        calls.append("gcp")
        return [{"labels": {"run_id": "target-run"}, "textPayload": "from-gcp"}]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)
    monkeypatch.setattr(run_logs, "load_logs_from_cache", lambda *args, **kwargs: [])
    monkeypatch.setattr(run_logs, "save_logs_to_cache", lambda *args, **kwargs: None)

    entries = query_logs_by_run_id(run_id="target-run", step=None, log_source=None, limit=50)
    assert entries == [{"labels": {"run_id": "target-run"}, "textPayload": "from-gcp"}]
    assert calls == ["gcp"]


def test_discover_runs_from_legacy_cf_pipeline_step_failed(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )

    def _fake_read_logs(filter_expr: str, project_id: str, limit: int):
        if "run_context_initialized" in filter_expr:
            return []
        assert "pipeline_step_failed" in filter_expr
        assert 'jsonPayload.log_source="cloud_function"' in filter_expr
        return [
            {
                "timestamp": "2026-06-03T05:00:00.000Z",
                "jsonPayload": {
                    "log_source": "cloud_function",
                    "event": "pipeline_step_failed",
                    "run_id": "legacy-failed-run",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                    "exit_code": "registry_auth_failed",
                },
            }
        ]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)

    runs = discover_runs("v1", "2026-06-03", "Etc/UTC", limit=50)

    assert len(runs) == 1
    assert runs[0].run_id == "legacy-failed-run"
    assert runs[0].exit_code == "registry_auth_failed"
    assert runs[0].last_event == "pipeline_step_failed"
    assert runs[0].run_started_at_utc == "2026-06-03T05:00:00.000Z"


def test_discover_runs_from_vm_creation_failed(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )

    def _fake_read_logs(filter_expr: str, project_id: str, limit: int):
        if "run_context_initialized" in filter_expr:
            return []
        assert "vm_creation_failed" in filter_expr
        return [
            {
                "timestamp": "2026-06-03T06:00:00.000Z",
                "jsonPayload": {
                    "log_source": "cloud_function",
                    "event": "vm_creation_failed",
                    "run_id": "vm-create-failed-run",
                    "run_started_at_utc": "2026-06-03T05:30:00.000Z",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                },
            }
        ]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)

    runs = discover_runs("v1", "2026-06-03", "Etc/UTC", limit=50)

    assert len(runs) == 1
    assert runs[0].run_id == "vm-create-failed-run"
    assert runs[0].last_event == "vm_creation_failed"


def test_discover_runs_merges_initialized_and_failure_for_same_run(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )

    def _fake_read_logs(filter_expr: str, project_id: str, limit: int):
        if "run_context_initialized" in filter_expr:
            return [
                {
                    "jsonPayload": {
                        "log_source": "cloud_function",
                        "event": "run_context_initialized",
                        "run_id": "same-run",
                        "run_started_at_utc": "2026-06-03T01:00:00.000Z",
                        "pipeline_version": "v1",
                        "step_name": "fetch_data",
                    }
                }
            ]
        return [
            {
                "timestamp": "2026-06-03T02:00:00.000Z",
                "jsonPayload": {
                    "log_source": "cloud_function",
                    "event": "vm_step_failed",
                    "run_id": "same-run",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                    "exit_code": "docker_image_not_found",
                },
            }
        ]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)

    runs = discover_runs("v1", "2026-06-03", "Etc/UTC", limit=50)

    assert len(runs) == 1
    assert runs[0].run_id == "same-run"
    assert runs[0].run_started_at_utc == "2026-06-03T02:00:00.000Z"
    assert runs[0].exit_code == "docker_image_not_found"
    assert runs[0].last_event == "vm_step_failed"


def test_discover_runs_from_vm_step_failed(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )

    def _fake_read_logs(filter_expr: str, project_id: str, limit: int):
        if "run_context_initialized" in filter_expr:
            return []
        return [
            {
                "timestamp": "2026-06-03T04:00:00.000Z",
                "jsonPayload": {
                    "log_source": "cloud_function",
                    "event": "vm_step_failed",
                    "run_id": "failed-run",
                    "pipeline_version": "v1",
                    "step_name": "fetch_data",
                    "exit_code": "docker_image_not_found",
                },
            }
        ]

    monkeypatch.setattr(run_logs, "read_logs", _fake_read_logs)

    runs = discover_runs("v1", "2026-06-03", "Etc/UTC", limit=50)

    assert len(runs) == 1
    assert runs[0].run_id == "failed-run"
    assert runs[0].exit_code == "docker_image_not_found"
    assert runs[0].last_event == "vm_step_failed"


def test_query_logs_cloud_function_legacy_text(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    monkeypatch.setattr(
        run_logs,
        "read_logs",
        lambda **kwargs: [
            {
                "resource": {"type": "cloud_run_revision"},
                "textPayload": 'Pipeline step failed with exit code: docker_image_not_found, run_id: target-run',
            },
            {"textPayload": "unrelated"},
        ],
    )
    monkeypatch.setattr(run_logs, "load_logs_from_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_logs, "save_logs_to_cache", lambda *args, **kwargs: None)

    entries = query_logs_by_run_id(
        run_id="target-run",
        step=None,
        log_source="cloud_function",
        limit=50,
    )
    assert len(entries) == 1


def test_matches_query_cloud_function_legacy_text_payload():
    entry = {
        "textPayload": (
            "Pipeline step failed with exit code: docker_image_not_found, run_id: target-run"
        ),
    }
    assert _matches_query(entry, "target-run", None, "cloud_function")
    assert not _matches_query(entry, "other-run", None, "cloud_function")


def test_matches_query_cloud_function_text_json_exit_code():
    entry = {
        "textPayload": '{"run_id":"target-run","exit_code":"docker_image_not_found"}',
    }
    assert _matches_query(entry, "target-run", None, "cloud_function")


def test_matches_query_cloud_function_text_json_run_id_on_cf_resource():
    entry = {
        "resource": {"type": "cloud_run_revision"},
        "textPayload": 'log line prefix {"run_id": "target-run"} suffix',
    }
    assert _matches_query(entry, "target-run", None, "cloud_function")


def test_matches_query_cloud_function_excludes_ml_pipeline():
    entry = {
        "jsonPayload": {
            "run_id": "target-run",
            "log_source": "ml_pipeline",
            "event": "pipeline_step_failed",
        },
    }
    assert not _matches_query(entry, "target-run", None, "cloud_function")


def test_query_logs_cloud_function_text_json_fragment(monkeypatch):
    monkeypatch.setattr(
        run_logs.AigearConfig,
        "get_config",
        lambda: SimpleNamespace(gcp=SimpleNamespace(gcp_project_id="demo-project")),
    )
    monkeypatch.setattr(
        run_logs,
        "read_logs",
        lambda **kwargs: [
            {
                "resource": {"type": "cloud_run_revision"},
                "textPayload": 'stderr {"run_id": "target-run", "exit_code": "registry_auth_failed"}',
            },
            {"textPayload": '{"run_id": "other-run"}'},
        ],
    )
    monkeypatch.setattr(run_logs, "load_logs_from_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_logs, "save_logs_to_cache", lambda *args, **kwargs: None)

    entries = query_logs_by_run_id(
        run_id="target-run",
        step=None,
        log_source="cloud_function",
        limit=50,
    )
    assert len(entries) == 1
    assert "target-run" in entries[0]["textPayload"]


def _entry(timestamp: str, payload: dict) -> dict:
    return {"timestamp": timestamp, "jsonPayload": payload}


def test_build_step_timeline_successful_pipeline():
    entries = [
        _entry(
            "2026-06-03T06:33:06Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "fetch_data",
            },
        ),
        _entry(
            "2026-06-03T06:33:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "fetch_data",
                "duration_ms": 5000,
            },
        ),
        _entry(
            "2026-06-03T06:34:39Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "training",
            },
        ),
        _entry(
            "2026-06-03T06:34:43Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "training",
                "duration_ms": 4000,
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert [row.step_name for row in rows] == ["fetch_data", "training"]
    assert rows[0].status == "OK"
    assert rows[0].started_at == "2026-06-03T06:33:06Z"
    assert rows[0].finished_at == "2026-06-03T06:33:11Z"
    assert rows[0].duration_ms == 5000
    assert rows[1].status == "OK"
    assert rows[1].duration_ms == 4000


def test_build_step_timeline_vm_step_failed_without_start():
    entries = [
        _entry(
            "2026-06-03T06:40:00Z",
            {
                "log_source": "cloud_function",
                "event": "vm_step_failed",
                "step_name": "model_service",
                "exit_code": "docker_image_not_found",
                "docker_image": "demo:latest",
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert len(rows) == 1
    assert rows[0].step_name == "model_service"
    assert rows[0].status == "FAILED"
    assert rows[0].started_at is None
    assert rows[0].finished_at == "2026-06-03T06:40:00Z"
    assert rows[0].detail == "docker_image_not_found (demo:latest)"


def test_build_step_timeline_model_service_completed_from_cloud_function():
    entries = [
        _entry(
            "2026-06-03T06:40:00Z",
            {
                "log_source": "cloud_function",
                "event": "run_context_initialized",
                "run_id": "run-abc",
                "step_name": "model_service",
            },
        ),
        _entry(
            "2026-06-03T06:40:30Z",
            {
                "log_source": "cloud_function",
                "event": "pipeline_completed",
                "run_id": "run-abc",
                "step_name": "model_service",
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert len(rows) == 1
    assert rows[0].step_name == "model_service"
    assert rows[0].status == "OK"
    assert rows[0].finished_at == "2026-06-03T06:40:30Z"


def test_build_step_timeline_legacy_cf_pipeline_step_failed():
    entries = [
        _entry(
            "2026-06-03T05:00:00Z",
            {
                "log_source": "cloud_function",
                "event": "pipeline_step_failed",
                "step_name": "fetch_data",
                "exit_code": "registry_auth_failed",
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert len(rows) == 1
    assert rows[0].status == "FAILED"
    assert rows[0].detail == "registry_auth_failed"


def test_build_step_timeline_ml_pipeline_step_failed():
    entries = [
        _entry(
            "2026-06-03T06:36:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "training",
            },
        ),
        _entry(
            "2026-06-03T06:36:16Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_failed",
                "step_name": "training",
                "error_message": "ValueError: bad input",
                "duration_ms": 5000,
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert len(rows) == 1
    assert rows[0].status == "FAILED"
    assert rows[0].started_at == "2026-06-03T06:36:11Z"
    assert rows[0].finished_at == "2026-06-03T06:36:16Z"
    assert rows[0].duration_ms == 5000
    assert rows[0].detail == "ValueError: bad input"


def test_build_step_timeline_vm_creation_failed():
    entries = [
        _entry(
            "2026-06-03T06:00:00Z",
            {
                "log_source": "cloud_function",
                "event": "vm_creation_failed",
                "step_name": "fetch_data",
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert len(rows) == 1
    assert rows[0].status == "FAILED"
    assert rows[0].detail == "vm_creation_failed"
    assert rows[0].finished_at == "2026-06-03T06:00:00Z"


def test_build_step_timeline_incomplete_running_step():
    entries = [
        _entry(
            "2026-06-03T06:36:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "preprocessing",
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert len(rows) == 1
    assert rows[0].status == "RUNNING"
    assert rows[0].started_at == "2026-06-03T06:36:11Z"
    assert rows[0].finished_at is None


def test_build_step_timeline_step_filter():
    entries = [
        _entry(
            "2026-06-03T06:33:06Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "fetch_data",
            },
        ),
        _entry(
            "2026-06-03T06:36:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "training",
            },
        ),
        _entry(
            "2026-06-03T06:36:16Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "training",
                "duration_ms": 5000,
            },
        ),
    ]

    rows = build_step_timeline(entries, step_filter="training")

    assert len(rows) == 1
    assert rows[0].step_name == "training"
    assert rows[0].status == "OK"


def test_build_step_timeline_shows_result_for_ok_step():
    entries = [
        _entry(
            "2026-06-03T06:36:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "training",
            },
        ),
        _entry(
            "2026-06-03T06:36:16Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "training",
                "duration_ms": 5000,
            },
        ),
        _entry(
            "2026-06-03T06:36:16Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_result",
                "step_name": "training",
                "result": {"accuracy": 0.92, "rows": 1000},
            },
        ),
    ]

    rows = build_step_timeline(entries)
    lines = format_step_timeline(rows, run_id="run-abc", show_hint=False)

    assert len(rows) == 1
    assert rows[0].result == '{"accuracy": 0.92, "rows": 1000}'
    assert "accuracy" in lines[2]
    assert "0.92" in lines[2]


def test_format_step_timeline_empty():
    lines = format_step_timeline([], run_id="run-empty")
    assert lines == ["No step timeline entries found."]


def test_build_step_timeline_full_pipeline_with_infra_failure():
    """Plan scenario: 3 OK steps + model_service infra failure."""
    entries = [
        _entry(
            "2026-06-03T06:33:06Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "fetch_data",
            },
        ),
        _entry(
            "2026-06-03T06:33:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "fetch_data",
                "duration_ms": 5000,
            },
        ),
        _entry(
            "2026-06-03T06:34:39Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "preprocessing",
            },
        ),
        _entry(
            "2026-06-03T06:34:43Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "preprocessing",
                "duration_ms": 4000,
            },
        ),
        _entry(
            "2026-06-03T06:36:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "training",
            },
        ),
        _entry(
            "2026-06-03T06:36:16Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "training",
                "duration_ms": 5000,
            },
        ),
        _entry(
            "2026-06-03T06:40:00Z",
            {
                "log_source": "cloud_function",
                "event": "vm_step_failed",
                "step_name": "model_service",
                "exit_code": "docker_image_not_found",
                "docker_image": "demo:latest",
            },
        ),
    ]

    rows = build_step_timeline(entries)
    lines = format_step_timeline(rows, run_id="26fe3e695ca7d8c6", show_hint=True)

    assert [row.step_name for row in rows] == [
        "fetch_data",
        "preprocessing",
        "training",
        "model_service",
    ]
    assert rows[0].status == "OK"
    assert rows[1].status == "OK"
    assert rows[2].status == "OK"
    assert rows[3].status == "FAILED"
    assert rows[3].detail == "docker_image_not_found (demo:latest)"
    assert "=== Step timeline (run_id=26fe3e695ca7d8c6) ===" in lines[0]
    assert any("model_service" in line and "FAILED" in line for line in lines)
    assert "Hint: use --step <name>" in lines[-1]


def test_build_step_timeline_sorts_out_of_order_entries():
    """Events must be applied in timestamp order even when input is shuffled."""
    entries = [
        _entry(
            "2026-06-03T06:33:11Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_finished",
                "step_name": "fetch_data",
                "duration_ms": 5000,
            },
        ),
        _entry(
            "2026-06-03T06:33:06Z",
            {
                "log_source": "ml_pipeline",
                "event": "pipeline_step_started",
                "step_name": "fetch_data",
            },
        ),
    ]

    rows = build_step_timeline(entries)

    assert len(rows) == 1
    assert rows[0].status == "OK"
    assert rows[0].started_at == "2026-06-03T06:33:06Z"
    assert rows[0].finished_at == "2026-06-03T06:33:11Z"


def test_format_step_timeline_derives_duration_from_timestamps():
    rows = build_step_timeline(
        [
            _entry(
                "2026-06-03T06:33:06Z",
                {
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_started",
                    "step_name": "fetch_data",
                },
            ),
            _entry(
                "2026-06-03T06:33:11Z",
                {
                    "log_source": "ml_pipeline",
                    "event": "pipeline_step_finished",
                    "step_name": "fetch_data",
                },
            ),
        ]
    )
    lines = format_step_timeline(rows, run_id="run-dur", show_hint=False)

    assert "5s" in lines[2]


def test_format_step_timeline_aligns_long_timestamps():
    rows = [
        run_logs.StepTimelineRow(
            step_name="fetch_data",
            status="OK",
            started_at="2026-06-10T06:01:57.602870783Z",
            finished_at="2026-06-10T06:02:03.306379868Z",
            duration_ms=5703,
            detail="None",
        ),
        run_logs.StepTimelineRow(
            step_name="model_service",
            status="OK",
            finished_at="2026-06-10T06:06:54.293999910Z",
            detail="None",
        ),
    ]

    lines = format_step_timeline(rows, run_id="run-align", show_hint=False)
    header, first_row, second_row = lines[1], lines[2], lines[3]

    assert first_row.index("2026-06-10T06:01:57.602870783Z") == header.index("STARTED (UTC)")
    assert second_row.index("—") == header.index("STARTED (UTC)")
    assert first_row.index("2026-06-10T06:02:03.306379868Z") == header.index("FINISHED (UTC)")
    assert second_row.index("2026-06-10T06:06:54.293999910Z") == header.index("FINISHED (UTC)")
    assert first_row.index("5s") == header.index("DURATION")

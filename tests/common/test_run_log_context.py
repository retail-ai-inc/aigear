import os

from aigear.common.run_log_context import RunLogContext


def test_from_env_returns_none_without_run_id(monkeypatch):
    monkeypatch.delenv("AIGEAR_RUN_ID", raising=False)
    assert RunLogContext.from_env() is None


def test_from_env_parses_required_fields(monkeypatch):
    monkeypatch.setenv("AIGEAR_RUN_ID", "abc123")
    monkeypatch.setenv("AIGEAR_RUN_STARTED_AT_UTC", "2026-05-21T00:00:00Z")
    monkeypatch.setenv("AIGEAR_PIPELINE_VERSION", "v1")
    monkeypatch.setenv("AIGEAR_STEP_NAME", "fetch_data")
    monkeypatch.setenv("AIGEAR_PROJECT_NAME", "demo")

    ctx = RunLogContext.from_env()
    assert ctx is not None
    assert ctx.run_id == "abc123"
    assert ctx.step_name == "fetch_data"
    assert ctx.as_log_fields()["log_source"] == "ml_pipeline"


def test_install_from_env_sets_thread_local(monkeypatch):
    monkeypatch.setenv("AIGEAR_RUN_ID", "abc123")
    monkeypatch.setenv("AIGEAR_RUN_STARTED_AT_UTC", "2026-05-21T00:00:00Z")
    monkeypatch.setenv("AIGEAR_PIPELINE_VERSION", "v1")

    ctx = RunLogContext.install_from_env(gcp_logging=True, project_id="proj")
    assert ctx is not None
    assert RunLogContext.current() is ctx
    assert ctx.gcp_logging is True
    RunLogContext.clear()
    assert RunLogContext.current() is None

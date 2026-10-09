import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ValidationError

from aigear.common import config


@pytest.fixture
def raw_config():
    sample = Path(__file__).resolve().parents[2] / "src/aigear/template/env.sample.json"
    return json.loads(sample.read_text(encoding="utf-8"))


@pytest.fixture
def loader(monkeypatch, raw_config):
    monkeypatch.setattr(config.AppConfig, "_raw", None)
    monkeypatch.setattr(config.AppConfig, "_aigear", None)
    loader = Mock(return_value=raw_config)
    monkeypatch.setattr(config, "_load_raw", loader)
    return loader


def test_config_loads_once_and_legacy_accessors_share_cache(loader, raw_config):
    assert config.get_project_name() == raw_config["project_name"]
    assert config.get_environment() == raw_config["environment"]
    assert config.EnvConfig.get_config_with_json() is raw_config
    assert config.PipelinesConfig.get_config() is config.AppConfig.pipelines()
    assert config.AigearConfig.get_config() is config.AppConfig.aigear()
    loader.assert_called_once_with()


def test_missing_pipeline_returns_empty_config(loader):
    assert config.AppConfig.pipeline("does-not-exist") == {}
    assert config.PipelinesConfig.get_version_config() == {}


def test_raw_as_validates_custom_model(loader, raw_config):
    class Environment(BaseModel):
        project_name: str

    assert config.AppConfig.raw_as(Environment).project_name == raw_config["project_name"]


def test_invalid_aigear_config_raises_validation_error(loader):
    loader.return_value = {"aigear": {"gcp": {}}}
    with pytest.raises(ValidationError):
        config.AppConfig.aigear()


def test_missing_env_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Configuration file not found"):
        config._load_raw(tmp_path / "missing.json")


def test_invalid_env_json_raises(tmp_path):
    path = tmp_path / "env.json"
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        config._load_raw(path)


def test_env_path_override_is_applied_in_fresh_process(tmp_path):
    path = tmp_path / "custom.json"
    path.write_text('{"project_name":"override-project"}', encoding="utf-8")
    env = {**os.environ, "AIGEAR_ENV_PATH": str(path)}
    result = subprocess.run(
        [sys.executable, "-B", "-c", "from aigear.common.config import AppConfig; print(AppConfig.project_name())"],
        env=env, capture_output=True, text=True, timeout=30, check=True,
    )
    assert result.stdout.strip() == "override-project"


def test_schema_preserves_existing_file_and_force_regenerates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    schema = tmp_path / "config_schema/env_schema.py"
    schema.parent.mkdir()
    schema.write_text("existing content", encoding="utf-8")
    generator = Mock()
    monkeypatch.setattr(config, "generate_schema", generator)
    config.AppConfig.generate_env_schema()
    generator.assert_not_called()
    assert schema.read_text(encoding="utf-8") == "existing content"
    config.AppConfig.generate_env_schema(forced_generate=True)
    assert generator.call_args.kwargs["output"] == schema
    assert generator.call_args.kwargs["forced_generate"] is True
    assert (schema.parent / "__init__.py").exists()


def test_show_and_delete_schema_are_safe_when_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    schema = tmp_path / "config_schema/env_schema.py"
    schema.parent.mkdir()
    schema.write_text("class EnvSchema: pass", encoding="utf-8")
    config.AppConfig.show_env_schema()
    assert "class EnvSchema: pass" in capsys.readouterr().out
    config.AppConfig.delete_env_schema()
    assert not schema.exists()
    config.AppConfig.delete_env_schema()
    config.AppConfig.show_env_schema()


def test_schema_generation_produces_importable_model(tmp_path, monkeypatch):
    import runpy

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "env.json"
    path.write_text('{"project_name":"demo","environment":"local"}', encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", path)
    config.AppConfig.generate_env_schema()
    model = runpy.run_path(str(tmp_path / "config_schema/env_schema.py"))["EnvSchema"]
    assert model(project_name="demo", environment="local").project_name == "demo"

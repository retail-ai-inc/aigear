import json
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ValidationError

from aigear.common import config
from aigear.common.schema import config_schema


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


def test_nested_bucket_defaults_and_raw_preservation(loader, raw_config):
    source = raw_config["aigear"]["gcp"]["bucket"]
    source["train_bucket"].pop("trash_retention_days")
    original = deepcopy(raw_config)
    bucket = config.AppConfig.aigear().gcp.bucket
    assert isinstance(bucket.train_bucket, config_schema.TrainBucket)
    assert isinstance(bucket.release_bucket, config_schema.ReleaseBucket)
    assert bucket.train_bucket.bucket_name == source["train_bucket"]["bucket_name"]
    assert bucket.release_bucket.bucket_name == source["release_bucket"]["bucket_name"]
    assert bucket.train_bucket.trash_retention_days == 30
    assert config.AppConfig.raw() == original


@pytest.mark.parametrize("mixed", [False, True])
def test_legacy_names_take_priority_without_mutating_raw(loader, raw_config, mixed):
    source = {"on": True, "bucket_name": "old-train", "bucket_name_for_release": "old-release"}
    if mixed:
        source.update(train_bucket={"unknown": True, "trash_retention_days": False}, release_bucket=None)
    raw_config["aigear"]["gcp"]["bucket"] = source
    original = deepcopy(raw_config)
    bucket = config.AigearConfig.get_config().gcp.bucket
    assert bucket.train_bucket.bucket_name == "old-train"
    assert bucket.release_bucket.bucket_name == "old-release"
    assert bucket.train_bucket.trash_retention_days is None
    assert not hasattr(bucket, "bucket_name")
    assert config.EnvConfig.get_config_with_json() == original


@pytest.mark.parametrize("value", [True, False, "30", None, 0, -1, 1.5, 30.0])
@pytest.mark.parametrize("entry", ["aigear", "raw_as", "generated"])
def test_retention_rejects_invalid_values(loader, raw_config, value, entry):
    raw_config["aigear"]["gcp"]["bucket"]["train_bucket"]["trash_retention_days"] = value
    with pytest.raises(ValidationError, match="trash_retention_days"):
        if entry == "aigear":
            config.AppConfig.aigear()
        elif entry == "raw_as":
            config.AppConfig.raw_as(BaseModel)
        else:
            config_schema.Config.model_validate(raw_config["aigear"])


@pytest.mark.parametrize("value", [1, 30, 365])
def test_retention_accepts_positive_integers(loader, raw_config, value):
    raw_config["aigear"]["gcp"]["bucket"]["train_bucket"]["trash_retention_days"] = value
    assert config.AppConfig.aigear().gcp.bucket.train_bucket.trash_retention_days == value


@pytest.mark.parametrize("node,field", [
    (None, "unknown"), ("train_bucket", "unknown"), ("release_bucket", "unknown"),
    ("release_bucket", "trash_retention_days"), (None, "bucket_name_for_release"),
])
@pytest.mark.parametrize("entry", ["aigear", "raw_as", "generated"])
def test_bucket_unknown_fields_are_rejected(loader, raw_config, node, field, entry):
    bucket = raw_config["aigear"]["gcp"]["bucket"]
    target = bucket if node is None else bucket[node]
    target[field] = 30
    with pytest.raises(ValidationError, match="extra_forbidden"):
        if entry == "aigear":
            config.AppConfig.aigear()
        elif entry == "raw_as":
            config.AppConfig.raw_as(BaseModel)
        else:
            config_schema.Config.model_validate(raw_config["aigear"])


@pytest.mark.parametrize("source", [
    {"on": True, "bucket_name_for_release": "release"},
    {"on": True, "bucket_name": "train"},
    {"on": True, "bucket_name": None, "bucket_name_for_release": "release"},
    {"on": True, "bucket_name": "train", "bucket_name_for_release": "release", "typo": 1},
])
def test_incomplete_or_invalid_legacy_config_fails(loader, raw_config, source):
    raw_config["aigear"]["gcp"]["bucket"] = source
    with pytest.raises(ValidationError):
        config.AppConfig.aigear()


def test_previous_optional_fields_remain_optional(loader, raw_config):
    raw_config["aigear"].pop("slack")
    raw_config["aigear"]["custom_extension"] = {"enabled": True}
    gcp = raw_config["aigear"]["gcp"]
    gcp["custom_extension"] = True
    for key in ("event", "branch_pattern", "tag_pattern"):
        gcp["cloud_build"].pop(key)
    for key in ("ms_image_name", "pl_image_name", "image_tag"):
        gcp["artifacts"].pop(key)
    model = config_schema.Config.model_validate(raw_config["aigear"])
    assert config.AppConfig.aigear() == model
    assert model.slack is None
    result = model.gcp
    assert result.cloud_build.event == "push"
    assert result.cloud_build.branch_pattern is None
    assert result.cloud_build.tag_pattern is None
    assert result.artifacts.ms_image_name is None
    assert result.artifacts.pl_image_name is None
    assert result.artifacts.image_tag == "latest"


@pytest.mark.parametrize("legacy", [False, True])
def test_generated_project_schema_uses_raw_shape_and_unified_validation(
    tmp_path, monkeypatch, loader, raw_config, legacy
):
    import runpy

    if legacy:
        raw_config["aigear"]["gcp"]["bucket"] = {
            "on": True, "bucket_name": "old-train", "bucket_name_for_release": "old-release"
        }
    original = deepcopy(raw_config)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "env.json"
    path.write_text(json.dumps(raw_config), encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", path)
    # Loading the runtime model first must not influence schema generation.
    config.AppConfig.aigear()
    config.EnvConfig.generative_env_schema()
    namespace = runpy.run_path(str(tmp_path / "config_schema/env_schema.py"))
    model = namespace["EnvSchema"]
    model.model_rebuild(_types_namespace=namespace)
    loaded = config.EnvConfig.get_config_with_schema(model).aigear.gcp.bucket
    assert path.read_text(encoding="utf-8") == json.dumps(original)
    assert config.AppConfig.raw() == original
    if legacy:
        assert loaded.bucket_name == "old-train"
        assert loaded.bucket_name_for_release == "old-release"
        assert not hasattr(loaded, "train_bucket")
    else:
        assert loaded.train_bucket.trash_retention_days == 30
        assert not hasattr(loaded, "bucket_name")
        source = raw_config["aigear"]["gcp"]["bucket"]
        source["train_bucket"].pop("trash_retention_days")
        assert config.AppConfig.raw_as(model).aigear.gcp.bucket.train_bucket.trash_retention_days == 30
        assert "trash_retention_days" not in source["train_bucket"]
        # The generated schema would coerce this; the unified loader must reject it,
        # even after the separately cached runtime model was loaded successfully.
        source["train_bucket"]["trash_retention_days"] = "30"
        with pytest.raises(ValidationError, match="trash_retention_days"):
            config.EnvConfig.get_config_with_schema(model)


def test_invalid_config_does_not_cache_partial_result(loader, raw_config):
    train = raw_config["aigear"]["gcp"]["bucket"]["train_bucket"]
    train["trash_retention_days"] = False
    with pytest.raises(ValidationError):
        config.AppConfig.aigear()
    assert config.AppConfig._aigear is None
    train["trash_retention_days"] = 7
    assert config.AppConfig.aigear().gcp.bucket.train_bucket.trash_retention_days == 7


def test_framework_schema_is_reproducible_from_template(tmp_path, raw_config):
    from aigear.common import dynamic_type

    checked_in = Path(config_schema.__file__).read_text(encoding="utf-8")
    sample = tmp_path / "src/aigear/template/env.sample.json"
    sample.parent.mkdir(parents=True)
    sample.write_text(json.dumps(raw_config), encoding="utf-8")
    generated = tmp_path / "src/aigear/common/schema/config_schema.py"
    generated.parent.mkdir(parents=True)
    script = generated.parent.parent / "dynamic_type.py"
    script.write_text(Path(dynamic_type.__file__).read_text(encoding="utf-8"), encoding="utf-8")

    def without_timestamp(text):
        return [line for line in text.splitlines() if not line.startswith("#   timestamp:")]

    dynamic_type.generate_aigear_config_schema(env_sample=sample, output_path=generated)
    assert without_timestamp(generated.read_text(encoding="utf-8")) == without_timestamp(checked_in)

    # Exercise the actual __main__ entry point outside the common/ directory,
    # then repeat generation to ensure the corrections are applied each time.
    for _ in range(2):
        subprocess.run(
            [sys.executable, str(script)], cwd=tmp_path,
            capture_output=True, text=True, timeout=30, check=True,
        )
        assert without_timestamp(generated.read_text(encoding="utf-8")) == without_timestamp(checked_in)

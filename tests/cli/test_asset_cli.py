import json

import pytest

from aigear.cli.asset import asset_cli
from aigear.management.registry import AssetRecord, FakeAssetRegistry


def _record(asset_type, name, version, created_at_utc="2026-06-30T00:00:00Z"):
    return AssetRecord(
        asset_type=asset_type,
        name=name,
        version=version,
        uri=f"gs://bucket/{asset_type}/{name}/{version}",
        file_name=f"{name}.pkl",
        created_at_utc=created_at_utc,
        status="active",
    )


def test_latest_uses_version_alias_and_model_name_default(capsys):
    registry = FakeAssetRegistry()
    registry.register(_record("model", "logistic_regression", "v1"))

    asset_cli(
        ["latest", "--version", "logistic_regression", "--type", "model"],
        registry=registry,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "logistic_regression"
    assert payload["version"] == "v1"


def test_latest_resolves_single_candidate_name(capsys):
    registry = FakeAssetRegistry()
    registry.register(_record("dataset", "breast_cancer", "v1"))

    asset_cli(
        ["latest", "--pipeline-version", "logistic_regression", "--type", "dataset"],
        registry=registry,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "breast_cancer"


def test_latest_requires_name_when_multiple_candidates(capsys):
    registry = FakeAssetRegistry()
    registry.register(_record("feature", "training_features", "v1"))
    registry.register(_record("feature", "standard_scaler", "v1"))

    with pytest.raises(SystemExit) as excinfo:
        asset_cli(
            ["latest", "--pipeline-version", "logistic_regression", "--type", "feature"],
            registry=registry,
        )

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--name is required" in err
    assert "standard_scaler" in err
    assert "training_features" in err


def test_list_outputs_filtered_records(capsys):
    registry = FakeAssetRegistry()
    registry.register(_record("dataset", "raw", "v1"))
    registry.register(_record("model", "logistic_regression", "v1"))

    asset_cli(
        ["list", "--pipeline-version", "logistic_regression", "--type", "dataset"],
        registry=registry,
    )

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["asset_type"] == "dataset"


def test_lineage_defaults_to_latest_version(capsys):
    registry = FakeAssetRegistry()
    dataset = _record("dataset", "raw", "v1")
    model = _record("model", "logistic_regression", "v1")
    model.inputs = [dataset.ref]
    registry.register(dataset)
    registry.register(model)

    asset_cli(
        ["lineage", "--pipeline-version", "logistic_regression", "--type", "model"],
        registry=registry,
    )

    payload = json.loads(capsys.readouterr().out)
    assert [item["asset_type"] for item in payload] == ["dataset", "model"]


def test_alias_can_set_and_read(capsys):
    registry = FakeAssetRegistry()
    registry.register(_record("service", "svc", "service-v1"))

    asset_cli(
        [
            "alias",
            "--pipeline-version",
            "logistic_regression",
            "--type",
            "service",
            "--name",
            "svc",
            "--alias",
            "champion",
            "--asset-version",
            "service-v1",
        ],
        registry=registry,
    )
    asset_cli(
        [
            "alias",
            "--pipeline-version",
            "logistic_regression",
            "--type",
            "service",
            "--name",
            "svc",
            "--alias",
            "champion",
        ],
        registry=registry,
    )

    assert capsys.readouterr().out.splitlines() == [
        "champion: service-v1",
        "champion: service-v1",
    ]


def test_register_external_uses_manager(capsys):
    class Manager:
        def register_external(self, **kwargs):
            assert kwargs["asset_type"] == "dataset"
            assert kwargs["asset_name"] == "raw"
            assert kwargs["uri"] == "gs://bucket/raw.csv"
            assert kwargs["metadata"] == {"source": "manual"}
            return _record("dataset", "raw", "v1")

    asset_cli(
        [
            "register-external",
            "--pipeline-version",
            "logistic_regression",
            "--type",
            "dataset",
            "--name",
            "raw",
            "--uri",
            "gs://bucket/raw.csv",
            "--metadata",
            '{"source": "manual"}',
        ],
        manager=Manager(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "raw"


def test_firestore_index_error_has_actionable_hint(capsys):
    class BrokenRegistry:
        def list(self, **kwargs):
            raise RuntimeError("Firestore index required: https://console.cloud.google.com")

    with pytest.raises(SystemExit) as excinfo:
        asset_cli(
            ["list", "--pipeline-version", "logistic_regression", "--type", "model"],
            registry=BrokenRegistry(),
        )

    assert excinfo.value.code == 2
    assert "composite index" in capsys.readouterr().err

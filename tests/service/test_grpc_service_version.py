import os
from unittest.mock import MagicMock, patch

import pytest

from aigear.management.registry import AssetRecord, FakeAssetRegistry
from aigear.service.grpc.grpc_service import grpc_service


def _pipeline_config():
    return {
        "model_service": {
            "grpc": {"multi_processing": {"on": False}},
        }
    }


def _service_record(version, model_version):
    model = AssetRecord(
        asset_type="model",
        name="logistic_regression",
        version=model_version,
        uri=f"gs://bucket/{model_version}.pkl",
        status="active",
    )
    service = AssetRecord(
        asset_type="service",
        name="aigear-demo-logistic-regression-service",
        version=version,
        uri=f"gs://bucket/{version}.yaml",
        inputs=[model.ref],
        status="active",
    )
    return model, service


def test_grpc_service_exports_requested_service_model_ref(monkeypatch):
    registry = FakeAssetRegistry()
    model, service = _service_record("service-v2", "model-v2")
    registry.register(model)
    registry.register(service)

    with patch("aigear.service.grpc.grpc_service.get_project_name", return_value="aigear_demo"):
        with patch(
            "aigear.service.grpc.grpc_service.PipelinesConfig.get_version_config",
            return_value=_pipeline_config(),
        ):
            with patch("aigear.service.grpc.grpc_service.get_environment", return_value="local"):
                with patch("aigear.service.grpc.grpc_service.LoadModule") as load_module:
                    with patch("aigear.service.grpc.grpc_service._run_server"):
                        load_module.return_value.load_module.return_value = MagicMock
                        grpc_service(
                            "logistic_regression",
                            "pkg.Service",
                            service_version="service-v2",
                            registry=registry,
                        )

    assert os.environ["AIGEAR_SERVICE_VERSION"] == "service-v2"
    assert os.environ["AIGEAR_MODEL_ASSET_NAME"] == "logistic_regression"
    assert os.environ["AIGEAR_MODEL_ASSET_VERSION"] == "model-v2"


def test_grpc_service_without_service_version_uses_latest(monkeypatch):
    registry = FakeAssetRegistry()
    older_model, older_service = _service_record("service-v1", "model-v1")
    newer_model, newer_service = _service_record("service-v2", "model-v2")
    older_service.created_at_utc = "2026-06-29T00:00:00Z"
    newer_service.created_at_utc = "2026-06-30T00:00:00Z"
    registry.register(older_model)
    registry.register(older_service)
    registry.register(newer_model)
    registry.register(newer_service)
    monkeypatch.delenv("AIGEAR_SERVICE_VERSION", raising=False)

    with patch("aigear.service.grpc.grpc_service.get_project_name", return_value="aigear_demo"):
        with patch(
            "aigear.service.grpc.grpc_service.PipelinesConfig.get_version_config",
            return_value=_pipeline_config(),
        ):
            with patch("aigear.service.grpc.grpc_service.get_environment", return_value="local"):
                with patch("aigear.service.grpc.grpc_service.LoadModule") as load_module:
                    with patch("aigear.service.grpc.grpc_service._run_server"):
                        load_module.return_value.load_module.return_value = MagicMock
                        grpc_service("logistic_regression", "pkg.Service", registry=registry)

    assert os.environ["AIGEAR_SERVICE_VERSION"] == "service-v2"
    assert os.environ["AIGEAR_MODEL_ASSET_VERSION"] == "model-v2"


def test_grpc_service_missing_requested_version_raises():
    registry = FakeAssetRegistry()

    with patch("aigear.service.grpc.grpc_service.get_project_name", return_value="aigear_demo"):
        with patch(
            "aigear.service.grpc.grpc_service.PipelinesConfig.get_version_config",
            return_value=_pipeline_config(),
        ):
            with pytest.raises(FileNotFoundError):
                grpc_service(
                    "logistic_regression",
                    "pkg.Service",
                    service_version="service-v9",
                    registry=registry,
                )

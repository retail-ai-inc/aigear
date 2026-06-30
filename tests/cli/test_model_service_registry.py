from pathlib import Path
from unittest.mock import patch

from aigear.cli.model_service import run_model_cli
from aigear.management.registry import AssetRecord, FakeAssetRegistry


def test_deploy_registers_service_asset_and_alias(tmp_path):
    registry = FakeAssetRegistry()
    registry.register(
        AssetRecord(
            asset_type="model",
            name="logistic_regression",
            version="model-v1",
            uri="gs://bucket/model.pkl",
            status="active",
        )
    )
    helm_path = tmp_path / "grpc_deployment_local.yaml"

    with patch("aigear.cli.model_service.get_project_name", return_value="aigear_demo"):
        with patch("aigear.cli.model_service.get_image_path", return_value="image:latest"):
            with patch(
                "aigear.cli.model_service.create_helm_file", return_value=helm_path
            ) as create_helm_file:
                with patch(
                    "aigear.deploy.local.grpc_local_deploy.deploy_local_grpc"
                ) as deploy_local_grpc:
                    run_model_cli(
                        [
                            "--version",
                            "logistic_regression",
                            "--local",
                            "--deploy",
                            "--replicas",
                            "2",
                            "--port",
                            "50051",
                        ],
                        registry=registry,
                    )

    create_helm_file.assert_called_once()
    deploy_local_grpc.assert_called_once_with(helm_path)
    service_name = "aigear-demo-logistic-regression-service"
    service = registry.latest("service", service_name)
    assert service is not None
    assert service.version == "service-v1"
    assert service.inputs[0].version == "model-v1"
    assert service.metadata["replicas"] == 2
    assert service.metadata["yaml_uri"] == str(helm_path)
    assert registry.get_alias("service", service_name, "champion") == "service-v1"


def test_second_deploy_moves_champion_to_previous(tmp_path):
    registry = FakeAssetRegistry()
    helm_path = tmp_path / "grpc_deployment_local.yaml"

    with patch("aigear.cli.model_service.get_project_name", return_value="aigear_demo"):
        with patch("aigear.cli.model_service.get_image_path", return_value="image:latest"):
            with patch("aigear.cli.model_service.create_helm_file", return_value=helm_path):
                with patch("aigear.deploy.local.grpc_local_deploy.deploy_local_grpc"):
                    run_model_cli(
                        ["--version", "logistic_regression", "--local", "--deploy"],
                        registry=registry,
                    )
                    run_model_cli(
                        ["--version", "logistic_regression", "--local", "--deploy"],
                        registry=registry,
                    )

    service_name = "aigear-demo-logistic-regression-service"
    assert registry.get_alias("service", service_name, "champion") == "service-v2"
    assert registry.get_alias("service", service_name, "previous") == "service-v1"


def test_rollback_uses_previous_service_record_to_recreate_yaml(tmp_path):
    registry = FakeAssetRegistry()
    service_name = "aigear-demo-logistic-regression-service"
    registry.register(
        AssetRecord(
            asset_type="service",
            name=service_name,
            version="service-v1",
            uri="old.yaml",
            metadata={
                "service_ports": "50051",
                "port": "50051",
                "replicas": 3,
            },
            status="active",
        )
    )
    registry.set_alias("service", service_name, "champion", "service-v2")
    registry.set_alias("service", service_name, "champion", "service-v3")
    registry.set_alias("service", service_name, "previous", "service-v1")
    helm_path = tmp_path / "grpc_deployment_local.yaml"

    with patch("aigear.cli.model_service.get_project_name", return_value="aigear_demo"):
        with patch(
            "aigear.cli.model_service.create_helm_file", return_value=helm_path
        ) as create_helm_file:
            with patch(
                "aigear.deploy.local.grpc_local_deploy.update_local_grpc"
            ) as update_local_grpc:
                run_model_cli(
                    [
                        "--version",
                        "logistic_regression",
                        "--local",
                        "--rollback",
                    ],
                    registry=registry,
                )

    create_helm_file.assert_called_once_with(
        pipeline_version="logistic_regression",
        service_ports="50051",
        replicas=3,
        port="50051",
        env="local",
        force=True,
    )
    update_local_grpc.assert_called_once_with(helm_path)


def test_rollback_can_use_explicit_service_version(tmp_path):
    registry = FakeAssetRegistry()
    service_name = "aigear-demo-logistic-regression-service"
    registry.register(
        AssetRecord(
            asset_type="service",
            name=service_name,
            version="service-v9",
            uri="old.yaml",
            metadata={"replicas": 4},
            status="active",
        )
    )
    helm_path = tmp_path / "grpc_deployment_local.yaml"

    with patch("aigear.cli.model_service.get_project_name", return_value="aigear_demo"):
        with patch(
            "aigear.cli.model_service.create_helm_file", return_value=helm_path
        ) as create_helm_file:
            with patch("aigear.deploy.local.grpc_local_deploy.update_local_grpc"):
                run_model_cli(
                    [
                        "--version",
                        "logistic_regression",
                        "--local",
                        "--rollback",
                        "--service-version",
                        "service-v9",
                    ],
                    registry=registry,
                )

    assert create_helm_file.call_args.kwargs["replicas"] == 4

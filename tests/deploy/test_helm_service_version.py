from pathlib import Path

from aigear.deploy.common.helm_chart import _create_helm_chart


def test_create_helm_chart_includes_service_version_argument(tmp_path):
    helm_path = tmp_path / "grpc_deployment_local.yaml"

    _create_helm_chart(
        helm_path=helm_path,
        service_name="svc",
        service_image="image:latest",
        pipeline_version="logistic_regression",
        model_class_path="pkg.module.Service",
        service_version="service-v2",
    )

    content = helm_path.read_text(encoding="utf-8")
    assert '- "--service-version"' in content
    assert '- "service-v2"' in content

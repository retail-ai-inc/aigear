from pathlib import Path

import pytest
import yaml

from aigear.deploy.common import helm_chart


@pytest.fixture
def model_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {"model_service": {"model_class_path": "src.pipelines.v1.model_service.model.ModelService", "venv_ms": "ms"}}
    monkeypatch.setattr(helm_chart.AppConfig, "pipeline", lambda _version: config)
    monkeypatch.setattr(helm_chart, "get_project_name", lambda: "demo_project")
    monkeypatch.setattr(helm_chart, "get_image_path", lambda **kwargs: "registry/model:v1")
    (tmp_path / "src/pipelines/v1/model_service").mkdir(parents=True)
    return config


@pytest.mark.parametrize("environment,policy", [("local", "Never"), ("staging", "Always"), ("production", "Always")])
def test_generated_manifest_has_expected_image_command_and_volume(model_config, environment, policy):
    path = helm_chart.create_helm_file("v1", env=environment, service_ports="6000", port="6001", replicas=3)
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    deployment = next(document for document in documents if document["kind"] == "Deployment")
    service = next(document for document in documents if document["kind"] == "Service")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert deployment["metadata"]["name"] == "demo-project-v1-service"
    assert deployment["spec"]["replicas"] == 3
    assert container["image"] == "registry/model:v1"
    assert container["imagePullPolicy"] == policy
    assert container["command"] == ["/opt/venv/ms/bin/aigear-task", "grpc"]
    assert container["args"] == ["--version", "v1"]
    assert container["ports"][0]["containerPort"] == 6000
    assert service["spec"]["ports"][0]["port"] == 6001
    assert ("volumes" in pod) is (environment == "local")
    if environment == "local":
        assert container["volumeMounts"][0]["mountPath"] == "/ms/asset"


def test_existing_manifest_is_preserved_until_forced(model_config):
    path = helm_chart.create_helm_file("v1")
    path.write_text("custom manifest", encoding="utf-8")
    assert helm_chart.create_helm_file("v1") == path
    assert path.read_text(encoding="utf-8") == "custom manifest"
    helm_chart.create_helm_file("v1", force=True)
    assert "kind: Deployment" in path.read_text(encoding="utf-8")


def test_missing_venv_uses_image_default_command(model_config):
    model_config["model_service"].pop("venv_ms")
    path = helm_chart.create_helm_file("v1")
    assert 'command: ["aigear-task", "grpc"]' in path.read_text(encoding="utf-8")


def test_windows_host_path_uses_docker_desktop_mount(monkeypatch):
    from pathlib import PureWindowsPath

    monkeypatch.setattr(helm_chart.platform, "system", lambda: "Windows")
    assert helm_chart._to_hostpath(PureWindowsPath("D:/ml/asset")) == "/run/desktop/mnt/host/d/ml/asset"


def test_unix_host_path_is_preserved(monkeypatch):
    monkeypatch.setattr(helm_chart.platform, "system", lambda: "Linux")
    assert helm_chart._to_hostpath(Path("/ml/asset")) == "/ml/asset"


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
@pytest.mark.parametrize("missing_service", [False, True])
def test_missing_model_class_uses_image_startup_at_project_root(model_config, environment, missing_service):
    if missing_service:
        model_config.pop("model_service")
    else:
        model_config["model_service"].pop("model_class_path")
    path = helm_chart.create_helm_file("v1", env=environment)
    assert path == Path.cwd() / f"grpc_deployment_{environment}.yaml"
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    deployment = next(document for document in documents if document["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "registry/model:v1"
    assert "command" not in container
    assert "args" not in container

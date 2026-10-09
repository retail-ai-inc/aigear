from pathlib import Path
from unittest.mock import Mock

import pytest

from aigear.cli import model_service


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
@pytest.mark.parametrize("operation", ["yaml", "deploy", "update", "delete", "status"])
def test_model_cli_dispatches_environment_and_operation(monkeypatch, environment, operation):
    from aigear.deploy.local import grpc_local_deploy
    from aigear.deploy.gcp import grpc_gcp_deploy

    monkeypatch.setattr("sys.argv", ["aigear-model", "--version", "v1", f"--{environment}", f"--{operation}"])
    path = Path("grpc_deployment.yaml")
    create = Mock(return_value=path)
    lookup = Mock(return_value=path)
    monkeypatch.setattr(model_service, "create_helm_file", create)
    monkeypatch.setattr(model_service, "get_helm_path", lookup)
    calls = {}
    for module, suffix in ((grpc_local_deploy, "local"), (grpc_gcp_deploy, "gcp")):
        for action in ("deploy", "update", "delete", "status"):
            calls[suffix, action] = Mock()
            monkeypatch.setattr(module, f"{action}_{suffix}_grpc", calls[suffix, action])
    model_service.run_model_cli()
    if operation in ("yaml", "deploy", "update"):
        create.assert_called_once_with(pipeline_version="v1", service_ports=None, replicas=None, port=None, env=environment, force=operation == "yaml")
        lookup.assert_not_called()
    else:
        lookup.assert_called_once_with(pipeline_version="v1", env=environment)
        create.assert_not_called()
    if operation != "yaml":
        selected = calls["local" if environment == "local" else "gcp", operation]
        selected.assert_called_once_with(path)
        assert sum(call.call_count for call in calls.values()) == 1
    else:
        assert all(call.call_count == 0 for call in calls.values())


@pytest.mark.parametrize("flag,value,keyword", [("--replicas", "3", "replicas"), ("--service_ports", "6000", "service_ports"), ("--port", "6001", "port")])
def test_explicit_model_parameters_force_yaml_regeneration(monkeypatch, flag, value, keyword):
    from aigear.deploy.local import grpc_local_deploy

    monkeypatch.setattr("sys.argv", ["aigear-model", "--version", "v1", "--local", "--update", flag, value])
    create = Mock(return_value=Path("manifest.yaml"))
    monkeypatch.setattr(model_service, "create_helm_file", create)
    monkeypatch.setattr(grpc_local_deploy, "update_local_grpc", Mock())
    model_service.run_model_cli()
    assert create.call_args.kwargs["force"] is True
    assert str(create.call_args.kwargs[keyword]) == value


@pytest.mark.parametrize("args", [["--local", "--staging", "--yaml"], ["--local", "--deploy", "--delete"], ["--yaml"], ["--local"]])
def test_model_parser_rejects_conflicting_or_missing_groups(args):
    with pytest.raises(SystemExit) as exc:
        model_service._get_parser().parse_args(["--version", "v1", *args])
    assert exc.value.code == 2


@pytest.mark.xfail(strict=True, reason="Known defect: model CLI does not require --version")
def test_model_parser_requires_pipeline_version():
    with pytest.raises(SystemExit):
        model_service._get_parser().parse_args(["--local", "--yaml"])

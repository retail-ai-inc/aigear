from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aigear.common import sh
from aigear.deploy.common import kubectl_command
from aigear.deploy.local import grpc_local_deploy
from aigear.deploy.gcp import grpc_gcp_deploy


@pytest.mark.parametrize("module,suffix", [(grpc_local_deploy, "local"), (grpc_gcp_deploy, "gcp")])
@pytest.mark.parametrize("operation,helper", [("deploy", "kubectl_apply"), ("update", "kubectl_apply"), ("delete", "kubectl_delete"), ("status", "kubectl_status")])
@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_failed_context_switch_never_runs_kubectl(monkeypatch, module, suffix, operation, helper, failure):
    monkeypatch.setattr(sh.platform, "system", lambda: "Windows")
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b"credentials failed"))
    if failure == "timeout":
        run.side_effect = subprocess.TimeoutExpired("context switch", 30)
    monkeypatch.setattr(sh.subprocess, "run", run)
    monkeypatch.setattr(grpc_gcp_deploy.AigearConfig, "get_config", lambda: SimpleNamespace(gcp=SimpleNamespace(
        gcp_project_id="project", location="region", kubernetes=SimpleNamespace(cluster_name="cluster"))))
    kubectl = Mock(return_value="created")
    monkeypatch.setattr(module, helper, kubectl)
    message = "credentials failed" if failure == "nonzero" else "timeout"
    with pytest.raises(RuntimeError, match=message):
        getattr(module, f"{operation}_{suffix}_grpc")(Path("manifest.yaml"))
    kubectl.assert_not_called()
    run.assert_called_once()


@pytest.mark.parametrize("module,suffix", [(grpc_local_deploy, "local"), (grpc_gcp_deploy, "gcp")])
@pytest.mark.parametrize("operation,helper", [("deploy", "kubectl_apply"), ("update", "kubectl_apply"), ("delete", "kubectl_delete"), ("status", "kubectl_status")])
def test_successful_context_switch_precedes_kubectl(monkeypatch, module, suffix, operation, helper):
    monkeypatch.setattr(sh.platform, "system", lambda: "Windows")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b"context switched", stderr=b""))
    monkeypatch.setattr(sh.subprocess, "run", run)
    monkeypatch.setattr(grpc_gcp_deploy.AigearConfig, "get_config", lambda: SimpleNamespace(gcp=SimpleNamespace(
        gcp_project_id="project", location="region", kubernetes=SimpleNamespace(cluster_name="cluster"))))
    manifest = Path("manifest.yaml")

    def kubectl_after_switch(path):
        assert path == manifest
        command = (["kubectl", "config", "use-context", "docker-desktop"] if suffix == "local" else
                   ["gcloud", "container", "clusters", "get-credentials", "cluster", "--region=region", "--project=project"])
        run.assert_called_once_with(command, input=None, capture_output=True, shell=True, timeout=30)
        return "created"

    kubectl = Mock(side_effect=kubectl_after_switch)
    monkeypatch.setattr(module, helper, kubectl)
    getattr(module, f"{operation}_{suffix}_grpc")(manifest)
    kubectl.assert_called_once_with(manifest)


@pytest.mark.parametrize("operation", ["kubectl_apply", "kubectl_delete", "kubectl_status"])
@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_kubectl_command_failure_is_logged_and_propagated(monkeypatch, operation, failure):
    monkeypatch.setattr(sh.platform, "system", lambda: "Windows")
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout=b"resource output\n", stderr=b"forbidden"))
    if failure == "timeout":
        run.side_effect = subprocess.TimeoutExpired("kubectl", 30)
    monkeypatch.setattr(sh.subprocess, "run", run)
    error = Mock()
    monkeypatch.setattr(kubectl_command.logger, "error", error)
    message = "forbidden" if failure == "nonzero" else "timeout"
    with pytest.raises(RuntimeError, match=message) as exc:
        getattr(kubectl_command, operation)(Path("manifest.yaml"))
    error.assert_called_once_with(str(exc.value))
    if failure == "nonzero":
        assert "resource output" in str(exc.value)
        assert "exit 1" in str(exc.value)


@pytest.mark.parametrize("operation,command", [
    ("kubectl_apply", ["kubectl", "apply", "-f", "manifest.yaml"]),
    ("kubectl_delete", ["kubectl", "delete", "-f", "manifest.yaml", "--wait=false"]),
    ("kubectl_status", ["kubectl", "get", "-f", "manifest.yaml"]),
])
def test_successful_kubectl_command_preserves_output_and_return_value(monkeypatch, operation, command):
    output = "first line\nsecond line\n"
    run = Mock(return_value=output)
    monkeypatch.setattr(kubectl_command, "run_sh", run)
    info = Mock()
    error = Mock()
    monkeypatch.setattr(kubectl_command.logger, "info", info)
    monkeypatch.setattr(kubectl_command.logger, "error", error)
    result = getattr(kubectl_command, operation)(Path("manifest.yaml"))
    run.assert_called_once_with(command, check=True)
    assert result == (None if operation == "kubectl_delete" else output)
    if operation == "kubectl_status":
        assert [call.args[0] for call in info.call_args_list] == output.splitlines()
    else:
        info.assert_called_once_with(output)
    error.assert_not_called()


@pytest.mark.parametrize("module,suffix,switch", [
    (grpc_local_deploy, "local", "switch_local_context"),
    (grpc_gcp_deploy, "gcp", "_switch_context"),
])
@pytest.mark.parametrize("operation", ["deploy", "update"])
def test_failed_apply_does_not_log_deployment_success(monkeypatch, module, suffix, switch, operation):
    monkeypatch.setattr(module, switch, Mock())
    monkeypatch.setattr(module, "kubectl_apply", Mock(side_effect=RuntimeError("forbidden")))
    info = Mock()
    monkeypatch.setattr(module.logger, "info", info)
    with pytest.raises(RuntimeError, match="forbidden"):
        getattr(module, f"{operation}_{suffix}_grpc")(Path("manifest.yaml"))
    info.assert_not_called()

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
@pytest.mark.xfail(strict=True, reason="Known defect: kubectl helpers do not propagate nonzero command exit codes")
def test_kubectl_command_failure_is_propagated(monkeypatch, operation):
    monkeypatch.setattr(sh.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b"forbidden")))
    with pytest.raises(RuntimeError, match="forbidden"):
        getattr(kubectl_command, operation)(Path("manifest.yaml"))

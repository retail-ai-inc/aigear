from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aigear.common import sh
from aigear.deploy.common import kubectl_command
from aigear.deploy.local import grpc_local_deploy
from aigear.deploy.gcp import grpc_gcp_deploy


@pytest.mark.parametrize("module,action", [(grpc_local_deploy, "deploy_local_grpc"), (grpc_gcp_deploy, "deploy_gcp_grpc")])
@pytest.mark.xfail(strict=True, reason="Known defect: context switching uses run_sh without check=True")
def test_failed_context_switch_never_applies_to_previous_cluster(monkeypatch, module, action):
    monkeypatch.setattr(sh.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b"credentials failed")))
    monkeypatch.setattr(grpc_gcp_deploy.AigearConfig, "get_config", lambda: SimpleNamespace(gcp=SimpleNamespace(
        gcp_project_id="project", location="region", kubernetes=SimpleNamespace(cluster_name="cluster"))))
    apply = Mock(return_value="created")
    monkeypatch.setattr(module, "kubectl_apply", apply)
    try:
        with pytest.raises(RuntimeError):
            getattr(module, action)(Path("manifest.yaml"))
    finally:
        apply.assert_not_called()


@pytest.mark.parametrize("operation", ["kubectl_apply", "kubectl_delete", "kubectl_status"])
@pytest.mark.xfail(strict=True, reason="Known defect: kubectl helpers do not propagate nonzero command exit codes")
def test_kubectl_command_failure_is_propagated(monkeypatch, operation):
    monkeypatch.setattr(sh.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=1, stdout=b"", stderr=b"forbidden")))
    with pytest.raises(RuntimeError, match="forbidden"):
        getattr(kubectl_command, operation)(Path("manifest.yaml"))

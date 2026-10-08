from unittest.mock import patch, call
import pytest
from aigear.infrastructure.gcp.kubernetes import KubernetesCluster


@patch("aigear.infrastructure.gcp.kubernetes.run_sh")
def test_update_calls_autoscaling_command(mock_run_sh):
    cluster = KubernetesCluster(
        cluster_name="my-cluster",
        zone="asia-northeast1",
        num_nodes=2,
        min_nodes=1,
        max_nodes=5,
        project_id="my-project",
    )
    cluster.update()
    autoscaling_call = call(
        [
            "gcloud", "container", "clusters", "update", "my-cluster",
            "--enable-autoscaling",
            "--min-nodes=1",
            "--max-nodes=5",
            "--region=asia-northeast1",
            "--node-pool=default-pool",
            "--project=my-project",
            "--quiet",
        ],
        check=True,
        timeout=600,
    )
    assert autoscaling_call in mock_run_sh.call_args_list


@patch("aigear.infrastructure.gcp.kubernetes.run_sh")
def test_update_calls_resize_command(mock_run_sh):
    cluster = KubernetesCluster(
        cluster_name="my-cluster",
        zone="asia-northeast1",
        num_nodes=2,
        min_nodes=1,
        max_nodes=5,
        project_id="my-project",
    )
    cluster.update()
    resize_call = call(
        [
            "gcloud", "container", "clusters", "resize", "my-cluster",
            "--num-nodes=2",
            "--region=asia-northeast1",
            "--node-pool=default-pool",
            "--project=my-project",
            "--async",
            "--quiet",
        ],
        check=True,
    )
    assert resize_call in mock_run_sh.call_args_list


@patch("aigear.infrastructure.gcp.kubernetes.run_sh")
def test_update_calls_autoscaling_before_resize(mock_run_sh):
    cluster = KubernetesCluster(
        cluster_name="my-cluster",
        zone="asia-northeast1",
        num_nodes=2,
        min_nodes=1,
        max_nodes=5,
        project_id="my-project",
    )
    cluster.update()
    assert mock_run_sh.call_count == 2
    first_cmd = mock_run_sh.call_args_list[0][0][0]
    assert first_cmd[3] == "update"
    second_cmd = mock_run_sh.call_args_list[1][0][0]
    assert second_cmd[3] == "resize"


@pytest.mark.parametrize("wait,timeout", [(False, 30), (True, 1800)])
@patch("aigear.infrastructure.gcp.kubernetes.run_sh")
def test_delete_waits_only_when_requested(mock_run_sh, wait, timeout):
    cluster = KubernetesCluster("my-cluster", "asia-northeast1", 2, 1, 5, "my-project")
    cluster.delete(wait=wait)
    command = mock_run_sh.call_args.args[0]
    assert ("--async" in command) is (not wait)
    assert mock_run_sh.call_args.kwargs == {"check": True, "timeout": timeout}


@patch("aigear.infrastructure.gcp.kubernetes.run_sh", side_effect=RuntimeError("execution timeout"))
def test_delete_propagates_timeout(mock_run_sh):
    cluster = KubernetesCluster("my-cluster", "asia-northeast1", 2, 1, 5, "my-project")
    with pytest.raises(RuntimeError, match="timeout"):
        cluster.delete(wait=True)


@patch("aigear.infrastructure.gcp.kubernetes.run_sh")
def test_delete_defaults_to_async(mock_run_sh):
    cluster = KubernetesCluster("my-cluster", "asia-northeast1", 2, 1, 5, "my-project")
    cluster.delete()
    assert "--async" in mock_run_sh.call_args.args[0]

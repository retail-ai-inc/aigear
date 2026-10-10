from unittest.mock import Mock
from types import SimpleNamespace

from google.api_core.exceptions import NotFound
import pytest

from aigear.infrastructure.gcp import pre_vm_image


@pytest.fixture
def image():
    return pre_vm_image.PreVMImage("project", "asia-northeast1-b")


def test_zone_input_normalizes_to_regional_fallbacks(image):
    assert image.fallback_zones == ["asia-northeast1-a", "asia-northeast1-b", "asia-northeast1-c"]


def test_empty_project_is_rejected():
    with pytest.raises(ValueError, match="project_id"):
        pre_vm_image.PreVMImage("", "asia-northeast1")


def test_subnet_not_ready_retries_same_zone_before_success(image, monkeypatch):
    client = Mock()
    client.insert.side_effect = [RuntimeError("subnetworks/default is not ready"), Mock()]
    monkeypatch.setattr(pre_vm_image.compute_v1, "InstancesClient", lambda: client)
    monkeypatch.setattr(image, "_wait_op", Mock())
    sleep = Mock()
    monkeypatch.setattr(pre_vm_image.time, "sleep", sleep)
    zone = image._create_instance_with_fallback(lambda zone: zone, "vm", "CPU")
    assert zone == "asia-northeast1-a"
    assert [call.kwargs["zone"] for call in client.insert.call_args_list] == [zone, zone]
    sleep.assert_called_once_with(30)


def test_stockout_moves_to_next_zone(image, monkeypatch):
    client = Mock()
    client.insert.side_effect = [RuntimeError("ZONE_RESOURCE_POOL_EXHAUSTED"), Mock()]
    monkeypatch.setattr(pre_vm_image.compute_v1, "InstancesClient", lambda: client)
    monkeypatch.setattr(image, "_wait_op", Mock())
    assert image._create_instance_with_fallback(lambda zone: zone, "vm", "GPU") == "asia-northeast1-b"
    assert client.insert.call_count == 2


def test_subnet_retry_budget_is_bounded_across_all_zones(image, monkeypatch):
    client = Mock()
    client.insert.side_effect = RuntimeError("subnetworks/default is not ready")
    monkeypatch.setattr(pre_vm_image.compute_v1, "InstancesClient", lambda: client)
    monkeypatch.setattr(pre_vm_image.time, "sleep", Mock())
    with pytest.raises(RuntimeError, match="All fallback zones exhausted"):
        image._create_instance_with_fallback(lambda zone: zone, "vm", "CPU")
    assert client.insert.call_count == 15
    assert pre_vm_image.time.sleep.call_count == 12


@pytest.mark.parametrize("failure", [NotFound("source image missing"), RuntimeError("permission denied")])
def test_non_capacity_error_is_not_retried(image, monkeypatch, failure):
    client = Mock()
    client.insert.side_effect = failure
    monkeypatch.setattr(pre_vm_image.compute_v1, "InstancesClient", lambda: client)
    with pytest.raises(type(failure), match=str(failure)):
        image._create_instance_with_fallback(lambda zone: zone, "vm", "CPU")
    assert client.insert.call_count == 1


@pytest.mark.parametrize("output,success", [("BAKE_DONE:OK", True), ("BAKE_DONE:FAILED", False), ("startup-script failed", False)])
def test_bake_markers_report_outcome(image, monkeypatch, output, success):
    monkeypatch.setattr(image, "_get_serial_output", lambda *args, **kwargs: (output, 10))
    if success:
        image._wait_bake_done("vm")
    else:
        with pytest.raises(RuntimeError):
            image._wait_bake_done("vm")


def test_bake_timeout_is_reported(image, monkeypatch):
    monkeypatch.setattr(pre_vm_image, "time", SimpleNamespace(time=Mock(side_effect=[0, 2000])))
    with pytest.raises(RuntimeError, match="Bake did not finish"):
        image._wait_bake_done("vm")


def test_operation_failure_is_not_treated_as_success(image):
    operation = Mock(error_code=403, error_message="forbidden")
    with pytest.raises(RuntimeError, match="forbidden"):
        image._wait_op(operation, "create image")
    operation.result.assert_called_once_with(timeout=600)


def test_existing_images_skip_baking(image, monkeypatch):
    monkeypatch.setattr(image, "gpu_image_exists", lambda: True)
    monkeypatch.setattr(image, "cpu_image_exists", lambda: True)
    gpu = Mock()
    cpu = Mock()
    monkeypatch.setattr(image, "create_gpu_image", gpu)
    monkeypatch.setattr(image, "create_cpu_image", cpu)
    image.create_all_images()
    gpu.assert_not_called()
    cpu.assert_not_called()
    image.create_all_images(skip_existing=False)
    gpu.assert_called_once()
    cpu.assert_called_once()

from contextlib import nullcontext
import importlib
from types import SimpleNamespace
from unittest.mock import Mock

from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct
import pytest

from aigear.service.grpc.protos import grpc_pb2

service = importlib.import_module("aigear.service.grpc.grpc_service")


def test_predict_converts_protobuf_request_and_response():
    model = Mock()
    model.predict.return_value = [1, 0]
    request = Struct()
    request.update({"features": [2.0, 3.0]})
    response = service.MLServicer(model).Predict(grpc_pb2.MLRequest(request=request), Mock())
    model.predict.assert_called_once_with({"features": [2.0, 3.0]})
    assert MessageToDict(response) == {"response": {"response": [1.0, 0.0]}}


def test_predict_does_not_swallow_model_error():
    model = Mock()
    model.predict.side_effect = ValueError("bad features")
    with pytest.raises(ValueError, match="bad features"):
        service.MLServicer(model).Predict(grpc_pb2.MLRequest(), Mock())


def test_server_converts_keepalive_seconds_and_registers_health(monkeypatch):
    server = Mock()
    factory = Mock(return_value=server)
    register_health = Mock()
    wait = Mock()
    monkeypatch.setattr(service.grpc, "server", factory)
    monkeypatch.setattr(service, "ServerInterceptor", Mock())
    monkeypatch.setattr(service.grpc_pb2_grpc, "add_MLServicer_to_server", Mock())
    monkeypatch.setattr(service.health_pb2_grpc, "add_HealthServicer_to_server", register_health)
    monkeypatch.setattr(service.grpc_features, "wait_until_closed", wait)
    service._run_server("0.0.0.0:50051", Mock(), {"keep_alive": {"time": 60, "timeout": 5}, "multi_processing": {"thread_count": 3}})
    options = dict(factory.call_args.kwargs["options"])
    assert options["grpc.keepalive_time_ms"] == 60000
    assert options["grpc.keepalive_timeout_ms"] == 5000
    register_health.assert_called_once()
    server.add_insecure_port.assert_called_once_with("0.0.0.0:50051")
    server.start.assert_called_once()
    wait.assert_called_once_with(server)
    factory.call_args.kwargs["thread_pool"].shutdown()


@pytest.fixture
def runtime(monkeypatch):
    config = {"model_service": {"grpc": {"multi_processing": {"on": True, "process_count": 3}, "port": "6000", "sentry": {"on": False}}}}
    monkeypatch.setattr(service.PipelinesConfig, "get_version_config", lambda _version: config)
    monkeypatch.setattr(service, "get_environment", lambda: "staging")
    monkeypatch.setattr(service.thread_config, "ml_thread_scope", lambda _enabled: nullcontext())
    model = Mock()
    loader = Mock()
    loader.return_value.load_module.return_value = Mock(return_value=model)
    monkeypatch.setattr(service, "LoadModule", loader)
    return config, model, loader


def test_windows_falls_back_to_single_process(runtime, monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    run = Mock()
    process = Mock()
    monkeypatch.setattr(service, "_run_server", run)
    monkeypatch.setattr(service.multiprocessing, "Process", process)
    service.grpc_service("v1", "pkg.ModelService")
    run.assert_called_once_with("0.0.0.0:6000", runtime[1], runtime[0]["model_service"]["grpc"])
    process.assert_not_called()


def test_linux_starts_configured_workers_with_shared_model(runtime, monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    monkeypatch.setattr(service.gc, "freeze", Mock())
    monkeypatch.setattr(service.grpc_features, "reserve_port", lambda port: nullcontext(port))
    workers = [Mock() for _ in range(3)]
    factory = Mock(side_effect=workers)
    monkeypatch.setattr(service.multiprocessing, "Process", factory)
    service.grpc_service("v1", "pkg.ModelService")
    assert factory.call_count == 3
    assert factory.call_args.kwargs["args"][0] == "0.0.0.0:6000"
    runtime[1].model.share_memory.assert_called_once()
    for worker in workers:
        worker.start.assert_called_once()
        worker.join.assert_called_once()


def test_missing_model_module_does_not_start_server(runtime, monkeypatch):
    runtime[2].return_value.load_module.return_value = None
    run = Mock()
    monkeypatch.setattr(service, "_run_server", run)
    service.grpc_service("v1", "pkg.MissingModel")
    run.assert_not_called()


def test_sentry_receives_configured_environment(runtime, monkeypatch):
    grpc_config = runtime[0]["model_service"]["grpc"]
    grpc_config["sentry"] = {"on": True, "dsn": "https://example", "traces_sample_rate": 0.25}
    monkeypatch.setattr(service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(service, "_run_server", Mock())
    init = Mock()
    monkeypatch.setattr(service, "sentry_init", init)
    service.grpc_service("v1", "pkg.ModelService")
    init.assert_called_once_with(dsn="https://example", traces_sample_rate=0.25, environment="staging")

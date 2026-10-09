import sys
from types import SimpleNamespace
from unittest.mock import Mock

from aigear.service.grpc.grpc_package import thread_config


def test_thread_env_limits_are_applied_before_model_loading(monkeypatch):
    keys = ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"]
    for key in keys:
        monkeypatch.setenv(key, "8")
    configure = Mock()
    monkeypatch.setattr(thread_config, "configure_framework_threads", configure)
    with thread_config.ml_thread_scope(True):
        assert all(thread_config.os.environ[key] == thread_config._N for key in keys)
        configure.assert_not_called()
    configure.assert_called_once()


def test_disabled_thread_scope_does_not_change_limits(monkeypatch):
    set_env = Mock()
    configure = Mock()
    monkeypatch.setattr(thread_config, "set_ml_thread_env_vars", set_env)
    monkeypatch.setattr(thread_config, "configure_framework_threads", configure)
    with thread_config.ml_thread_scope(False):
        pass
    set_env.assert_not_called()
    configure.assert_not_called()


def test_framework_thread_limits_are_applied_to_loaded_frameworks(monkeypatch):
    torch = SimpleNamespace(set_num_threads=Mock(), set_num_interop_threads=Mock())
    threading = SimpleNamespace(set_intra_op_parallelism_threads=Mock(), set_inter_op_parallelism_threads=Mock())
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "tensorflow", SimpleNamespace(config=SimpleNamespace(threading=threading)))
    thread_config.configure_framework_threads("2")
    torch.set_num_threads.assert_called_once_with(2)
    torch.set_num_interop_threads.assert_called_once_with(2)
    threading.set_intra_op_parallelism_threads.assert_called_once_with(2)
    threading.set_inter_op_parallelism_threads.assert_called_once_with(2)

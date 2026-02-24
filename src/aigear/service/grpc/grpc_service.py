import sys
import grpc
import json
import platform
import multiprocessing
from pathlib import Path
from typing import Type
from concurrent import futures
from grpc_health.v1 import health
from google.protobuf import struct_pb2
from sentry_sdk import init as sentry_init
from grpc_health.v1 import health_pb2_grpc
from google.protobuf.json_format import MessageToDict
from sentry_sdk.integrations.grpc.server import ServerInterceptor
from aigear.service.grpc.protos import grpc_pb2
from aigear.service.grpc.protos import grpc_pb2_grpc
from aigear.service.grpc.grpc_package import grpc_ml_module, grpc_features
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class MLServicer(grpc_pb2_grpc.MLServicer):
    def __init__(self, model_instance):
        self.model_service = model_instance

    def Predict(self, request, context):
        logger.info("Predict function called:")
        request = MessageToDict(request).get('request', {})
        logger.info(f"Model input: {request}.")
        model_out = self.model_service.predict(request)
        logger.info(f"Model output: {model_out}.")
        response_data = struct_pb2.Struct()
        response_data.update({"response": model_out})
        return grpc_pb2.MLResponse(response=response_data)


def _run_server(bind_address: str, model_instance: Type, grpc_options: dict):
    """Start a server in a subprocess."""
    logger.info("Starting new server.")

    options = [
        ("grpc.so_reuseport", 1),  # Non blocking settings
    ]
    # Add keepalive
    keepalive_time = grpc_options.get("keep_alive", {}).get("time")
    keepalive_timeout = grpc_options.get("keep_alive", {}).get("timeout")
    if keepalive_time and keepalive_timeout:
        options.extend(
            [
                ("grpc.keepalive_time_ms", keepalive_time * 1000),
                # send keepalive ping every x second, default is 2 hours
                ("grpc.keepalive_timeout_ms", keepalive_timeout * 1000),
                # keepalive ping time out after x seconds, default is 20 seconds
                ("grpc.keepalive_permit_without_calls", True),  # allow keepalive pings when there are no gRPC calls
            ]
        )
    logger.info(f"gRPC has added Keepalive. interval time: {keepalive_time}s, timeout: {keepalive_timeout}s.")

    max_workers = grpc_options.get("multi_processing", {}).get("thread_count", 5)
    logger.info(f"Enable thread count: {max_workers}.")
    server = grpc.server(
        thread_pool=futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=[ServerInterceptor()],
        options=options,
    )
    grpc_pb2_grpc.add_MLServicer_to_server(MLServicer(model_instance), server)

    # health check service - add this service to server
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    server.add_insecure_port(bind_address)
    server.start()
    grpc_features.wait_until_closed(server)


def get_env_variables(grpc_directory: Path):
    env_path = grpc_directory / "env.json"
    with open(env_path, "r") as f:
        env_cog = json.load(f)
        grpc_cog = env_cog.get("grpc")
        return grpc_cog


def grpc_service(model_class_path):
    logger.info(f"Model class path:'{model_class_path}'")
    # load ml module
    model_class = grpc_ml_module.MLModule(model_class_path).load_module()
    logger.info(f"gRPC load module: {model_class_path}...")
    if model_class is None:
        logger.error("model module instance fail!!!!!!")
        return
    logger.info("gRPC load module successfully.")

    # Get environment variables
    grpc_directory = Path.cwd()
    env_variables = get_env_variables(grpc_directory)
    if env_variables is None:
        logger.error(f"`env.json` not found in grpc directory({grpc_directory})")
        return
    logger.info(f"Environment variables: {env_variables}")

    # Enable Sentry
    sentry_cog = env_variables.get("sentry", {})
    sentry_enable = sentry_cog.get("on")
    logger.info(f"Enable Sentry: {sentry_enable}")
    if sentry_enable:
        sentry_init(
            dsn=sentry_cog.get("dsn"),
            traces_sample_rate=sentry_cog.get("traces_sample_rate"),
            environment=env_variables.get("environment"),
        )

    # model instance
    model_instance = model_class(model_path_dict=env_variables.get("model_path"))

    # grpc
    is_windows = platform.system().lower() == "windows"
    process_switch = env_variables.get("multi_processing", {}).get("on", False)
    port = int(env_variables.get("port", "50051"))
    service_host = env_variables.get("service_host", "0.0.0.0")
    if process_switch and not is_windows:
        with grpc_features.reserve_port(port) as grpc_port:
            bind_address = f"{service_host}:{grpc_port}"
            sys.stdout.flush()
            # multiprocessing
            workers = []
            process_count = env_variables.get("multi_processing", {}).get("process_count", 1)
            for _ in range(process_count):
                worker = multiprocessing.Process(
                    target=_run_server,
                    args=(bind_address, model_instance, env_variables,)
                )
                worker.start()
                workers.append(worker)
            for worker in workers:
                worker.join()
    else:
        bind_address = f"{service_host}:{port}"
        _run_server(bind_address, model_instance, env_variables)


if __name__ == '__main__':
    grpc_service(model_class_path="")

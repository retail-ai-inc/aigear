import multiprocessing
import platform
import sys
from concurrent import futures
from typing import Type

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from grpc_health.v1 import health, health_pb2_grpc
from sentry_sdk import init as sentry_init
from sentry_sdk.integrations.grpc.server import ServerInterceptor

from aigear.common.config import PipelinesConfig, get_environment
from aigear.common.loading_module import LoadModule
from aigear.common.logger import Logging
from aigear.service.grpc.grpc_package import grpc_features, thread_config
from aigear.service.grpc.protos import grpc_pb2, grpc_pb2_grpc

logger = Logging(log_name=__name__).console_logging()
thread_config.configure_frameworks()


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


def grpc_service(pipeline_version, model_class_path):
    # load ml module
    logger.info(f"gRPC load module: {model_class_path}...")
    model_class = LoadModule(model_class_path).load_module()
    if model_class is None:
        logger.error("model module instance fail!!!!!!")
        return
    model_instance = model_class()
    logger.info("gRPC load module successfully.")

    # Get environment variables
    environment = get_environment()
    pipeline_version_config = PipelinesConfig.get_version_config(pipeline_version)
    if pipeline_version_config is None:
        logger.error(f"No pipeline_version({pipeline_version}) config found in `env.json`.")
        return
    logger.info(f"Environment variables: {pipeline_version_config}")
    release_config = pipeline_version_config.get("model_service", {})

    # Release switch
    release_switch = release_config.get("release", False)
    if not release_switch:
        logger.info(f"The Release parameter for pipeline_version({pipeline_version}) is not turned on.")
        return

    grpc_config = release_config.get("grpc", {})
    # Enable Sentry
    sentry_cog = grpc_config.get("sentry", {})
    sentry_enable = sentry_cog.get("on")
    logger.info(f"Enable Sentry: {sentry_enable}")
    if sentry_enable:
        sentry_init(
            dsn=sentry_cog.get("dsn"),
            traces_sample_rate=sentry_cog.get("traces_sample_rate"),
            environment=environment,
        )

    # grpc
    is_windows = platform.system().lower() == "windows"
    multi_processing = grpc_config.get("multi_processing", {})
    process_switch = multi_processing.get("on", False)
    port = int(grpc_config.get("port", "50051"))
    service_host = grpc_config.get("service_host", "0.0.0.0")
    if process_switch and not is_windows:
        with grpc_features.reserve_port(port) as grpc_port:
            bind_address = f"{service_host}:{grpc_port}"
            sys.stdout.flush()
            # multiprocessing
            workers = []
            process_count = multi_processing.get("process_count", 2)
            for _ in range(process_count):
                worker = multiprocessing.Process(
                    target=_run_server,
                    args=(bind_address, model_instance, grpc_config,)
                )
                worker.start()
                workers.append(worker)
            for worker in workers:
                worker.join()
    else:
        bind_address = f"{service_host}:{port}"
        _run_server(bind_address, model_instance, grpc_config)


if __name__ == '__main__':
    grpc_service(pipeline_version="", model_class_path="")

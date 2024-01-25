from concurrent import futures
import grpc
import json
from aigear.proto import grpc_pb2, grpc_pb2_grpc
from grpc_health.v1 import health
from grpc_health.v1 import health_pb2_grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from sentry_sdk import init as sentry_init
from sentry_sdk.integrations.grpc.server import ServerInterceptor
from aigear.grpc_package import grpc_ml_module, grpc_log, grpc_features
import multiprocessing
import sys
import os

# logger setting
logger = grpc_log.GrpcLog().grpc_log()


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


def _run_server(bind_address: str, model_instance: callable, thread_count: int):
    """Start a server in a subprocess."""
    logger.info("Starting new server.")

    # Non blocking settings
    options = [
        ("grpc.so_reuseport", 1),
    ]
    server = grpc.server(
        thread_pool=futures.ThreadPoolExecutor(max_workers=thread_count),
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


def get_env_variables(tag: str):
    with open("env.json", "r") as f:
        env = json.load(f)

    env_variables = {
        'modelPath': env.get("grpc", {}).get("servers", {}).get(tag, {}).get("modelPath"),
        'serviceHost': env.get("grpc", {}).get("servers", {}).get(tag, {}).get("serviceHost"),
        'port': env.get("grpc", {}).get("servers", {}).get(tag, {}).get("port"),
        'sentryEnable': env.get("grpc", {}).get("sentry", {}).get("on", False),
        'sentryDSN': env.get("grpc", {}).get("sentry", {}).get("dsn", ""),
        'tracesSampleRate': env.get("grpc", {}).get("sentry", {}).get("tracesSampleRate", 1.0),
        'environment': env.get("environment"),
    }
    is_multi_process = env.get("grpc", {}).get(
        "servers", {}).get(tag, {}).get("multiProcessing", {}).get("on", False)
    process_count = env.get("grpc", {}).get(
        "servers", {}).get(tag, {}).get("multiProcessing", {}).get("processCount")
    env_variables['processCount'] = (is_multi_process and process_count) or 1
    env_variables['threadCount'] = env.get("grpc", {}).get(
        "servers", {}).get(tag, {}).get("multiProcessing", {}).get("threadCount")

    return env_variables


def check_env_variables(env_variables: dict):
    run_or_not = True
    for env_variable_name in env_variables:
        if env_variables[env_variable_name] == None:
            logger.error(
                f"{env_variable_name} not found in the env variables!")
            run_or_not = False
            break

    return run_or_not


def main():
    sys.path.append(os.getcwd())
    tag = grpc_features.get_argument()
    # Check Arg parameters
    if tag == "":
        logger.error("Miss tag code in command!")

    # Get environment variables
    env_variables = get_env_variables(tag)

    # install ml package and load ml module
    ml_module_instance = grpc_ml_module.MLModule(tag)
    model_class = ml_module_instance.load_module()
    if model_class is None:
        logger.error("Model file not found!!!!!!")
    else:
        logger.error(f"gRPC load module: {ml_module_instance.ml_module_path}")

    # Enable Sentry
    if env_variables['sentryEnable']:
        sentry_init(
            dsn=env_variables['sentryDSN'],
            traces_sample_rate=env_variables['tracesSampleRate'],
            environment=env_variables['environment'],
        )
    logger.info(f"Enable Sentry: {env_variables['sentryEnable']}")

    # Check environment variables
    not_miss_variables = check_env_variables(env_variables)
    # Check Arg parameters
    if not_miss_variables:
        model_instance = model_class(model_path=env_variables['modelPath'])
        with grpc_features.reserve_port(int(env_variables['port'])) as grpc_port:
            bind_address = f"{env_variables['serviceHost']}:{grpc_port}"
            thread_count = env_variables['threadCount']
            # multiprocessing
            sys.stdout.flush()
            workers = []
            for _ in range(env_variables['processCount']):
                worker = multiprocessing.Process(target=_run_server, args=(bind_address, model_instance, thread_count,))
                worker.start()
                workers.append(worker)
            for worker in workers:
                worker.join()
    else:
        logger.error("Missing necessary environment variables in env.json!!!!!!")


if __name__ == '__main__':
    # Start Service
    main()

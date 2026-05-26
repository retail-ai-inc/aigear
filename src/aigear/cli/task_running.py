import argparse
import sys
import os
from aigear.common.config import AigearConfig, PipelinesConfig
from aigear.common.lifecycle_log import emit_lifecycle_log
from aigear.common.logger import Logging
from aigear.common.loading_module import LoadModule
from aigear.common.run_log_context import RunLogContext
from aigear.service.grpc.grpc_service import grpc_service


def run_workflow(pipeline_version: str, step_name: str) -> None:
    logger = Logging(log_name=__name__).console_logging()
    pipeline_config = PipelinesConfig.get_version_config(pipeline_version)
    if pipeline_config is None:
        logger.error(f"No config found for version: {pipeline_version}")
        return
    module_path = pipeline_config.get(step_name, {}).get("pipeline_step")
    if not module_path:
        logger.error(
            f"No pipeline_step found for step '{step_name}' in version '{pipeline_version}'"
        )
        return

    aigear_config = AigearConfig.get_config()
    ctx = RunLogContext.install_from_env(
        gcp_logging=aigear_config.gcp.logging,
        project_id=aigear_config.gcp.gcp_project_id,
    )
    task_logger = Logging(log_name=__name__).for_task()
    try:
        if ctx:
            emit_lifecycle_log("pipeline_step_started", ctx)
        function_module = LoadModule(module_path).load_module()
        function_module(pipeline_version=pipeline_version)
        if ctx:
            emit_lifecycle_log("pipeline_step_finished", ctx)
    except Exception as e:
        if ctx:
            emit_lifecycle_log("pipeline_step_failed", ctx)
        task_logger.error(f"Error while executing {module_path}: {e}")
    finally:
        RunLogContext.clear()


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    workflow_parser = subparsers.add_parser(
        "workflow", help="Run a pipeline step function"
    )
    workflow_parser.add_argument(
        "--version", default="", help="Version of the pipeline"
    )
    workflow_parser.add_argument(
        "--step",
        default="",
        help="Step name (e.g. fetch_data). "
        "The full module path is looked up from env.json.",
    )

    grpc_parser = subparsers.add_parser("grpc", help="Run a gRPC model service")
    grpc_parser.add_argument("--version", default="", help="Version of the pipeline")

    return parser.parse_args()


def task_run() -> None:
    project_root = os.getcwd()
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    args = get_argument()
    if args.subcommand == "workflow":
        run_workflow(args.version, args.step)
    elif args.subcommand == "grpc":
        pipeline_config = PipelinesConfig.get_version_config(args.version)
        model_class_path = (
            pipeline_config.get("model_service", {}).get("model_class_path")
            if pipeline_config
            else None
        )
        grpc_service(args.version, model_class_path)

import logging
import argparse
import sys
import os
from aigear.common.loading_module import LoadModule
from aigear.service.grpc.grpc_service import grpc_service


def run_workflow(pipeline_version, module_path):
    function_module = LoadModule(module_path).load_module()
    try:
        function_module(
            pipeline_version=pipeline_version
        )
    except Exception as e:
        logging.error(f"Error while executing {module_path}: {e}")


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    workflow_parser = subparsers.add_parser("workflow", help="Run a pipeline step function")
    grpc_parser = subparsers.add_parser("grpc", help="Run a gRPC model service")

    for sub in (workflow_parser, grpc_parser):
        sub.add_argument("--version", default="", help="Version of the pipeline")
        sub.add_argument("--module", default="",
                         help="Module path to the function (workflow) or class (grpc) to load, "
                              "e.g. package.module.my_func or package.module.MyClass")

    return parser.parse_args()


def task_run():
    project_root = os.getcwd()
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    args = get_argument()
    if args.subcommand == "workflow":
        run_workflow(args.version, args.module)
    elif args.subcommand == "grpc":
        grpc_service(args.version, args.module)

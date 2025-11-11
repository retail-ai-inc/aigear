import importlib
import logging
import argparse
import sys
import os


def run_step(pipeline_version, step):
    project_root = os.getcwd()
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    module_name = f"src.pipelines.{step}"
    try:
        module = importlib.import_module(module_name)
        module.main(pipeline_version)
    except Exception as e:
        logging.error(f"Error while executing {module_name}: {e}")

def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default="",
                        help='Version of the pipeline')
    parser.add_argument('--step', default="",
                        help='Name of the pipeline step')
    args = parser.parse_args()
    return args

def task_run():
    args = get_argument()
    run_step(args.version, args.step)

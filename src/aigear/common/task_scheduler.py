import importlib
import logging
import argparse
import os


def run_step(pipeline_version, step_name):
    os.makedirs(f'output/{pipeline_version}', exist_ok=True)
    module_name = f"pipelines.{pipeline_version}.{step_name}"
    try:
        module = importlib.import_module(module_name)
        module.main(pipeline_version)
    except Exception as e:
        logging.error(f"Error while executing {module_name}: {e}")


def task_run():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default="",
                        help='Version of the pipeline')
    parser.add_argument('--step', default="",
                        help='Name of the pipeline step')
    args = parser.parse_args()
    run_step(args.version, args.step)

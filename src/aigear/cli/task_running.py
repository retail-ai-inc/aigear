import importlib
import logging
import argparse
import os

def run_step(pipeline_version, step):
    os.makedirs(f'output/{pipeline_version}', exist_ok=True)
    module_name = f"pipelines.{step}"
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

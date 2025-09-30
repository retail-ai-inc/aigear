import importlib
import argparse
import os
from ..common.logger import create_stage_logger, PipelineStage

# Use preprocessing stage logger for task execution
task_logger = create_stage_logger(
    stage=PipelineStage.PREPROCESSING,
    module_name=__name__,
    cpu_count=1,
    memory_limit="1GB",
    enable_cloud_logging=False
)

def run_step(pipeline_version, step):
    with task_logger.stage_context() as logger:
        logger.info(f"Starting pipeline step: {step} (version: {pipeline_version})")
        os.makedirs(f'output/{pipeline_version}', exist_ok=True)
        module_name = f"pipelines.{step}"
        try:
            logger.info(f"Loading module: {module_name}")
            module = importlib.import_module(module_name)
            module.main(pipeline_version)
            logger.info(f"Pipeline step {step} completed successfully")
        except Exception as e:
            logger.error(f"Error while executing {module_name}: {e}")
            raise

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

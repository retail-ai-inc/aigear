import os
import json
import argparse
from aigear.deploy.gcp import Scheduler
from aigear.common.config import read_config
from aigear.common.logger import create_stage_logger, PipelineStage

# Use deployment stage logger for scheduler CLI operations
scheduler_cli_logger = create_stage_logger(
    stage=PipelineStage.DEPLOYMENT,
    module_name=__name__,
    cpu_count=1,
    memory_limit="1GB",
    enable_cloud_logging=False
)


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default="",
                        help='Version of the pipeline')
    parser.add_argument('--step_names', default="",
                        help='Name of the pipeline step')
    args = parser.parse_args()
    return args


def read_pipelines_config():
    with scheduler_cli_logger.stage_context() as logger:
        pipelines_config_path = os.path.join(os.getcwd(), "env.json")
        logger.info(f"Reading pipeline configuration from: {pipelines_config_path}")
        with open(pipelines_config_path, "r", encoding="utf-8") as f:
            pipelines_config = json.load(f)
        logger.info("Pipeline configuration loaded successfully")
        return pipelines_config


def scheduler_init():
    with scheduler_cli_logger.stage_context() as logger:
        logger.info("Starting scheduler initialization")

        aigear_config = read_config(env_path=os.path.join(os.getcwd(), "env.json"))
        pipelines_config = read_pipelines_config()
        args = get_argument()

        logger.info(f"Processing pipeline version: {args.version}")
        pipeline_config = pipelines_config.get("pipelines", {}).get(args.version, {})

        step_names = args.step_names.split(",")
        logger.info(f"Processing pipeline steps: {step_names}")

        scheduler_messages = []
        for step_name in step_names:
            step_config = pipeline_config.get(step_name, {})
            resources = step_config.get("resources", {})
            task_run_parameters = step_config.get("task_run_parameters", {})
            message = {**resources, **task_run_parameters}
            scheduler_messages.append(message)
            logger.info(f"Configured step {step_name} with resources and parameters")

        scheduler_config = pipeline_config.get("scheduler", {})
        scheduler_name = scheduler_config.get("name")
        scheduler_location = scheduler_config.get("location")
        scheduler_schedule = scheduler_config.get("schedule")
        scheduler_time_zone = scheduler_config.get("time_zone")

        logger.info(f"Creating scheduler: {scheduler_name} in {scheduler_location}")
        scheduler = Scheduler(
            name=scheduler_name,
            location=scheduler_location,
            schedule=scheduler_schedule,
            topic_name=aigear_config.gcp.pub_sub.topic_name,
            message=scheduler_messages,
            time_zone=scheduler_time_zone,
        )

        is_exist = scheduler.describe()
        logger.info(f"Scheduler exists: {is_exist}")
        print(is_exist)

        if not is_exist:
            logger.info("Creating new scheduler")
            scheduler.create()
            logger.info("Scheduler creation completed")
        else:
            logger.info("Scheduler already exists, skipping creation")

        logger.info("Scheduler initialization completed")

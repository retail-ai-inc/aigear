import os
import json
import argparse
from aigear.deploy.gcp import Scheduler
from aigear.common.config import read_config


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
    pipelines_config_path = os.path.join(os.getcwd(), "env.json")
    with open(pipelines_config_path, "r", encoding="utf-8") as f:
        pipelines_config = json.load(f)
    return pipelines_config


def scheduler_init():
    aigear_config = read_config(env_path=os.path.join(os.getcwd(), "env.json"))
    pipelines_config = read_pipelines_config()
    args = get_argument()
    pipeline_config = pipelines_config.get("pipelines", {}).get(args.version, {})

    step_names = args.step_names.split(",")
    scheduler_messages = []
    for step_name in step_names:
        step_config = pipeline_config.get(step_name, {})
        resources = step_config.get("resources", {})
        task_run_parameters = step_config.get("task_run_parameters", {})
        message = {**resources, **task_run_parameters}
        scheduler_messages.append(message)

    scheduler_config = pipeline_config.get("scheduler", {})
    scheduler_name = scheduler_config.get("name")
    scheduler_location = scheduler_config.get("location")
    scheduler_schedule = scheduler_config.get("schedule")
    scheduler_time_zone = scheduler_config.get("time_zone")
    scheduler = Scheduler(
        name=scheduler_name,
        location=scheduler_location,
        schedule=scheduler_schedule,
        topic_name=aigear_config.gcp.pub_sub.topic_name,
        message=scheduler_messages,
        time_zone=scheduler_time_zone,
    )
    is_exist = scheduler.describe()
    print(is_exist)
    if not is_exist:
        scheduler.create()

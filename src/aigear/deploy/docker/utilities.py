import os
import re
from pathlib import Path
from ...common.logger import logger


def read_workdir_from_dockerfile(dockerfile_path):
    workdir_pattern = re.compile(r'^\s*WORKDIR\s+(.+)$', re.IGNORECASE)
    with open(dockerfile_path, 'r') as file:
        for line in file:
            line = line.split('#', 1)[0].strip()
            match = workdir_pattern.match(line)
            if match:
                return match.group(1).strip()


def flow_path_in_workdir(absolute_path: str):
    flow_name = Path(absolute_path).name
    directory = Path(absolute_path).parent
    dockerfile_path = os.path.join(directory, "Dockerfile")
    workdir = read_workdir_from_dockerfile(dockerfile_path)
    if not workdir:
        logger.error(f"`WORKDIR` not found from {dockerfile_path}.")
    pipeline_path = os.path.join(workdir, flow_name).replace("\\", "/")
    return pipeline_path

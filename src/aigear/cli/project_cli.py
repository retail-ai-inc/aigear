import argparse
from ..project import Project
from ..constant import (
    PROJECT_NAME,
    PROJECT_VERSION,
    PROJECT_DESCRIBE,
)


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--name', default=PROJECT_NAME,
                        help='Project name.')
    parser.add_argument('--version', default=PROJECT_VERSION,
                        help='Project version.')
    parser.add_argument('--describe', default=PROJECT_DESCRIBE,
                        help='Project describe.')
    args = parser.parse_args()
    return args.tag


def project_init():
    args = get_argument()
    project = Project(name=args.name, version=args.version, describe=args.describe)
    project.init()

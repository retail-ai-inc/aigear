import argparse
from aigear.project import Project
from aigear.constant import (
    PROJECT_NAME,
    PROJECT_VERSION,
)


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--name', default=PROJECT_NAME,
                        help='Project name.')
    parser.add_argument('--version', default=PROJECT_VERSION,
                        help='Project version.')
    args = parser.parse_args()
    return args.tag


def project_init():
    args = get_argument()
    project = Project(name=args.name, version=args.version)
    project.init()

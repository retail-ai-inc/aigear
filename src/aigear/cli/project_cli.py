import argparse
from ..project import Project


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="template_project",
                        help="Project name.")
    args = parser.parse_args()
    return args.tag


def project_init():
    args = get_argument()
    project = Project(name=args.name)
    project.init()

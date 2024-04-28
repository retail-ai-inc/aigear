import argparse
from aigear.project import Project


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--name', default="template_project",
                        help='Project name.')
    parser.add_argument('--version', default="0.0.1",
                        help='Project version.')
    args = parser.parse_args()
    return args.tag


def project_init():
    args = get_argument()
    project = Project(name=args.name, version=args.version)
    project.init()

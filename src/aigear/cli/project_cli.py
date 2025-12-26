import argparse
from ..project import Project


def get_argument():
    parser = argparse.ArgumentParser(
        description="Initialize a new aigear project with pipelines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a basic project (with default pipeline_version_1)
  aigear-init --name my_project

  # Create project with specific pipelines
  aigear-init --name my_project --pipeline pipeline_version_v1,pipeline_version_v2
        """
    )

    parser.add_argument(
        "--name",
        default="template_project",
        help="Project name"
    )

    parser.add_argument(
        "--pipeline",
        type=str,
        help="Comma-separated list of pipeline names (e.g., pipeline_v1,pipeline_v2). If not provided, will use pipeline_version_1"
    )

    args = parser.parse_args()
    return args


def project_init():
    args = get_argument()

    # Parse pipelines
    pipelines = None
    if args.pipeline:
        pipelines = [p.strip() for p in args.pipeline.split(',')]

    # Create project
    project = Project(
        name=args.name,
        pipelines=pipelines
    )

    project.init()

import argparse
from ..project import Project


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", default="template_project", help="Project name.")
    parser.add_argument(
        "--pipeline_versions",
        default="pipeline_version_1",
        type=str,
        help="Comma-separated list of pipeline names (e.g., pipeline_v1,pipeline_v2). If not provided, will use pipeline_version_1",
    )
    args = parser.parse_args()
    return args


def project_init() -> None:
    args = get_argument()
    # Parse pipelines
    pipeline_versions = [p.strip() for p in args.pipeline_versions.split(",")]

    project = Project(name=args.name, pipeline_versions=pipeline_versions)
    project.init()

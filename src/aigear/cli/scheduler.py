import argparse
from aigear.deploy.gcp.scheduler import create_scheduler
from aigear.deploy.gcp.artifacts_image import create_artifacts_image
from aigear.cli.artifacts_image import add_artifacts_args


def add_scheduler_args(parser: argparse.ArgumentParser):
    parser.add_argument('--version', default="",
                        help='Version of the pipeline')
    parser.add_argument('--step_names', default="",
                        help='Name of the pipeline step')
    return parser

def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser = add_scheduler_args(parser)
    parser = add_artifacts_args(parser)
    args = parser.parse_args()
    return args

def scheduler_init():
    args = get_argument()
    step_names = args.step_names.split(",")
    
    # Create artifacts image before creating scheduler
    create_artifacts_image(
        dockerfile_path=args.dockerfile_path,
        force=args.force,
        image_name=args.image_name,
        image_version = args.image_version
    )
    
    create_scheduler(args.version, step_names)

import argparse
from aigear.deploy.gcp.scheduler import create_scheduler
from aigear.deploy.gcp.artifacts_image import create_artifacts_image


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default="",
                        help='Version of the pipeline')
    parser.add_argument('--step_names', default="",
                        help='Name of the pipeline step')
    parser.add_argument('--dockerfile_path', default=".",
                        help='Path of dockerfile')
    parser.add_argument('--force', action='store_true',
                        help='Force recreate image even if it already exists')
    args = parser.parse_args()
    return args

def scheduler_init():
    args = get_argument()
    step_names = args.step_names.split(",")
    
    # Create artifacts image before creating scheduler
    create_artifacts_image(dockerfile_path=args.dockerfile_path, force=args.force)
    
    create_scheduler(args.version, step_names)

import argparse
from aigear.deploy.gcp.scheduler import create_scheduler


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

def scheduler_init():
    args = get_argument()
    step_names = args.step_names.split(",")
    create_scheduler(args.version, step_names)

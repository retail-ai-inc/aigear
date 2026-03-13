import argparse
from aigear.common.config import EnvConfig


def get_argument():
    # Arg
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--force', action='store_true',
                        help='force recreate image even if it already exists')
    args = parser.parse_args()
    return args

def auto_generate_env_schema():
    args = get_argument()
    EnvConfig.generative_env_schema(
        forced_generate=args.force,
    )


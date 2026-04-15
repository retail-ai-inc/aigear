import argparse
from aigear.common.config import EnvConfig


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate",
        action="store_true",
        help="Generate environment schema file."
    )
    # Future commands:
    # group.add_argument("--delete", action="store_true", help="...")
    # group.add_argument("--update", action="store_true", help="...")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recreate schema even if it already exists."
    )
    return parser.parse_args()


def env_schema():
    args = get_argument()
    if args.generate:
        EnvConfig.generative_env_schema(forced_generate=args.force)
    # Future commands:
    # elif args.delete:
    #     EnvConfig.delete_env_schema()
    # elif args.update:
    #     EnvConfig.update_env_schema(forced_generate=args.force)

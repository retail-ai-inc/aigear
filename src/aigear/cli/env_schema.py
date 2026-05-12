import argparse
from aigear.common.config import EnvConfig


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate", action="store_true", help="Generate environment schema file."
    )
    group.add_argument(
        "--delete", action="store_true", help="Delete the generated environment schema file."
    )
    group.add_argument(
        "--show", action="store_true", help="Print the current environment schema file."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recreate schema even if it already exists.",
    )
    return parser.parse_args()


def env_schema() -> None:
    args = get_argument()
    if args.generate:
        EnvConfig.generative_env_schema(forced_generate=args.force)
    elif args.delete:
        EnvConfig.delete_env_schema()
    elif args.show:
        EnvConfig.show_env_schema()

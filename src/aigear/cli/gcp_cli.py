import argparse
from aigear.infrastructure.gcp.infra import Infra


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--create",
        action="store_true",
        help="Initialize GCP infrastructure resources."
    )
    # Future commands:
    # group.add_argument("--delete", action="store_true", help="...")
    # group.add_argument("--update", action="store_true", help="...")
    return parser.parse_args()


def gcp_infra():
    args = get_argument()
    if args.create:
        Infra().create()
    # Future commands:
    # elif args.delete:
    #     Infra().delete()
    # elif args.update:
    #     Infra().update()
    else:
        Infra().create()

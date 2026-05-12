import argparse
from aigear.infrastructure.gcp.infra import Infra


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--create", action="store_true", help="Initialize GCP infrastructure resources."
    )
    group.add_argument(
        "--delete",
        action="store_true",
        help="Delete GCP infrastructure resources. Note: Artifact Registry, Cloud KMS, and Pre-VM Images require manual deletion.",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Query and display the live state of all GCP infrastructure resources.",
    )
    return parser.parse_args()


def gcp_infra() -> None:
    args = get_argument()
    if args.create:
        Infra().create()
    elif args.delete:
        Infra().delete()
    elif args.status:
        Infra().status()

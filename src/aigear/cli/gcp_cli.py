import argparse

from aigear.common.logger import Logging
from aigear.infrastructure.gcp.infra import Infra

logger = Logging(log_name=__name__).console_logging()


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--create", action="store_true", help="Initialize GCP infrastructure resources."
    )
    group.add_argument(
        "--update",
        action="store_true",
        help="Update GCP infrastructure resources that support update (Cloud Build trigger, Kubernetes cluster).",
    )
    group.add_argument(
        "--delete",
        action="store_true",
        help=(
            "Delete enabled GCP resources, waiting for Cloud Function and GKE deletion. "
            "KMS key versions are scheduled for destruction; keyrings persist. "
            "The service account is retained if cleanup fails."
        ),
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Query and display the live state of all GCP infrastructure resources.",
    )
    return parser.parse_args()


def gcp_infra() -> None:
    args = get_argument()
    try:
        infra = Infra()
        if args.create:
            success = infra.create()
        elif args.update:
            success = infra.update()
        elif args.delete:
            success = infra.delete()
        else:
            infra.status()
            return
    except RuntimeError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
    if success is False:
        raise SystemExit(1)

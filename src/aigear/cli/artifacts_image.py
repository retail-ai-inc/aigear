import argparse

from aigear.common.constant import DOCKERFILE_PIPELINE, DOCKERFILE_SERVICE
from aigear.deploy.gcp.artifacts_image import (
    create_artifacts_image,
    delete_artifacts_image,
    retag_artifacts_image,
)


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--create", action="store_true", help="Build Docker image(s) locally."
    )
    group.add_argument("--delete", action="store_true", help="Delete Docker image(s).")
    group.add_argument("--retag", action="store_true", help="Re-tag a Docker image.")
    parser.add_argument(
        "--push",
        action="store_true",
        help="Sync the operation to Artifact Registry.",
    )
    parser.add_argument(
        "--dockerfile_path",
        default=None,
        help="Path of Dockerfile. If omitted with --create, operates on all default images.",
    )
    parser.add_argument(
        "--build_context", default=".", help="Docker build context path."
    )
    parser.add_argument(
        "--is_service",
        action="store_true",
        help="Target the model service image (default: pipeline image).",
    )
    parser.add_argument("--src_tag", default=None, help="Source tag for --retag.")
    parser.add_argument(
        "--target_tag", default=None, help="Destination tag for --retag."
    )

    args = parser.parse_args()

    if args.retag and args.src_tag is None:
        parser.error("--retag requires --src_tag.")
    if args.retag and args.target_tag is None:
        parser.error("--retag requires --target_tag.")

    return args


def _run_operation(
    args: argparse.Namespace, dockerfile_path=None, is_service=False
) -> bool:
    if args.create:
        return create_artifacts_image(
            dockerfile_path=dockerfile_path,
            build_context=args.build_context,
            is_service=is_service,
            is_build=True,
            is_push=args.push,
        )
    if args.delete:
        return delete_artifacts_image(is_service=is_service, is_push=args.push)
    if args.retag:
        return retag_artifacts_image(
            src_tag=args.src_tag,
            target_tag=args.target_tag,
            is_service=is_service,
            is_push=args.push,
        )
    return False


def docker_image():
    args = get_argument()

    if args.create and args.dockerfile_path is None:
        print("No '--dockerfile_path' provided, operating on all default images.")
        for dockerfile, is_service in [
            (DOCKERFILE_PIPELINE, False),
            (DOCKERFILE_SERVICE, True),
        ]:
            print(f"Processing image: '{dockerfile}'...")
            success = _run_operation(
                args, dockerfile_path=dockerfile, is_service=is_service
            )
            if success:
                print(f"The image({dockerfile}) operation completed.")
            else:
                print(
                    f"The image({dockerfile}) operation failed, please check the errors above."
                )
            print("-----------------------------------")
    else:
        label = args.dockerfile_path or ("service" if args.is_service else "pipeline")
        print(f"Processing image: '{label}'...")
        success = _run_operation(
            args, dockerfile_path=args.dockerfile_path, is_service=args.is_service
        )
        if success:
            print(f"The image({label}) operation completed.")
        else:
            print(
                f"The image({label}) operation failed, please check the errors above."
            )
        print("-----------------------------------")

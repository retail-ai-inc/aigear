import argparse

from aigear.common.constant import DOCKERFILE_PIPELINE, DOCKERFILE_SERVICE
from aigear.deploy.gcp.artifacts_image import create_artifacts_image


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--create",
        action="store_true",
        help="Build Docker image(s).",
    )
    # Future commands:
    # group.add_argument("--delete", action="store_true", help="...")
    # group.add_argument("--update", action="store_true", help="...")
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push Docker image(s). Can be used alone (push only) or with --create (build then push).",
    )
    parser.add_argument(
        "--dockerfile_path",
        default=None,
        help="Path of Dockerfile. If omitted, operates on all default images.",
    )
    parser.add_argument(
        "--build_context", default=".", help="Docker build context path."
    )
    parser.add_argument(
        "--is_service",
        action="store_true",
        help="Determine whether it is a model service image.",
    )
    args = parser.parse_args()
    if not args.create and not args.push:
        parser.error("At least one of --create or --push is required.")
    return args


def _run_images(args: argparse.Namespace) -> None:
    if args.dockerfile_path is None:
        print("No '--dockerfile_path' provided, operating on all default images.")
        for dockerfile, is_service in [
            (DOCKERFILE_PIPELINE, False),
            (DOCKERFILE_SERVICE, True),
        ]:
            print(f"Processing image: '{dockerfile}'...")
            success = create_artifacts_image(
                dockerfile_path=dockerfile,
                build_context=args.build_context,
                is_service=is_service,
                is_build=args.create,
                is_push=args.push,
            )
            if success:
                print(f"The image({dockerfile}) operation completed.")
            else:
                print(
                    f"The image({dockerfile}) operation failed, please check the errors above."
                )
            print("-----------------------------------")
    else:
        print(f"Processing image: '{args.dockerfile_path}'...")
        success = create_artifacts_image(
            dockerfile_path=args.dockerfile_path,
            build_context=args.build_context,
            is_service=args.is_service,
            is_build=args.create,
            is_push=args.push,
        )
        if success:
            print(f"The image({args.dockerfile_path}) operation completed.")
        else:
            print(
                f"The image({args.dockerfile_path}) operation failed, please check the errors above."
            )
        print("-----------------------------------")


def docker_image():
    _run_images(get_argument())

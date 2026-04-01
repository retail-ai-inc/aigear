import argparse

from aigear.deploy.gcp.artifacts_image import create_artifacts_image


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--create",
        action="store_true",
        help="Build and push Docker image(s) to Artifact Registry."
    )
    # Future commands:
    # group.add_argument("--delete", action="store_true", help="...")
    # group.add_argument("--update", action="store_true", help="...")
    parser.add_argument("--dockerfile_path", default=None,
                        help="Path of Dockerfile. If omitted, builds all default images.")
    parser.add_argument("--build_context", default=".",
                        help="Docker build context path.")
    parser.add_argument("--is_service", action="store_true",
                        help="Determine whether it is a model service image.")
    parser.add_argument("--force", action="store_true",
                        help="Force recreate image even if it already exists.")
    parser.add_argument("--push", action="store_true",
                        help="Push the image after building.")
    return parser.parse_args()


def _build_images(args):
    if args.dockerfile_path is None:
        print("No '--dockerfile_path' provided, building all default images.")
        for dockerfile, is_service in [("Dockerfile.pl", False), ("Dockerfile.ms", True)]:
            print(f"Building image: '{dockerfile}'...")
            create_artifacts_image(
                dockerfile_path=dockerfile,
                build_context=args.build_context,
                force=args.force,
                is_service=is_service,
                is_push=args.push,
            )
            print(f"The image({dockerfile}) creation completed.")
            print("-----------------------------------")
    else:
        print(f"Building image: '{args.dockerfile_path}'...")
        create_artifacts_image(
            dockerfile_path=args.dockerfile_path,
            build_context=args.build_context,
            force=args.force,
            is_service=args.is_service,
            is_push=args.push,
        )
        print(f"The image({args.dockerfile_path}) creation completed.")
        print("-----------------------------------")


def docker_image():
    args = get_argument()
    if args.create:
        _build_images(args)
    # Future commands:
    # elif args.delete:
    #     _delete_images(args)
    # elif args.update:
    #     _update_images(args)
    else:
        _build_images(args)

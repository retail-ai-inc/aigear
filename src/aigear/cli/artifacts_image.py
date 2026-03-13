import argparse

from aigear.deploy.gcp.artifacts_image import create_artifacts_image


def general_args(parser: argparse.ArgumentParser):
    parser.add_argument('--force', action='store_true',
                        help='force recreate image even if it already exists')
    parser.add_argument('--push', action='store_true',
                        help='The switch for pushing')
    return parser


def add_artifacts_args(parser: argparse.ArgumentParser):
    parser.add_argument('--dockerfile_path', default=None,
                        help='path of dockerfile')
    parser.add_argument('--build_context', default=".",
                        help='path of dockerfile')
    parser.add_argument('--image_name', default=None,
                        help='The name of the Docker image')
    parser.add_argument('--image_version', default="latest",
                        help='The version of the Docker image')
    parser.add_argument('--is_service', action='store_true',
                        help='Determine whether it is a image of the model service')
    parser = general_args(parser)
    return parser


def create_image():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser = add_artifacts_args(parser)
    args = parser.parse_args()

    if args.dockerfile_path is None:
        print("The 'dockerfile_math' has not been entered, and default mode will be used to create all images.")
        for dockerfile in ["Dockerfile.pl", "Dockerfile.ms"]:
            print(f"Building image: '{dockerfile}'...")
            is_service = False
            if dockerfile == "Dockerfile.ms":
                is_service = True
            
            create_artifacts_image(
                dockerfile_path=dockerfile,
                build_context=args.build_context,
                force=args.force,
                image_name=args.image_name,
                image_version=args.image_version,
                is_service=is_service,
                is_push=args.push
            )
            print(f"The image({dockerfile}) creation completed.")
            print("-----------------------------------")
    else:
        print(f"Building image: '{args.dockerfile_path}'...")
        create_artifacts_image(
            dockerfile_path=args.dockerfile_path,
            build_context=args.build_context,
            force=args.force,
            image_name=args.image_name,
            image_version=args.image_version,
            is_service=args.is_service,
            is_push=args.push
        )
        print(f"The image({args.dockerfile_path}) creation completed.")
        print("-----------------------------------")

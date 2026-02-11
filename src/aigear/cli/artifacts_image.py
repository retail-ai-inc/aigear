import argparse
from aigear.deploy.gcp.artifacts_image import create_artifacts_image


def get_argument():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dockerfile_path', default=".",
                        help='path of dockerfile')
    parser.add_argument('--image_name',
                        help='The name of the Docker image')
    parser.add_argument('--image_version', default="latest",
                        help='The version of the Docker image')
    args = parser.parse_args()
    return args

def artifacts_image_init():
    args = get_argument()
    create_artifacts_image(
        dockerfile_path=args.dockerfile_path,
        image_name=args.image_name,
        image_version = args.image_version
    )

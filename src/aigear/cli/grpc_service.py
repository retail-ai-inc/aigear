import os
import sys
import argparse
from aigear.service.grpc.grpc_service import grpc_service


def get_argument():
    # Arg
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default="",
                        help='Version of the pipeline')
    parser.add_argument('--model_class_path',
                        help='Deploy gRPC services by company code')
    args = parser.parse_args()
    return args


def grpc_run():
    project_root = os.getcwd()
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    args = get_argument()
    grpc_service(args.version, args.model_class_path)

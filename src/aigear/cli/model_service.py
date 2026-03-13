import argparse

from aigear.deploy.gcp.grpc_gcp_deploy import delete_gcp_grpc, deploy_gcp_grpc
from aigear.deploy.local.grpc_local_deploy import delete_local_grpc, deploy_local_grpc


def get_argument():
    # Arg
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', 
                        help='Version of the pipeline')
    parser.add_argument('--model_class_path',
                        help='Deploy gRPC services by company code')
    parser.add_argument('--service_ports', default="50051",
                        help='Internal interface of service')
    parser.add_argument('--replicas', default=1,
                        help='Number of copies')
    parser.add_argument('--port', default="50051",
                        help='External interface of service')
    parser.add_argument('--gcp', action='store_true',
                    help='GCP deployment of services')
    parser.add_argument('--delete', action='store_true',
                help='Delete deployment of services')
    args = parser.parse_args()
    return args

def deploy_grpc_service():
    args = get_argument()
    if args.gcp:
        if args.delete:
            delete_gcp_grpc(
                model_class_path=args.model_class_path,
            )
        else:
            deploy_gcp_grpc(
                pipeline_version=args.version,
                model_class_path=args.model_class_path,
                service_ports=args.service_ports,
                replicas=args.replicas,
                port=args.port,
            )
    else:
        if args.delete:
            delete_local_grpc(
                model_class_path=args.model_class_path,
            )
        else:
            deploy_local_grpc(
                pipeline_version=args.version,
                model_class_path=args.model_class_path,
                service_ports=args.service_ports,
                replicas=args.replicas,
                port=args.port,
            )

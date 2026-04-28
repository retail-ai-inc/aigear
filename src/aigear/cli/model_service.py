import argparse

from aigear.common.constant import ENV_PRODUCTION, ENV_STAGING
from aigear.service.grpc.constant import DEFAULT_GRPC_PORT
from aigear.deploy.gcp.grpc_gcp_deploy import delete_gcp_grpc, deploy_gcp_grpc
from aigear.deploy.local.grpc_local_deploy import delete_local_grpc, deploy_local_grpc


def get_argument() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version',
                        help='Version of the pipeline')
    parser.add_argument('--service_ports', default=DEFAULT_GRPC_PORT,
                        help='Internal interface of service')
    parser.add_argument('--replicas', default=1,
                        help='Number of copies')
    parser.add_argument('--port', default=DEFAULT_GRPC_PORT,
                        help='External interface of service')

    env_group = parser.add_mutually_exclusive_group(required=True)
    env_group.add_argument('--local', action='store_true',
                           help='Deploy to local Kubernetes (Docker Desktop)')
    env_group.add_argument('--staging', action='store_true',
                           help='Deploy to GCP staging environment')
    env_group.add_argument('--production', action='store_true',
                           help='Deploy to GCP production environment')

    parser.add_argument('--delete', action='store_true',
                        help='Delete deployment of services')
    return parser.parse_args()


def deploy_grpc_service() -> None:
    args = get_argument()

    if args.staging or args.production:
        env = ENV_PRODUCTION if args.production else ENV_STAGING
        if args.delete:
            delete_gcp_grpc(pipeline_version=args.version, env=env)
        else:
            deploy_gcp_grpc(
                pipeline_version=args.version,
                service_ports=args.service_ports,
                replicas=args.replicas,
                port=args.port,
                env=env,
            )
    else:
        if args.delete:
            delete_local_grpc(pipeline_version=args.version)
        else:
            deploy_local_grpc(
                pipeline_version=args.version,
                service_ports=args.service_ports,
                replicas=args.replicas,
                port=args.port,
            )

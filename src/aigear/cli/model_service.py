import argparse

from aigear.common.constant import ENV_LOCAL, ENV_PRODUCTION, ENV_STAGING
from aigear.deploy.common.helm_chart import create_helm_file, get_helm_path


def _get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage gRPC model service: generate YAML, deploy, update, delete, or check status.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--version',
                        help='Pipeline version')
    parser.add_argument('--service_ports', default=None,
                        help='Internal interface of service')
    parser.add_argument('--replicas', default=None, type=int,
                        help='Number of copies')
    parser.add_argument('--port', default=None,
                        help='External interface of service')

    env_group = parser.add_mutually_exclusive_group(required=True)
    env_group.add_argument('--local', action='store_true',
                           help='Target local Kubernetes (Docker Desktop)')
    env_group.add_argument('--staging', action='store_true',
                           help='Target GCP staging environment')
    env_group.add_argument('--production', action='store_true',
                           help='Target GCP production environment')

    op_group = parser.add_mutually_exclusive_group(required=True)
    op_group.add_argument('--yaml', action='store_true',
                          help='Generate the deployment YAML file only')
    op_group.add_argument('--deploy', action='store_true',
                          help='Deploy the gRPC model service')
    op_group.add_argument('--update', action='store_true',
                          help='Update an existing gRPC model service (re-applies with new params)')
    op_group.add_argument('--delete', action='store_true',
                          help='Delete the gRPC model service deployment')
    op_group.add_argument('--status', action='store_true',
                          help='Show the status of the gRPC model service deployment')
    return parser


def run_model_cli() -> None:
    parser = _get_parser()
    args = parser.parse_args()

    if args.local:
        env = ENV_LOCAL
    elif args.staging:
        env = ENV_STAGING
    else:
        env = ENV_PRODUCTION

    if args.yaml or args.deploy or args.update:
        force = args.yaml or any(x is not None for x in [args.service_ports, args.replicas, args.port])
        helm_path = create_helm_file(
            pipeline_version=args.version,
            service_ports=args.service_ports,
            replicas=args.replicas,
            port=args.port,
            env=env,
            force=force,
        )
        if args.yaml:
            return
    else:
        helm_path = get_helm_path(pipeline_version=args.version, env=env)

    op = next(k for k in ("deploy", "update", "delete", "status") if getattr(args, k))

    if env == ENV_LOCAL:
        from aigear.deploy.local.grpc_local_deploy import (
            delete_local_grpc, deploy_local_grpc, status_local_grpc, update_local_grpc,
        )
        ops = {
            "deploy": deploy_local_grpc,
            "update": update_local_grpc,
            "delete": delete_local_grpc,
            "status": status_local_grpc,
        }
    else:
        from aigear.deploy.gcp.grpc_gcp_deploy import (
            delete_gcp_grpc, deploy_gcp_grpc, status_gcp_grpc, update_gcp_grpc,
        )
        ops = {
            "deploy": deploy_gcp_grpc,
            "update": update_gcp_grpc,
            "delete": delete_gcp_grpc,
            "status": status_gcp_grpc,
        }

    ops[op](helm_path)

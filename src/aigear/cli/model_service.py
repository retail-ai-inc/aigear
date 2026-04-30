import argparse

from aigear.common.config import AppConfig
from aigear.common.constant import ENV_LOCAL, ENV_PRODUCTION, ENV_STAGING
from aigear.service.grpc.constant import DEFAULT_GRPC_PORT
from aigear.deploy.common.helm_chart import create_helm_file, get_helm_path
from aigear.deploy.gcp.grpc_gcp_deploy import delete_gcp_grpc, deploy_gcp_grpc, status_gcp_grpc, update_gcp_grpc
from aigear.deploy.local.grpc_local_deploy import delete_local_grpc, deploy_local_grpc, status_local_grpc, update_local_grpc

_ALL_ENVS = [ENV_LOCAL, ENV_STAGING, ENV_PRODUCTION]


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
    parser.add_argument('--force', action='store_true',
                        help='Force overwrite existing YAML files')

    env_group = parser.add_mutually_exclusive_group()
    env_group.add_argument('--local', action='store_true',
                           help='Target local Kubernetes (Docker Desktop)')
    env_group.add_argument('--staging', action='store_true',
                           help='Target GCP staging environment')
    env_group.add_argument('--production', action='store_true',
                           help='Target GCP production environment')

    op_group = parser.add_mutually_exclusive_group(required=True)
    op_group.add_argument('--yaml', action='store_true',
                          help='Generate deployment YAML file(s); omit env flags to generate for all environments')
    op_group.add_argument('--deploy', action='store_true',
                          help='Deploy the gRPC model service')
    op_group.add_argument('--update', action='store_true',
                          help='Update an existing gRPC model service (re-applies with new params)')
    op_group.add_argument('--delete', action='store_true',
                          help='Delete the gRPC model service deployment')
    op_group.add_argument('--status', action='store_true',
                          help='Show the status of the gRPC model service deployment')
    return parser


def _create_yamls(version: str | None, env: str | None, force: bool) -> None:
    envs = [env] if env else _ALL_ENVS
    pipelines = AppConfig.pipelines()

    if version:
        pipeline_items = [(version, pipelines.get(version, {}))]
    else:
        pipeline_items = [(v, c) for v, c in pipelines.items() if isinstance(c, dict)]

    for ver, pipeline_config in pipeline_items:
        ms_config = pipeline_config.get("model_service", {})
        model_class_path = ms_config.get("model_class_path")
        if not model_class_path:
            continue
        for e in envs:
            existing = get_helm_path(model_class_path=model_class_path, env=e).exists()
            if existing and not force:
                print(f"Skipped [{ver}][{e}]: already exists (use --force to overwrite)")
                continue
            helm_path = create_helm_file(
                pipeline_version=ver,
                model_class_path=model_class_path,
                venv=ms_config.get("venv_ms"),
                env=e,
                force=force,
            )
            if helm_path:
                action = "Overwritten" if existing else "Generated"
                print(f"{action} [{ver}][{e}]: {helm_path}")


def run_model_cli() -> None:
    parser = _get_parser()
    args = parser.parse_args()

    if args.yaml:
        if args.local:
            env = ENV_LOCAL
        elif args.staging:
            env = ENV_STAGING
        elif args.production:
            env = ENV_PRODUCTION
        else:
            env = None
        _create_yamls(version=args.version, env=env, force=args.force)
        return

    if not any([args.local, args.staging, args.production]):
        parser.error("one of --local, --staging, --production is required for this operation")

    force = args.force or any(x is not None for x in [args.service_ports, args.replicas, args.port])
    service_ports = args.service_ports or DEFAULT_GRPC_PORT
    replicas      = args.replicas if args.replicas is not None else 1
    port          = args.port or DEFAULT_GRPC_PORT

    if args.local:
        env = ENV_LOCAL
    elif args.staging:
        env = ENV_STAGING
    else:
        env = ENV_PRODUCTION

    if not args.local:
        if args.deploy:
            deploy_gcp_grpc(
                pipeline_version=args.version,
                service_ports=service_ports,
                replicas=replicas,
                port=port,
                env=env,
                force=force,
            )
        elif args.update:
            update_gcp_grpc(
                pipeline_version=args.version,
                service_ports=service_ports,
                replicas=replicas,
                port=port,
                env=env,
                force=force,
            )
        elif args.delete:
            delete_gcp_grpc(pipeline_version=args.version, env=env)
        elif args.status:
            status_gcp_grpc(pipeline_version=args.version, env=env)
    else:  # args.local
        if args.deploy:
            deploy_local_grpc(
                pipeline_version=args.version,
                service_ports=service_ports,
                replicas=replicas,
                port=port,
                env=env,
                force=force,
            )
        elif args.update:
            update_local_grpc(
                pipeline_version=args.version,
                service_ports=service_ports,
                replicas=replicas,
                port=port,
                env=env,
                force=force,
            )
        elif args.delete:
            delete_local_grpc(pipeline_version=args.version)
        elif args.status:
            status_local_grpc(pipeline_version=args.version)

import argparse
from pathlib import Path
import re

from aigear.common.constant import ENV_LOCAL, ENV_PRODUCTION, ENV_STAGING
from aigear.common.config import get_project_name
from aigear.deploy.common.helm_chart import create_helm_file, get_helm_path
from aigear.management.registry import (
    AssetRecord,
    AssetRegistry,
    FirestoreAssetRegistry,
)
from aigear.service.grpc.constant import DEFAULT_GRPC_PORT
from aigear.common.image import get_image_path


def _get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage gRPC model service: generate YAML, deploy, update, delete, or check status.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", help="Pipeline version")
    parser.add_argument(
        "--service_ports", default=None, help="Internal interface of service"
    )
    parser.add_argument("--replicas", default=None, type=int, help="Number of copies")
    parser.add_argument("--port", default=None, help="External interface of service")

    env_group = parser.add_mutually_exclusive_group(required=True)
    env_group.add_argument(
        "--local", action="store_true", help="Target local Kubernetes (Docker Desktop)"
    )
    env_group.add_argument(
        "--staging", action="store_true", help="Target GCP staging environment"
    )
    env_group.add_argument(
        "--production", action="store_true", help="Target GCP production environment"
    )

    op_group = parser.add_mutually_exclusive_group(required=True)
    op_group.add_argument(
        "--yaml", action="store_true", help="Generate the deployment YAML file only"
    )
    op_group.add_argument(
        "--deploy", action="store_true", help="Deploy the gRPC model service"
    )
    op_group.add_argument(
        "--update",
        action="store_true",
        help="Update an existing gRPC model service (re-applies with new params)",
    )
    op_group.add_argument(
        "--delete", action="store_true", help="Delete the gRPC model service deployment"
    )
    op_group.add_argument(
        "--status",
        action="store_true",
        help="Show the status of the gRPC model service deployment",
    )
    op_group.add_argument(
        "--rollback",
        action="store_true",
        help="Rollback the gRPC model service to the previous or requested service version",
    )
    parser.add_argument(
        "--service-version",
        help="Service asset version used by rollback.",
    )
    return parser


def run_model_cli(
    argv: list[str] | None = None,
    registry: AssetRegistry | None = None,
) -> None:
    parser = _get_parser()
    args = parser.parse_args(argv)

    if args.local:
        env = ENV_LOCAL
    elif args.staging:
        env = ENV_STAGING
    else:
        env = ENV_PRODUCTION

    if args.rollback:
        registry = registry or _create_registry(args.version)
        helm_path = _prepare_rollback_yaml(args, env, registry)
    elif args.yaml or args.deploy or args.update:
        service_version = None
        if args.deploy or args.update:
            registry = registry or _create_registry(args.version)
            service_version = _next_service_version(
                registry.latest("service", _service_name(args.version))
            )
        force = args.yaml or any(
            x is not None for x in [args.service_ports, args.replicas, args.port]
        )
        helm_path = create_helm_file(
            pipeline_version=args.version,
            service_ports=args.service_ports,
            replicas=args.replicas,
            port=args.port,
            env=env,
            force=force,
            service_version=service_version,
        )
        if args.yaml:
            return
    else:
        helm_path = get_helm_path(pipeline_version=args.version, env=env)

    op = "update" if args.rollback else next(
        k for k in ("deploy", "update", "delete", "status") if getattr(args, k)
    )

    if env == ENV_LOCAL:
        from aigear.deploy.local.grpc_local_deploy import (
            delete_local_grpc,
            deploy_local_grpc,
            status_local_grpc,
            update_local_grpc,
        )

        ops = {
            "deploy": deploy_local_grpc,
            "update": update_local_grpc,
            "delete": delete_local_grpc,
            "status": status_local_grpc,
        }
    else:
        from aigear.deploy.gcp.grpc_gcp_deploy import (
            delete_gcp_grpc,
            deploy_gcp_grpc,
            status_gcp_grpc,
            update_gcp_grpc,
        )

        ops = {
            "deploy": deploy_gcp_grpc,
            "update": update_gcp_grpc,
            "delete": delete_gcp_grpc,
            "status": status_gcp_grpc,
        }

    ops[op](helm_path)
    if args.deploy or args.update:
        _register_service_asset(args, env, helm_path, registry, service_version)


def _create_registry(pipeline_version: str) -> AssetRegistry:
    return FirestoreAssetRegistry(
        project_name=get_project_name() or "",
        pipeline_version=pipeline_version,
    )


def _register_service_asset(
    args: argparse.Namespace,
    env: str,
    helm_path: Path,
    registry: AssetRegistry,
    version: str | None = None,
) -> AssetRecord:
    service_name = _service_name(args.version)
    model = registry.latest("model", args.version)
    version = version or _next_service_version(registry.latest("service", service_name))
    metadata = _service_metadata(args, env, helm_path, service_name)
    record = AssetRecord(
        asset_type="service",
        name=service_name,
        version=version,
        uri=str(helm_path),
        file_name=helm_path.name,
        step_name="model_service",
        pipeline_version=args.version,
        project_name=get_project_name() or "",
        inputs=[model.ref] if model else [],
        metadata=metadata,
        status="active",
    )
    registered = registry.register(record)
    registry.set_alias("service", service_name, "champion", version)
    return registered


def _prepare_rollback_yaml(
    args: argparse.Namespace,
    env: str,
    registry: AssetRegistry,
) -> Path:
    service_name = _service_name(args.version)
    target_version = args.service_version or registry.get_alias(
        "service", service_name, "previous"
    )
    if target_version is None:
        raise SystemExit(f"No rollback target found for service {service_name}")
    record = registry.get("service", service_name, target_version)
    if record is None:
        raise SystemExit(f"Service version not found: {service_name}/{target_version}")
    metadata = record.metadata
    return create_helm_file(
        pipeline_version=args.version,
        service_ports=str(metadata.get("service_ports") or DEFAULT_GRPC_PORT),
        replicas=int(metadata.get("replicas") or 1),
        port=str(metadata.get("port") or DEFAULT_GRPC_PORT),
        env=env,
        force=True,
        service_version=target_version,
    )


def _service_metadata(
    args: argparse.Namespace,
    env: str,
    helm_path: Path,
    service_name: str,
) -> dict:
    return {
        "image": get_image_path(is_service=True),
        "env": env,
        "port": str(args.port or DEFAULT_GRPC_PORT),
        "service_ports": str(args.service_ports or DEFAULT_GRPC_PORT),
        "replicas": args.replicas if args.replicas is not None else 1,
        "pipeline_version": args.version,
        "service_name": service_name,
        "yaml_uri": str(helm_path),
    }


def _service_name(pipeline_version: str) -> str:
    project_name = (get_project_name() or "").replace("_", "-")
    version = pipeline_version.replace("_", "-")
    return f"{project_name}-{version}-service"


def _next_service_version(latest: AssetRecord | None) -> str:
    if latest is None:
        return "service-v1"
    match = re.fullmatch(r"service-v(\d+)", latest.version)
    if not match:
        return "service-v1"
    return f"service-v{int(match.group(1)) + 1}"

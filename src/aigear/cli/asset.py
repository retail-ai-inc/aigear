from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from aigear.common.config import get_project_name
from aigear.management.registry import (
    AssetRecord,
    AssetRegistry,
    AssetType,
    FirestoreAssetRegistry,
)
from aigear.management.versioned_asset import VersionedAssetManagement


def _get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage versioned pipeline assets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--pipeline-version",
            "--version",
            dest="pipeline_version",
            required=True,
            help="Pipeline version. --version is kept as a compatibility alias.",
        )
        subparser.add_argument(
            "--type",
            dest="asset_type",
            required=True,
            choices=["dataset", "feature", "model", "service"],
            help="Asset type.",
        )
        subparser.add_argument("--name", help="Asset name.")

    list_parser = subparsers.add_parser("list", help="List assets.")
    add_common(list_parser)
    list_parser.add_argument("--run-id", help="Filter by run ID.")
    list_parser.add_argument("--step-name", help="Filter by step name.")
    list_parser.add_argument("--status", help="Filter by asset status.")
    list_parser.add_argument("--limit", type=int, help="Maximum rows to return.")

    latest_parser = subparsers.add_parser("latest", help="Show the latest active asset.")
    add_common(latest_parser)

    lineage_parser = subparsers.add_parser("lineage", help="Show asset lineage.")
    add_common(lineage_parser)
    lineage_parser.add_argument("--asset-version", help="Asset version.")

    alias_parser = subparsers.add_parser("alias", help="Show an asset alias target.")
    add_common(alias_parser)
    alias_parser.add_argument("--alias", required=True, help="Alias name.")
    alias_parser.add_argument(
        "--asset-version",
        help="Set alias to this asset version. Omit to read the alias.",
    )

    register_parser = subparsers.add_parser(
        "register-external",
        help="Register an external asset URI without uploading a file.",
    )
    add_common(register_parser)
    register_parser.add_argument("--uri", required=True, help="External asset URI.")
    register_parser.add_argument("--asset-version", help="Asset version.")
    register_parser.add_argument("--file-name", help="Display file name.")
    register_parser.add_argument("--run-id", help="Run ID.")
    register_parser.add_argument("--step-name", help="Step name.")
    register_parser.add_argument(
        "--metadata",
        help="JSON object stored as asset metadata.",
    )
    return parser


def asset_cli(
    argv: Sequence[str] | None = None,
    registry: AssetRegistry | None = None,
    manager: VersionedAssetManagement | None = None,
) -> None:
    parser = _get_parser()
    args = parser.parse_args(argv)
    try:
        _run(args, registry=registry, manager=manager)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            _handle_error(exc)
        raise
    except Exception as exc:
        _handle_error(exc)


def _run(
    args: argparse.Namespace,
    registry: AssetRegistry | None = None,
    manager: VersionedAssetManagement | None = None,
) -> None:
    if args.command == "register-external":
        manager = manager or VersionedAssetManagement(
            pipeline_version=args.pipeline_version,
            project_name=_project_name(),
        )
        record = manager.register_external(
            asset_type=args.asset_type,
            asset_name=_required_register_name(args),
            uri=args.uri,
            version=args.asset_version,
            file_name=args.file_name,
            metadata=_parse_metadata(args.metadata),
            run_id=args.run_id,
            step_name=args.step_name,
        )
        _print_record(record)
        return

    registry = registry or _create_registry(args.pipeline_version)
    asset_type: AssetType = args.asset_type

    if args.command == "list":
        records = registry.list(
            asset_type=asset_type,
            name=args.name,
            run_id=args.run_id,
            step_name=args.step_name,
            status=args.status,
            limit=args.limit,
        )
        _print_records(records)
        return

    name = _resolve_asset_name(registry, asset_type, args.name, args.pipeline_version)
    if args.command == "latest":
        record = registry.latest(asset_type, name)
        if record is None:
            raise SystemExit(f"No active asset found for {asset_type}/{name}")
        _print_record(record)
        return

    if args.command == "lineage":
        version = args.asset_version
        if version is None:
            latest = registry.latest(asset_type, name)
            if latest is None:
                raise SystemExit(f"No active asset found for {asset_type}/{name}")
            version = latest.version
        _print_records(registry.lineage(asset_type, name, version))
        return

    if args.command == "alias":
        if args.asset_version:
            registry.set_alias(asset_type, name, args.alias, args.asset_version)
            print(f"{args.alias}: {args.asset_version}")
            return
        version = registry.get_alias(asset_type, name, args.alias)
        if version is None:
            raise SystemExit(f"Alias not found: {asset_type}/{name}/{args.alias}")
        print(f"{args.alias}: {version}")


def _create_registry(pipeline_version: str) -> AssetRegistry:
    return FirestoreAssetRegistry(
        project_name=_project_name(),
        pipeline_version=pipeline_version,
    )


def _project_name() -> str:
    return get_project_name() or ""


def _required_register_name(args: argparse.Namespace) -> str:
    if args.name:
        return args.name
    if args.asset_type == "model":
        return args.pipeline_version
    raise SystemExit(f"--name is required for register-external {args.asset_type} assets")


def _resolve_asset_name(
    registry: AssetRegistry,
    asset_type: AssetType,
    name: str | None,
    pipeline_version: str,
) -> str:
    if name:
        return name
    if asset_type == "model":
        return pipeline_version
    candidates = sorted({record.name for record in registry.list(asset_type=asset_type)})
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(f"No assets found for type {asset_type}")
    joined = ", ".join(candidates)
    raise SystemExit(
        f"--name is required because multiple {asset_type} assets exist: {joined}"
    )


def _parse_metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise SystemExit("--metadata must be a JSON object")
    return parsed


def _print_record(record: AssetRecord) -> None:
    print(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))


def _print_records(records: list[AssetRecord]) -> None:
    print(
        json.dumps(
            [record.to_dict() for record in records],
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _handle_error(exc: BaseException) -> None:
    message = str(exc)
    if "index" in message.lower() and "firestore" in message.lower():
        message = (
            f"{message}\nFirestore may require a composite index for this query. "
            "Open the index creation link from the Firestore error and retry."
        )
    print(message, file=sys.stderr)
    raise SystemExit(2) from exc

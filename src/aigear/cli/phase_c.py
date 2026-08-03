"""Phase C lifecycle query commands for ``aigear-asset``."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_reservation import (
    compute_import_idempotency_key_hash,
)
from aigear.management.v2.record_codec import encode_record
from aigear.management.v2.release_query import (
    ReleaseQueryError,
    list_release_history,
    list_release_impact,
)

__all__ = [
    "PhaseCCliError",
    "add_phase_c_parsers",
    "is_phase_c_command",
    "print_phase_c_error",
    "run_phase_c_query",
]


class PhaseCCliError(ValueError):
    pass


def _add_registry_binding(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pipeline-version", required=True)
    parser.add_argument("--database-id", default="(default)")


def _add_page(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--page-token")


def add_phase_c_parsers(subparsers) -> None:
    import_parser = subparsers.add_parser("import", help="Manage Phase C imports.")
    import_commands = import_parser.add_subparsers(
        dest="phase_c_action", required=True
    )
    import_status = import_commands.add_parser("status", help="Show import status.")
    _add_registry_binding(import_status)
    import_status.add_argument("--idempotency-key", required=True)
    import_status.set_defaults(phase_c_resource="import")

    release_parser = subparsers.add_parser("release", help="Manage service releases.")
    release_commands = release_parser.add_subparsers(
        dest="phase_c_action", required=True
    )
    release_show = release_commands.add_parser("show", help="Show release state.")
    _add_registry_binding(release_show)
    release_show.add_argument("--service-name", required=True)
    release_show.set_defaults(phase_c_resource="release")

    release_history = release_commands.add_parser(
        "history", help="List release operations."
    )
    _add_registry_binding(release_history)
    _add_page(release_history)
    release_history.add_argument("--service-name", required=True)
    release_history.set_defaults(phase_c_resource="release")

    release_impact = release_commands.add_parser(
        "impact", help="List releases containing an asset version."
    )
    _add_registry_binding(release_impact)
    _add_page(release_impact)
    release_impact.add_argument("--asset-version-id", required=True)
    release_impact.set_defaults(phase_c_resource="release")


def is_phase_c_command(args: argparse.Namespace) -> bool:
    return hasattr(args, "phase_c_resource")


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _record_payload(kind: str, record) -> dict:
    return {
        "schema_version": "2.0",
        "kind": kind,
        "record": encode_record(record),
    }


def _page_payload(kind: str, page) -> dict:
    return {
        "schema_version": "2.0",
        "kind": kind,
        "items": [encode_record(item) for item in page.items],
        "page": {
            "cutoff": page.cutoff,
            "next_page_token": page.next_page_token,
        },
    }


def _page_token_key(provided: bytes | None) -> bytes:
    if provided is not None:
        return provided
    encoded = os.environ.get("AIGEAR_RELEASE_PAGE_TOKEN_KEY")
    if not encoded:
        raise PhaseCCliError(
            "AIGEAR_RELEASE_PAGE_TOKEN_KEY is required for release queries"
        )
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except Exception as exc:
        raise PhaseCCliError(
            "AIGEAR_RELEASE_PAGE_TOKEN_KEY must be base64url encoded"
        ) from exc


def run_phase_c_query(
    args: argparse.Namespace,
    *,
    registry,
    page_token_signing_key: bytes | None = None,
    now: datetime | None = None,
) -> None:
    if args.phase_c_resource == "import" and args.phase_c_action == "status":
        key_hash = compute_import_idempotency_key_hash(args.idempotency_key)
        operation = registry.get_import_operation(key_hash)
        if operation is None:
            raise PhaseCCliError("import operation was not found")
        _print(_record_payload("import_status", operation))
        return

    if args.phase_c_action == "show":
        state = registry.get_service_release_state(args.service_name)
        if state is None:
            raise PhaseCCliError("service release state was not found")
        operation = (
            None
            if state.active_operation_id is None
            else registry.get_release_operation(state.active_operation_id)
        )
        if state.active_operation_id is not None and operation is None:
            raise PhaseCCliError("active service release operation was not found")
        _print(
            {
                "schema_version": "2.0",
                "kind": "release_status",
                "state": encode_record(state),
                "operation": encode_record(operation),
            }
        )
        return

    page_token_signing_key = _page_token_key(page_token_signing_key)
    query_now = now or datetime.now(timezone.utc)
    try:
        if args.phase_c_action == "history":
            page = list_release_history(
                registry,
                service_name=args.service_name,
                signing_key=page_token_signing_key,
                now=query_now,
                database_id=args.database_id,
                page_size=args.page_size,
                page_token=args.page_token,
            )
            _print(_page_payload("release_history", page))
            return
        if args.phase_c_action == "impact":
            page = list_release_impact(
                registry,
                asset_version_id=TypedId.from_typed(args.asset_version_id),
                signing_key=page_token_signing_key,
                now=query_now,
                database_id=args.database_id,
                page_size=args.page_size,
                page_token=args.page_token,
            )
            _print(_page_payload("release_impact", page))
            return
    except (ReleaseQueryError, ValueError) as exc:
        raise PhaseCCliError(str(exc)) from exc
    raise PhaseCCliError("unsupported Phase C query command")


def print_phase_c_error(exc: BaseException) -> None:
    print(
        json.dumps(
            {
                "schema_version": "2.0",
                "kind": "error",
                "error": {"code": "invalid_request", "message": str(exc)},
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )

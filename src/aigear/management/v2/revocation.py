"""Immediate revocation guards and bounded post-commit impact analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from itertools import islice
from typing import Optional, Tuple

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.policy import PolicyDecision, PolicyDecisionHead

__all__ = [
    "RevocationError",
    "RevokedAssetError",
    "RevocationUsage",
    "RevocationImpactCursor",
    "RevocationImpactReport",
    "require_non_revoked_policy_head",
    "analyze_revocation_impact",
]


class RevocationError(ValueError):
    pass


class RevokedAssetError(RevocationError):
    pass


class RevocationUsage(str, Enum):
    NEW_RUN_SEED = "new_run_seed"
    ALIAS = "alias"
    RELEASE = "release"
    ROLLBACK = "rollback"
    RUNTIME_LEASE = "runtime_lease"


@dataclass(frozen=True)
class RevocationImpactCursor:
    """Independent cursors keep partial success resumable without rescanning."""

    cutoff: str
    max_depth: int
    max_nodes: int
    downstream_cursor: Optional[str] = None
    service_cursor: Optional[str] = None
    downstream_complete: bool = False
    service_complete: bool = False

    def __post_init__(self) -> None:
        _aware_utc_time("cutoff", self.cutoff)
        if (
            isinstance(self.max_depth, bool)
            or not isinstance(self.max_depth, int)
            or not 1 <= self.max_depth <= 128
        ):
            raise RevocationError("max_depth must be an int in [1, 128]")
        if (
            isinstance(self.max_nodes, bool)
            or not isinstance(self.max_nodes, int)
            or not 1 <= self.max_nodes <= 100000
        ):
            raise RevocationError("max_nodes must be an int in [1, 100000]")
        for field_name in ("downstream_cursor", "service_cursor"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value):
                raise RevocationError(f"{field_name} must be None or a non-empty str")
        if self.downstream_complete and self.downstream_cursor is not None:
            raise RevocationError("completed downstream scans cannot retain a cursor")
        if self.service_complete and self.service_cursor is not None:
            raise RevocationError("completed service scans cannot retain a cursor")
        for field_name in ("downstream_complete", "service_complete"):
            if not isinstance(getattr(self, field_name), bool):
                raise RevocationError(f"{field_name} must be bool")


@dataclass(frozen=True)
class RevocationImpactReport:
    subject_asset_version_id: TypedId
    decision_epoch: int
    head_revision: int
    downstream_asset_version_ids: Tuple[TypedId, ...]
    active_service_names: Tuple[str, ...]
    next_cursor: Optional[RevocationImpactCursor]
    errors: Tuple[str, ...]
    generated_at: str
    cutoff: str
    max_depth: int
    max_nodes: int

    @property
    def complete(self) -> bool:
        return self.next_cursor is None


def _aware_utc_time(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RevocationError(f"{field_name} must be an ISO timestamp") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise RevocationError(f"{field_name} must be timezone-aware UTC")
    return parsed


def require_non_revoked_policy_head(
    head: Optional[PolicyDecisionHead], *, usage: RevocationUsage
) -> PolicyDecisionHead:
    """Fail closed at every entry point that can create new production use."""

    if not isinstance(usage, RevocationUsage):
        raise RevocationError("usage must be a RevocationUsage")
    if head is None or head.decision is not PolicyDecision.APPROVED:
        raise RevokedAssetError(f"asset is not approved for {usage.value}")
    return head


def _query_page(
    reader,
    method_name: str,
    *,
    subject_asset_version_id: TypedId,
    cursor: Optional[str],
    page_size: int,
    cutoff: str,
    max_depth: int,
    max_nodes: int,
    item_type: type,
    label: str,
):
    method = getattr(reader, method_name, None)
    if not callable(method):
        raise RevocationError(f"impact reader has no bounded {label} query")
    result = method(
        subject_asset_version_id=subject_asset_version_id,
        cursor=cursor,
        limit=page_size,
        cutoff=cutoff,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    if not isinstance(result, tuple) or len(result) != 2:
        raise RevocationError(f"{label} query must return (items, next_cursor)")
    raw_items, next_cursor = result
    try:
        items = tuple(islice(iter(raw_items), page_size + 1))
    except TypeError as exc:
        raise RevocationError(f"{label} query items must be iterable") from exc
    if len(items) > page_size:
        raise RevocationError(f"{label} query exceeded its requested limit")
    if not all(isinstance(item, item_type) for item in items):
        raise RevocationError(f"{label} query returned an invalid item")
    if len(set(items)) != len(items):
        raise RevocationError(f"{label} query returned duplicate items")
    if next_cursor is not None and (
        not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor
    ):
        raise RevocationError(f"{label} query returned a non-progressing cursor")
    return items, next_cursor


def analyze_revocation_impact(
    reader,
    subject_asset_version_id: TypedId,
    *,
    cursor: Optional[RevocationImpactCursor] = None,
    page_size: int = 100,
    cutoff: str,
    max_depth: int = 32,
    max_nodes: int = 10000,
    generated_at: str,
) -> RevocationImpactReport:
    """Read one bounded impact page after revocation has already committed.

    Query failures are reported and retain their input cursor. They never
    mutate or roll back the authoritative revoked policy head.
    """

    if not isinstance(subject_asset_version_id, TypedId):
        raise RevocationError("subject_asset_version_id must be a TypedId")
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= 500
    ):
        raise RevocationError("page_size must be an int in [1, 500]")
    _aware_utc_time("generated_at", generated_at)
    position = cursor or RevocationImpactCursor(
        cutoff=cutoff,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    if (
        position.cutoff != cutoff
        or position.max_depth != max_depth
        or position.max_nodes != max_nodes
    ):
        raise RevocationError("impact cursor does not match the requested scan bounds")
    current = reader.get_policy_decision_head(subject_asset_version_id)
    if (
        current is None
        or current.subject_asset_version_id != subject_asset_version_id
        or current.decision is not PolicyDecision.REVOKED
    ):
        raise RevocationError("impact analysis requires the current revoked policy head")

    downstream = ()
    services = ()
    errors = []
    downstream_cursor = position.downstream_cursor
    service_cursor = position.service_cursor
    downstream_complete = position.downstream_complete
    service_complete = position.service_complete

    if not downstream_complete:
        try:
            downstream, downstream_cursor = _query_page(
                reader,
                "query_revocation_downstream_assets",
                subject_asset_version_id=subject_asset_version_id,
                cursor=position.downstream_cursor,
                page_size=page_size,
                cutoff=cutoff,
                max_depth=max_depth,
                max_nodes=max_nodes,
                item_type=TypedId,
                label="downstream",
            )
            downstream_complete = downstream_cursor is None
        except Exception as exc:
            errors.append(f"downstream: {type(exc).__name__}")

    if not service_complete:
        try:
            services, service_cursor = _query_page(
                reader,
                "query_revocation_active_services",
                subject_asset_version_id=subject_asset_version_id,
                cursor=position.service_cursor,
                page_size=page_size,
                cutoff=cutoff,
                max_depth=max_depth,
                max_nodes=max_nodes,
                item_type=str,
                label="service",
            )
            if any(not service for service in services):
                raise RevocationError("service query returned an empty service name")
            service_complete = service_cursor is None
        except Exception as exc:
            services = ()
            service_cursor = position.service_cursor
            service_complete = position.service_complete
            errors.append(f"service: {type(exc).__name__}")

    next_cursor = None
    if not (downstream_complete and service_complete):
        next_cursor = RevocationImpactCursor(
            cutoff=cutoff,
            max_depth=max_depth,
            max_nodes=max_nodes,
            downstream_cursor=downstream_cursor,
            service_cursor=service_cursor,
            downstream_complete=downstream_complete,
            service_complete=service_complete,
        )
    return RevocationImpactReport(
        subject_asset_version_id=subject_asset_version_id,
        decision_epoch=current.current_epoch,
        head_revision=current.revision,
        downstream_asset_version_ids=downstream,
        active_service_names=services,
        next_cursor=next_cursor,
        errors=tuple(errors),
        generated_at=generated_at,
        cutoff=cutoff,
        max_depth=max_depth,
        max_nodes=max_nodes,
    )

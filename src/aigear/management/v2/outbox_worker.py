"""Bounded, restart-safe projection outbox worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Tuple

from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.projection_consumer import ProjectionEvent, consume_projection_event
from aigear.management.v2.records.outbox import ProjectionKind

__all__ = ["OutboxDrainError", "OutboxDrainResult", "drain_projection_outbox"]


class OutboxDrainError(ValueError):
    pass


@dataclass(frozen=True)
class OutboxDrainResult:
    scanned: int
    succeeded: int
    failed_event_ids: Tuple[str, ...]


def drain_projection_outbox(
    registry,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    *,
    worker_principal: str,
    now: datetime,
    batch_size: int = 100,
) -> OutboxDrainResult:
    """Process one bounded batch; one poison event cannot block the rest.

    ``query_due_outbox_events`` includes both scheduled retries and expired
    delivery leases.  The consumer performs the actual atomic lease/fence and
    acknowledgement, so concurrent worker batches remain safe and duplicate
    delivery is expected.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise OutboxDrainError("now must be timezone-aware")
    if not worker_principal:
        raise OutboxDrainError("worker_principal must be non-empty")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 500:
        raise OutboxDrainError("batch_size must be an int in [1, 500]")
    query = getattr(registry, "query_due_outbox_events", None)
    if query is None:
        raise OutboxDrainError("Registry has no bounded outbox work query")

    records = tuple(
        query(
            now=now.isoformat(),
            limit=batch_size,
            kinds=(
                ProjectionKind.ASSET_MANIFEST,
                ProjectionKind.COMMITTED_RUN_OUTPUT,
            ),
        )
    )
    failures = []
    succeeded = 0
    for record in records:
        event = ProjectionEvent(
            kind=record.kind,
            subject_id=record.subject_id,
            projection_schema_version=record.projection_schema_version,
            projection_source_revision=record.projection_source_revision,
            projection_repair_epoch=record.projection_repair_epoch,
        )
        try:
            consume_projection_event(
                registry,
                gcs,
                layout,
                event,
                worker_principal=worker_principal,
                now=now,
            )
            succeeded += 1
        except Exception:
            failures.append(record.event_id.typed)
    return OutboxDrainResult(
        scanned=len(records),
        succeeded=succeeded,
        failed_event_ids=tuple(failures),
    )

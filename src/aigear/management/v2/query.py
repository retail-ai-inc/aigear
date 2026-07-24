"""Bounded, keyset-paginated list queries for Pipeline V2 (spec section 18.1/18.2).

Implements exactly the two "list" query shapes spec 18.1 names that the
current data model already supports without any new fields or indexes:

- :func:`list_assets`: AssetVersion documents filtered by
  ``(asset_type, name)`` and optionally ``lifecycle_state``/``trust_state``,
  ordered by ``(created_at, asset_version_id)``.
- :func:`list_run_outputs`: committed Occurrence documents for one Run
  (optionally narrowed to one Step), ordered by
  ``(committed_at, occurrence_id)``.

"exact asset" / "exact occurrence" / "exact run output" (spec 18.1's other
rows this data model already supports) are plain point reads the registry
exposes directly (``get_asset_version``/``get_occurrence``/
``get_committed_occurrence_by_output_key``) and need no query-layer wrapper.

Deliberately out of scope (see docs/pipeline-v2-phase-b-tasks.md T27):
``latest`` candidate search (spec 18.3, needs Phase C fields not modeled
yet), lineage/impact graph traversal pagination, and every other spec 18.1
row this data model does not populate at all yet (labels-by-asset,
component/attachment owners, operations, pins, projection backlog, release
history, GC candidates).

Pagination follows spec 18.2 exactly: keyset-only (``(sort_field, id)`` +
``startAfter``, never offset), an opaque ``page_token`` that embeds
``schema_version``/``filter_digest``/``order``/``database_id``/
``page_cutoff``/``cursor`` and is rejected outright if any of the first four
do not match the caller's current request, and a ``page_cutoff`` fixed on
the *first* page of a scan so a later page never picks up a record created
after the scan started.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.records.asset_version import LifecycleState, TrustState
from aigear.management.v2.records.occurrence import OccurrenceStatus

__all__ = [
    "QueryError",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "PageToken",
    "QueryPage",
    "list_assets",
    "list_run_outputs",
]

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

_ASSET_LIST_ORDER = "asset_list.v1:(created_at,asset_version_id)"
_RUN_OUTPUT_LIST_ORDER = "run_output_list.v1:(committed_at,occurrence_id)"


class QueryError(ValueError):
    """Raised for a malformed page token, a token/request mismatch, or a bad page_size/filter."""


def _require_non_empty_str(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise QueryError(f"{field_name} must be a non-empty str, got {value!r}")
    return value


def _parse_timestamp(field_name: str, value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise QueryError(f"{field_name} must be an ISO-8601 timestamp, got {value!r}") from exc


def _require_valid_page_size(page_size: int) -> int:
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not (1 <= page_size <= MAX_PAGE_SIZE)
    ):
        raise QueryError(f"page_size must be an int in [1, {MAX_PAGE_SIZE}], got {page_size!r}")
    return page_size


@dataclass(frozen=True)
class PageToken:
    """The full state one page hands back to the caller to fetch the next one.

    Every field except ``cursor`` is re-validated against the *next* call's
    own parameters by :meth:`check_matches_request` (spec 18.2's "token 与
    当前请求不匹配时拒绝"); ``cursor`` is the opaque ``(sort_value,
    tiebreak_id)`` keyset position of the last item already returned.
    """

    schema_version: str
    filter_digest: str
    order: str
    database_id: str
    page_cutoff: str
    cursor: Tuple[str, str]

    def __post_init__(self) -> None:
        _require_non_empty_str("schema_version", self.schema_version)
        _require_non_empty_str("filter_digest", self.filter_digest)
        _require_non_empty_str("order", self.order)
        _require_non_empty_str("database_id", self.database_id)
        _parse_timestamp("page_cutoff", self.page_cutoff)
        if (
            not isinstance(self.cursor, tuple)
            or len(self.cursor) != 2
            or not all(isinstance(part, str) and part for part in self.cursor)
        ):
            raise QueryError(f"cursor must be a 2-tuple of non-empty str, got {self.cursor!r}")

    def check_matches_request(
        self, *, schema_version: str, filter_digest: str, order: str, database_id: str
    ) -> None:
        mismatches = [
            name
            for name, expected, actual in (
                ("schema_version", schema_version, self.schema_version),
                ("filter_digest", filter_digest, self.filter_digest),
                ("order", order, self.order),
                ("database_id", database_id, self.database_id),
            )
            if expected != actual
        ]
        if mismatches:
            raise QueryError(
                f"page_token does not match the current request: {', '.join(mismatches)} differ"
            )

    def encode(self) -> str:
        payload = [
            self.schema_version,
            self.filter_digest,
            self.order,
            self.database_id,
            self.page_cutoff,
            list(self.cursor),
        ]
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @classmethod
    def decode(cls, token: str) -> "PageToken":
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
            schema_version, filter_digest, order, database_id, page_cutoff, cursor = json.loads(raw)
            return cls(
                schema_version=schema_version,
                filter_digest=filter_digest,
                order=order,
                database_id=database_id,
                page_cutoff=page_cutoff,
                cursor=tuple(cursor),
            )
        except QueryError:
            raise
        except Exception as exc:
            raise QueryError(f"malformed page_token: {token!r}") from exc


@dataclass(frozen=True)
class QueryPage:
    """One page of results plus the opaque token to fetch the next page (``None`` at the end)."""

    items: Tuple[object, ...]
    next_page_token: Optional[str]


def _paginate(
    entries: Sequence[Tuple[Optional[str], str, object]],
    *,
    sort_field_name: str,
    schema_version: str,
    filter_digest: str,
    order: str,
    database_id: str,
    page_size: int,
    page_token: Optional[str],
    now: datetime,
) -> QueryPage:
    """Shared keyset-pagination core for both list functions.

    ``entries`` is every record already matching the caller's filter (the
    fake registry has no server-side index to stream from, so this task
    materializes the whole matching set up front and paginates in memory);
    each is a ``(sort_value, tiebreak_id, item)`` triple. Entries with no
    sort value are dropped -- a real Firestore write always stamps a server
    timestamp, so ``None`` here only means "not yet visible to any query",
    never "sorts first".
    """
    page_size = _require_valid_page_size(page_size)
    rows = [
        (_parse_timestamp(sort_field_name, sort_value), tiebreak_id, sort_value, item)
        for sort_value, tiebreak_id, item in entries
        if sort_value is not None
    ]
    rows.sort(key=lambda row: (row[0], row[1]))

    if page_token is None:
        cutoff_str = now.isoformat()
        cutoff_dt = now
        after: Optional[Tuple[datetime, str]] = None
    else:
        decoded = PageToken.decode(page_token)
        decoded.check_matches_request(
            schema_version=schema_version, filter_digest=filter_digest, order=order,
            database_id=database_id,
        )
        cutoff_str = decoded.page_cutoff
        cutoff_dt = _parse_timestamp("page_cutoff", cutoff_str)
        after_sort_value, after_id = decoded.cursor
        after = (_parse_timestamp(sort_field_name, after_sort_value), after_id)

    visible = [row for row in rows if row[0] <= cutoff_dt]
    if after is not None:
        visible = [row for row in visible if (row[0], row[1]) > after]

    page_rows = visible[: page_size]
    next_token: Optional[str] = None
    if len(visible) > page_size:
        _, last_id, last_sort_value, _ = page_rows[-1]
        next_token = PageToken(
            schema_version=schema_version, filter_digest=filter_digest, order=order,
            database_id=database_id, page_cutoff=cutoff_str, cursor=(last_sort_value, last_id),
        ).encode()

    return QueryPage(items=tuple(row[3] for row in page_rows), next_page_token=next_token)


def list_assets(
    registry: FakeRegistryV2,
    *,
    schema_version: str,
    asset_type: str,
    name: str,
    lifecycle_state: Optional[LifecycleState] = None,
    trust_state: Optional[TrustState] = None,
    database_id: str = "(default)",
    page_size: int = DEFAULT_PAGE_SIZE,
    page_token: Optional[str] = None,
    now: datetime,
) -> QueryPage:
    """Spec 18.1's "asset list" row: AssetVersion documents for one
    ``(asset_type, name)``, optionally narrowed to a ``lifecycle_state``/
    ``trust_state``, ordered by ``(created_at, asset_version_id)``."""
    _require_non_empty_str("schema_version", schema_version)
    _require_non_empty_str("asset_type", asset_type)
    _require_non_empty_str("name", name)
    if lifecycle_state is not None and not isinstance(lifecycle_state, LifecycleState):
        raise QueryError(f"lifecycle_state must be a LifecycleState, got {lifecycle_state!r}")
    if trust_state is not None and not isinstance(trust_state, TrustState):
        raise QueryError(f"trust_state must be a TrustState, got {trust_state!r}")

    filter_digest = digest_sha256_of_jcs(
        [
            "aigear.query.asset-list.v1",
            asset_type,
            name,
            lifecycle_state.value if lifecycle_state is not None else None,
            trust_state.value if trust_state is not None else None,
        ]
    )

    entries = [
        (record.created_at, record.asset_version_id.typed, record)
        for record in registry.iter_asset_versions()
        if record.asset_type == asset_type
        and record.name == name
        and (lifecycle_state is None or record.lifecycle_state == lifecycle_state)
        and (trust_state is None or record.trust_state == trust_state)
    ]
    return _paginate(
        entries,
        sort_field_name="created_at",
        schema_version=schema_version,
        filter_digest=filter_digest,
        order=_ASSET_LIST_ORDER,
        database_id=database_id,
        page_size=page_size,
        page_token=page_token,
        now=now,
    )


def list_run_outputs(
    registry: FakeRegistryV2,
    *,
    schema_version: str,
    run_id: str,
    step_name: Optional[str] = None,
    database_id: str = "(default)",
    page_size: int = DEFAULT_PAGE_SIZE,
    page_token: Optional[str] = None,
    now: datetime,
) -> QueryPage:
    """Spec 18.1's "run output list" row: committed Occurrence documents for
    one Run, optionally narrowed to one Step, ordered by
    ``(committed_at, occurrence_id)``. ``committed`` is a fixed filter, not
    caller-configurable, matching the spec's literal query shape."""
    _require_non_empty_str("schema_version", schema_version)
    _require_non_empty_str("run_id", run_id)
    if step_name is not None:
        _require_non_empty_str("step_name", step_name)

    filter_digest = digest_sha256_of_jcs(["aigear.query.run-output-list.v1", run_id, step_name])

    entries = [
        (record.committed_at, record.occurrence_id.typed, record)
        for record in registry.iter_occurrences_by_run(run_id)
        if record.status == OccurrenceStatus.COMMITTED
        and (step_name is None or record.step_name == step_name)
    ]
    return _paginate(
        entries,
        sort_field_name="committed_at",
        schema_version=schema_version,
        filter_digest=filter_digest,
        order=_RUN_OUTPUT_LIST_ORDER,
        database_id=database_id,
        page_size=page_size,
        page_token=page_token,
        now=now,
    )

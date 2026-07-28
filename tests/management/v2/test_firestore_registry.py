from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.firestore_registry import FirestoreRegistryV2


class _Query:
    def __init__(self, sink, actions=()):
        self.sink = sink
        self.actions = actions

    def _next(self, action):
        return _Query(self.sink, self.actions + (action,))

    def where(self, field, operator, value):
        return self._next(("where", field, operator, value))

    def order_by(self, field):
        return self._next(("order_by", field))

    def start_after(self, cursor):
        return self._next(("start_after", cursor))

    def limit(self, value):
        query = self._next(("limit", value))
        self.sink.append(query.actions)
        return query

    def stream(self):
        return ()


class _Client:
    def __init__(self):
        self.queries = []

    def collection(self, _path):
        return _Query(self.queries)


def test_firestore_asset_query_pushes_cursor_cutoff_and_limit_server_side():
    client = _Client()
    registry = FirestoreRegistryV2("proj", "v1", client=client)

    assert tuple(
        registry.query_asset_versions(
            asset_type="model",
            name="weights",
            page_cutoff="2026-07-27T00:00:00+00:00",
            cursor=("2026-07-26T00:00:00+00:00", "sha256:" + "aa" * 32),
            limit=51,
        )
    ) == ()

    actions = client.queries[-1]
    assert ("where", "created_at", "<=", "2026-07-27T00:00:00+00:00") in actions
    assert any(action[0] == "start_after" for action in actions)
    assert actions[-1] == ("limit", 51)


def test_firestore_outbox_query_is_two_bounded_index_scans():
    client = _Client()
    registry = FirestoreRegistryV2("proj", "v1", client=client)

    assert registry.query_due_outbox_events(
        now="2026-07-27T00:00:00+00:00", limit=100
    ) == ()

    assert len(client.queries) == 2
    assert all(actions[-1] == ("limit", 100) for actions in client.queries)
    assert any(
        ("where", "next_attempt_at", "<=", "2026-07-27T00:00:00+00:00")
        in actions
        for actions in client.queries
    )
    assert any(
        ("where", "lease_expires_at", "<=", "2026-07-27T00:00:00+00:00")
        in actions
        for actions in client.queries
    )


class _ReadTimeQuery:
    def __init__(self, sink, actions=()):
        self.sink = sink
        self.actions = actions

    def _next(self, action):
        return _ReadTimeQuery(self.sink, self.actions + (action,))

    def where(self, field, operator, value):
        return self._next(("where", field, operator, value))

    def order_by(self, field):
        return self._next(("order_by", field))

    def start_after(self, cursor):
        return self._next(("start_after", cursor))

    def limit(self, value):
        return self._next(("limit", value))

    def stream(self, *, read_time=None):
        self.sink.append((self.actions, read_time))
        return ()


class _ReadTimeSnapshot:
    exists = False


class _ReadTimeDocument:
    def __init__(self, sink):
        self.sink = sink

    def get(self, *, read_time=None):
        self.sink.append(read_time)
        return _ReadTimeSnapshot()


class _ReadTimeClient:
    def __init__(self):
        self.queries = []
        self.point_reads = []

    def collection(self, _path):
        return _ReadTimeQuery(self.queries)

    def document(self, _path):
        return _ReadTimeDocument(self.point_reads)


def test_policy_evidence_queries_pin_read_time_cursor_and_limit_server_side():
    read_time = datetime(2026, 7, 28, tzinfo=timezone.utc)
    client = _ReadTimeClient()
    registry = FirestoreRegistryV2(
        "proj", "v1", client=client
    ).at_read_time(read_time)
    asset_id = TypedId.from_bare("aa" * 32)
    cursor = TypedId.from_bare("bb" * 32).typed

    assert registry.get_asset_version(asset_id) is None
    assert registry.query_occurrences_by_asset(
        asset_version_id=asset_id,
        cursor=cursor,
        limit=17,
    ) == ()
    assert registry.query_import_provenance_by_asset(
        asset_version_id=asset_id,
        cursor=cursor,
        limit=17,
    ) == ()

    assert client.point_reads == [read_time]
    assert len(client.queries) == 2
    assert all(actions[-1] == ("limit", 17) for actions, _ in client.queries)
    assert all(observed == read_time for _, observed in client.queries)
    assert all(
        any(action[0] == "start_after" for action in actions)
        for actions, _ in client.queries
    )
    with pytest.raises(ValueError, match="read-only"):
        registry.run_atomic(lambda tx: None)

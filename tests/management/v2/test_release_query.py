from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_query import (
    ReleaseQueryError,
    list_release_history,
    list_release_impact,
)
from tests.management.v2.records.test_release import _operation
from tests.management.v2.test_release_registry import _release


_KEY = b"release-page-token-test-key-32bytes!!"
_NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


def _registry():
    registry = FakeRegistryV2(server_read_time=_NOW)
    asset_id = TypedId.from_bare("cc" * 32)
    for index in range(3):
        at = (_NOW - timedelta(minutes=3 - index)).isoformat()
        release_id = TypedId.from_bare(f"{index + 1:02x}" * 32)
        registry.put_release(
            _release(
                release_id=release_id,
                manifest_digest=release_id,
                asset_version_ids=(asset_id,),
                created_at=at,
            )
        )
        registry.put_release_operation(
            replace(
                _operation(),
                operation_id=f"release-history-{index}",
                target_release_id=release_id,
                request_fingerprint=release_id,
                idempotency_key_hash=f"history-{index}",
                created_at=at,
                updated_at=at,
            )
        )
    return registry, asset_id


def test_history_uses_signed_keyset_token_and_fixed_cutoff():
    registry, _asset_id = _registry()
    first = list_release_history(
        registry,
        service_name="predictor",
        signing_key=_KEY,
        now=_NOW,
        page_size=2,
    )
    second = list_release_history(
        registry,
        service_name="predictor",
        signing_key=_KEY,
        now=_NOW + timedelta(days=1),
        page_size=2,
        page_token=first.next_page_token,
    )

    assert len(first.items) == 2
    assert len(second.items) == 1
    assert second.cutoff == first.cutoff
    assert {item.operation_id for item in (*first.items, *second.items)} == {
        "release-history-0",
        "release-history-1",
        "release-history-2",
    }


def test_token_tampering_or_filter_reuse_is_rejected():
    registry, _asset_id = _registry()
    page = list_release_history(
        registry,
        service_name="predictor",
        signing_key=_KEY,
        now=_NOW,
        page_size=1,
    )
    token = page.next_page_token
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ReleaseQueryError, match="signature"):
        list_release_history(
            registry,
            service_name="predictor",
            signing_key=_KEY,
            now=_NOW,
            page_size=1,
            page_token=tampered,
        )
    with pytest.raises(ReleaseQueryError, match="current query"):
        list_release_history(
            registry,
            service_name="other",
            signing_key=_KEY,
            now=_NOW,
            page_size=1,
            page_token=token,
        )


def test_impact_report_is_bounded_by_exact_asset_filter():
    registry, asset_id = _registry()
    page = list_release_impact(
        registry,
        asset_version_id=asset_id,
        signing_key=_KEY,
        now=_NOW,
        page_size=2,
    )

    assert len(page.items) == 2
    assert page.next_page_token is not None
    assert all(asset_id in item.asset_version_ids for item in page.items)

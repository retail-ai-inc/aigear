from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_commit import commit_policy_decision_registry
from aigear.management.v2.records.asset_version import TrustState
from aigear.management.v2.records.outbox import (
    OUTBOX_PRIORITY_EMERGENCY,
    ProjectionKind,
)
from aigear.management.v2.records.policy import PolicyDecision, PolicyDecisionHead
from aigear.management.v2.revocation import (
    RevocationImpactCursor,
    RevocationUsage,
    RevokedAssetError,
    analyze_revocation_impact,
    require_non_revoked_policy_head,
)
from tests.management.v2.test_policy_commit import _FP, _asset
from tests.management.v2.test_policy_journal import _journal, _prepare, _setup


def _revoked_registry():
    asset = _asset()
    previous_attestation = TypedId.from_bare("de" * 32)
    approved_asset = replace(
        asset,
        trust_state=TrustState.APPROVED,
        policy_decision_head_ref=previous_attestation,
        record_revision=2,
    )
    previous_head = PolicyDecisionHead(
        schema_version="2.0",
        environment_fingerprint=_FP,
        subject_asset_version_id=asset.asset_version_id,
        current_epoch=1,
        revision=1,
        attestation_id=previous_attestation,
        decision=PolicyDecision.APPROVED,
        policy_version="policy-2026-07",
        not_before="2026-07-27T00:00:00+00:00",
        valid_until="2026-07-29T00:00:00+00:00",
    )
    operation, lock, authenticated = _setup(
        epoch=2,
        subject=asset.asset_version_id,
        decision=PolicyDecision.REVOKED,
    )
    journal = _journal()
    journaled = _prepare(operation, lock, authenticated, journal)
    registry = FakeRegistryV2()
    registry.put_asset_version(approved_asset)
    registry.put_policy_decision_head(previous_head)
    registry.put_policy_decision_operation(journaled)
    registry.put_policy_decision_reservation(lock)
    result = commit_policy_decision_registry(
        registry,
        journaled,
        authenticated,
        journal=journal,
        committed_at="2026-07-28T00:00:05+00:00",
    )
    return registry, result


def test_revocation_commits_emergency_event_atomically_and_first():
    registry, result = _revoked_registry()

    assert result.head.decision is PolicyDecision.REVOKED
    assert result.head.current_epoch == 2
    asset = registry.get_asset_version(result.head.subject_asset_version_id)
    assert asset.trust_state is TrustState.REVOKED
    due = registry.query_due_outbox_events(
        now="2026-07-28T00:00:05+00:00", limit=10
    )
    assert [event.kind for event in due] == [
        ProjectionKind.POLICY_REVOCATION_EMERGENCY,
        ProjectionKind.POLICY_DECISION_AUDIT,
    ]
    assert due[0].priority == OUTBOX_PRIORITY_EMERGENCY


@pytest.mark.parametrize("usage", tuple(RevocationUsage))
def test_all_new_authorization_entries_reject_revoked_head(usage):
    _, result = _revoked_registry()

    with pytest.raises(RevokedAssetError, match=usage.value):
        require_non_revoked_policy_head(result.head, usage=usage)


class _ImpactReader:
    def __init__(self, head, *, fail_services=False):
        self.head = head
        self.fail_services = fail_services
        self.calls = []

    def get_policy_decision_head(self, _subject):
        return self.head

    def query_revocation_downstream_assets(
        self, *, subject_asset_version_id, cursor, limit, cutoff, max_depth, max_nodes
    ):
        self.calls.append(
            ("downstream", subject_asset_version_id, cursor, limit, cutoff, max_depth, max_nodes)
        )
        if cursor is None:
            return ((TypedId.from_bare("01" * 32),), "downstream-page-2")
        return ((TypedId.from_bare("02" * 32),), None)

    def query_revocation_active_services(
        self, *, subject_asset_version_id, cursor, limit, cutoff, max_depth, max_nodes
    ):
        self.calls.append(
            ("service", subject_asset_version_id, cursor, limit, cutoff, max_depth, max_nodes)
        )
        if self.fail_services:
            raise RuntimeError("service index unavailable")
        return (("fraud-api",), None)


def test_impact_analysis_is_bounded_and_resumes_each_index_independently():
    registry, result = _revoked_registry()
    reader = _ImpactReader(result.head)

    first = analyze_revocation_impact(
        reader,
        result.head.subject_asset_version_id,
        page_size=7,
        cutoff="2026-07-28T00:00:05+00:00",
        generated_at="2026-07-28T00:01:00+00:00",
    )
    assert first.downstream_asset_version_ids == (TypedId.from_bare("01" * 32),)
    assert first.active_service_names == ("fraud-api",)
    assert first.next_cursor == RevocationImpactCursor(
        cutoff="2026-07-28T00:00:05+00:00",
        max_depth=32,
        max_nodes=10000,
        downstream_cursor="downstream-page-2",
        downstream_complete=False,
        service_complete=True,
    )
    assert all(call[3] == 7 for call in reader.calls)

    reader.calls.clear()
    second = analyze_revocation_impact(
        reader,
        result.head.subject_asset_version_id,
        cursor=first.next_cursor,
        page_size=7,
        cutoff="2026-07-28T00:00:05+00:00",
        generated_at="2026-07-28T00:02:00+00:00",
    )
    assert second.complete
    assert second.downstream_asset_version_ids == (TypedId.from_bare("02" * 32),)
    assert second.active_service_names == ()
    assert [call[0] for call in reader.calls] == ["downstream"]
    assert registry.get_policy_decision_head(result.head.subject_asset_version_id).decision is PolicyDecision.REVOKED


def test_impact_query_failure_is_reported_without_weakening_revocation():
    registry, result = _revoked_registry()
    reader = _ImpactReader(result.head, fail_services=True)

    report = analyze_revocation_impact(
        reader,
        result.head.subject_asset_version_id,
        page_size=5,
        cutoff="2026-07-28T00:00:05+00:00",
        generated_at="2026-07-28T00:01:00+00:00",
    )

    assert report.errors == ("service: RuntimeError",)
    assert not report.complete
    assert report.next_cursor.service_complete is False
    assert registry.get_asset_version(
        result.head.subject_asset_version_id
    ).trust_state is TrustState.REVOKED

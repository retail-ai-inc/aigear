from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.policy_commit import (
    PolicyCommitConflict,
    commit_policy_decision_registry,
    finalize_policy_decision_commit,
)
from aigear.management.v2.policy_journal import append_policy_decision_committed
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    AssetVersionRecord,
    LifecycleState,
    ProducerSpec,
    TrustState,
    compute_asset_version_id,
)
from aigear.management.v2.records.outbox import ProjectionKind
from aigear.management.v2.records.policy import (
    PolicyDecision,
    PolicyDecisionHead,
    PolicyDecisionOperationPhase,
    compute_subject_epoch_key,
)
from tests.management.v2.test_policy_journal import _journal, _prepare, _setup

_FP = TypedId.from_bare("aa" * 32)


def _asset():
    component = AssetComponent(
        role="model",
        logical_name="model",
        blob_id=TypedId.from_bare("44" * 32),
        media_type="model/onnx",
    )
    producer = ProducerSpec(
        source_commit="commit-1",
        image_digest=TypedId.from_bare("55" * 32),
        code_digest=TypedId.from_bare("66" * 32),
        config_digest=TypedId.from_bare("77" * 32),
    )
    manifest = {
        "environment_id": "production",
        "environment_fingerprint": _FP.typed,
        "asset_type": "model",
        "name": "fraud",
        "components": [component.to_manifest_dict()],
        "input_bindings": [],
        "producer_spec": producer.to_manifest_dict(),
        "schema_contract_digest": TypedId.from_bare("88" * 32).typed,
        "runtime_contract_digest": TypedId.from_bare("99" * 32).typed,
        "policy_version": "policy-2026-07",
    }
    asset_id = compute_asset_version_id(manifest)
    return AssetVersionRecord(
        schema_version="2.0",
        environment_id="production",
        environment_fingerprint=_FP,
        asset_version_id=asset_id,
        asset_type="model",
        name="fraud",
        manifest_digest=asset_id,
        record_revision=1,
        components=(component,),
        input_bindings=(),
        producer_spec=producer,
        schema_contract_digest=TypedId.from_bare("88" * 32),
        runtime_contract_digest=TypedId.from_bare("99" * 32),
        lifecycle_state=LifecycleState.ACTIVE,
        trust_state=TrustState.VERIFIED,
        policy_version="policy-2026-07",
        manifest_integrity_attestation_ref=TypedId.from_bare("aa" * 32),
        created_at="2026-07-28T00:00:00+00:00",
    )


def _state():
    asset = _asset()
    operation, lock, authenticated = _setup(subject=asset.asset_version_id)
    journal = _journal()
    journaled = _prepare(operation, lock, authenticated, journal)
    registry = FakeRegistryV2()
    registry.put_asset_version(asset)
    registry.put_policy_decision_operation(journaled)
    registry.put_policy_decision_reservation(lock)
    return registry, journaled, authenticated, journal, asset


def test_policy_registry_commit_is_atomic_and_journal_finalized():
    registry, operation, authenticated, journal, asset = _state()

    result = commit_policy_decision_registry(
        registry,
        operation,
        authenticated,
        journal=journal,
        committed_at="2026-07-28T00:00:05+00:00",
    )

    assert result.operation.phase is PolicyDecisionOperationPhase.COMMITTING
    assert result.head.current_epoch == 1
    assert result.head.revision == 1
    assert result.epoch_binding.attestation_id == authenticated.attestation.attestation_id
    updated = registry.get_asset_version(asset.asset_version_id)
    assert updated.trust_state is TrustState.APPROVED
    assert updated.policy_decision_head_ref == result.head.attestation_id
    assert updated.record_revision == 2
    epoch_key = compute_subject_epoch_key(asset.asset_version_id, 1)
    assert registry.get_policy_decision_epoch(epoch_key) == result.epoch_binding
    assert registry.get_attestation(result.head.attestation_id) == authenticated.attestation
    outbox = tuple(registry._outbox_events.values())
    assert len(outbox) == 1
    assert outbox[0].kind is ProjectionKind.POLICY_DECISION_AUDIT

    committed_entry = append_policy_decision_committed(
        result.operation,
        journal=journal,
        registry_commit_digest=result.registry_commit_digest,
        issued_at="2026-07-28T00:00:06+00:00",
        expected_previous_sequence=operation.prepared_journal.sequence,
        expected_previous_entry_id=operation.prepared_journal.entry_id,
    )
    succeeded = finalize_policy_decision_commit(
        registry,
        result,
        journal=journal,
        committed_journal_entry=committed_entry,
        finished_at="2026-07-28T00:00:07+00:00",
    )
    assert succeeded.phase is PolicyDecisionOperationPhase.SUCCEEDED
    assert succeeded.lease_expires_at is None


def test_registry_commit_ack_loss_reconstructs_same_result():
    registry, operation, authenticated, journal, _ = _state()
    first = commit_policy_decision_registry(
        registry,
        operation,
        authenticated,
        journal=journal,
        committed_at="2026-07-28T00:00:05+00:00",
    )

    replay = commit_policy_decision_registry(
        registry,
        operation,
        authenticated,
        journal=journal,
        committed_at="2026-07-28T00:00:05+00:00",
    )

    assert replay == first
    assert len(registry._policy_decision_epochs) == 1
    assert len(registry._outbox_events) == 1


def test_missing_asset_rolls_back_every_policy_write():
    registry, operation, authenticated, journal, asset = _state()
    del registry._asset_versions[asset.asset_version_id]

    with pytest.raises(PolicyCommitConflict, match="not active"):
        commit_policy_decision_registry(
            registry,
            operation,
            authenticated,
            journal=journal,
            committed_at="2026-07-28T00:00:05+00:00",
        )

    assert not registry._policy_decision_heads
    assert not registry._policy_decision_epochs
    assert not registry._attestations
    assert not registry._outbox_events
    assert registry.get_policy_decision_operation(
        operation.idempotency_key_hash
    ) == operation


def test_stale_head_revision_rejects_without_partial_commit():
    registry, operation, authenticated, journal, asset = _state()
    existing_head = PolicyDecisionHead(
        schema_version="2.0",
        environment_fingerprint=_FP,
        subject_asset_version_id=asset.asset_version_id,
        current_epoch=1,
        revision=1,
        attestation_id=TypedId.from_bare("de" * 32),
        decision=PolicyDecision.APPROVED,
        policy_version="old-policy",
        not_before="2026-07-27T00:00:00+00:00",
        valid_until="2026-07-29T00:00:00+00:00",
    )
    registry.put_policy_decision_head(existing_head)

    with pytest.raises(PolicyCommitConflict, match="head revision or epoch"):
        commit_policy_decision_registry(
            registry,
            operation,
            authenticated,
            journal=journal,
            committed_at="2026-07-28T00:00:05+00:00",
        )

    assert registry.get_policy_decision_head(asset.asset_version_id) == existing_head
    assert not registry._policy_decision_epochs
    assert not registry._outbox_events


def test_finalize_rejects_committed_evidence_for_another_registry_digest():
    registry, operation, authenticated, journal, _ = _state()
    result = commit_policy_decision_registry(
        registry,
        operation,
        authenticated,
        journal=journal,
        committed_at="2026-07-28T00:00:05+00:00",
    )
    entry = append_policy_decision_committed(
        result.operation,
        journal=journal,
        registry_commit_digest=TypedId.from_bare("ff" * 32),
        issued_at="2026-07-28T00:00:06+00:00",
        expected_previous_sequence=operation.prepared_journal.sequence,
        expected_previous_entry_id=operation.prepared_journal.entry_id,
    )

    with pytest.raises(PolicyCommitConflict, match="journal"):
        finalize_policy_decision_commit(
            registry,
            result,
            journal=journal,
            committed_journal_entry=entry,
            finished_at="2026-07-28T00:00:07+00:00",
        )

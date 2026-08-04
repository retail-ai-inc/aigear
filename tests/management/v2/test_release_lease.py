from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.release_lease import (
    DEFAULT_RELEASE_HEARTBEAT_SECONDS,
    DEFAULT_RELEASE_LEASE_TTL_SECONDS,
    ReleaseExternalState,
    ReleaseLeaseBusy,
    ReleaseLeaseConflict,
    ReleaseLeaseFenced,
    acquire_release_lease,
    heartbeat_release_lease,
    require_release_lease,
    takeover_release_lease,
)
from aigear.management.v2.records.release import ReleasePhase
from tests.management.v2.test_release_registry import _FP, _release


_NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


def _registry(now=_NOW):
    registry = FakeRegistryV2(server_read_time=now)
    registry.put_release(_release())
    return registry


def _acquire(registry, **overrides):
    values = dict(
        environment_fingerprint=_FP,
        service_name="predictor",
        target_release_id=_release().release_id,
        idempotency_key="publish-1",
        owner_principal="publisher-a@example.test",
    )
    values.update(overrides)
    return acquire_release_lease(registry, **values)


def test_acquire_is_idempotent_and_uses_server_time_defaults():
    registry = _registry()

    first = _acquire(registry)
    second = _acquire(registry)

    assert second == first
    assert first.fencing_token == 1
    assert first.lease_expires_at == (
        _NOW + timedelta(seconds=DEFAULT_RELEASE_LEASE_TTL_SECONDS)
    ).isoformat()
    assert DEFAULT_RELEASE_HEARTBEAT_SECONDS == 30
    state = registry.get_service_release_state("predictor")
    assert state.active_operation_id == first.operation_id
    assert state.fencing_token == first.fencing_token
    assert state.revision == first.expected_service_revision


def test_one_service_rejects_a_second_live_publisher():
    registry = _registry()
    _acquire(registry)

    with pytest.raises(ReleaseLeaseBusy, match="live"):
        _acquire(
            registry,
            idempotency_key="publish-2",
            owner_principal="publisher-b@example.test",
        )


def test_expired_takeover_reads_external_state_then_increments_fence():
    registry = _registry()
    old = _acquire(registry, lease_ttl_seconds=31)
    registry._server_read_time = _NOW + timedelta(seconds=32)
    calls = []

    def observe(service_name):
        calls.append(service_name)
        return ReleaseExternalState(
            deployment_uid="uid-1",
            deployment_resource_version="41",
            service_resource_version="52",
        )

    new = _acquire(
        registry,
        idempotency_key="publish-2",
        owner_principal="publisher-b@example.test",
        read_external_state=observe,
    )

    assert calls == ["predictor"]
    assert new.fencing_token == old.fencing_token + 1
    assert new.expected_deployment_uid == "uid-1"
    assert new.expected_deployment_resource_version == "41"
    assert new.expected_service_resource_version == "52"
    previous = registry.get_release_operation(old.operation_id)
    assert previous.phase.value == "reconciling"
    assert previous.lease_expires_at is None


def test_expired_takeover_without_external_read_fails_closed():
    registry = _registry()
    _acquire(registry, lease_ttl_seconds=31)
    registry._server_read_time = _NOW + timedelta(seconds=32)

    with pytest.raises(ReleaseLeaseConflict, match="external state"):
        _acquire(
            registry,
            idempotency_key="publish-2",
            owner_principal="publisher-b@example.test",
        )


def test_takeover_cas_rejects_state_changed_after_observation():
    registry = _registry()
    old = _acquire(registry, lease_ttl_seconds=31)
    registry._server_read_time = _NOW + timedelta(seconds=32)

    def observe(_service_name):
        state = registry.get_service_release_state("predictor")
        registry.put_service_release_state(
            type(state)(
                **{
                    **state.__dict__,
                    "revision": state.revision + 1,
                    "updated_at": registry.get_server_read_time().isoformat(),
                }
            )
        )
        return ReleaseExternalState(service_resource_version="52")

    with pytest.raises(ReleaseLeaseConflict, match="changed after"):
        _acquire(
            registry,
            idempotency_key="publish-2",
            owner_principal="publisher-b@example.test",
            read_external_state=observe,
        )
    assert registry.get_release_operation(old.operation_id) == old


def test_heartbeat_uses_server_time_and_cannot_revive_expired_lease():
    registry = _registry()
    operation = _acquire(registry)
    registry._server_read_time = _NOW + timedelta(seconds=30)

    renewed = heartbeat_release_lease(
        registry,
        operation_id=operation.operation_id,
        owner_principal=operation.owner_principal,
        fencing_token=operation.fencing_token,
    )
    assert renewed.lease_expires_at == (
        registry.get_server_read_time()
        + timedelta(seconds=DEFAULT_RELEASE_LEASE_TTL_SECONDS)
    ).isoformat()

    registry._server_read_time = datetime.fromisoformat(renewed.lease_expires_at)
    with pytest.raises(ReleaseLeaseFenced, match="stale"):
        heartbeat_release_lease(
            registry,
            operation_id=renewed.operation_id,
            owner_principal=renewed.owner_principal,
            fencing_token=renewed.fencing_token,
        )


def test_old_owner_is_fenced_from_heartbeat_finalize_and_compensate_guards():
    registry = _registry()
    old = _acquire(registry, lease_ttl_seconds=31)
    registry._server_read_time = _NOW + timedelta(seconds=32)
    _acquire(
        registry,
        idempotency_key="publish-2",
        owner_principal="publisher-b@example.test",
        read_external_state=lambda _name: ReleaseExternalState(),
    )

    for action in (require_release_lease, heartbeat_release_lease):
        with pytest.raises(ReleaseLeaseFenced):
            action(
                registry,
                operation_id=old.operation_id,
                owner_principal=old.owner_principal,
                fencing_token=old.fencing_token,
            )


def test_release_lease_can_reserve_uncommitted_target_and_rejects_bad_ttl():
    registry = _registry()
    uncommitted = _release().release_id
    empty_registry = FakeRegistryV2(server_read_time=_NOW)
    assert _acquire(empty_registry, target_release_id=uncommitted).target_release_id == uncommitted
    with pytest.raises(ValueError, match="heartbeat interval"):
        _acquire(registry, lease_ttl_seconds=30)


def test_reconciler_takes_over_same_expired_operation_with_new_fence():
    registry = _registry()
    old = _acquire(registry, lease_ttl_seconds=31)
    registry._server_read_time = _NOW + timedelta(seconds=32)
    calls = []

    def observe(service_name):
        calls.append(service_name)
        return ReleaseExternalState(
            deployment_uid="uid-1",
            deployment_resource_version="41",
            service_resource_version="52",
        )

    taken_over = takeover_release_lease(
        registry,
        operation_id=old.operation_id,
        owner_principal="reconciler@example.test",
        read_external_state=observe,
    )

    assert calls == ["predictor"]
    assert taken_over.operation_id == old.operation_id
    assert taken_over.phase.value == "reconciling"
    assert taken_over.fencing_token == old.fencing_token + 1
    assert taken_over.expected_deployment_uid == "uid-1"
    assert registry.get_service_release_state("predictor").fencing_token == (
        taken_over.fencing_token
    )

    retry = takeover_release_lease(
        registry,
        operation_id=old.operation_id,
        owner_principal="reconciler@example.test",
        read_external_state=lambda _name: pytest.fail("must not reread on retry"),
    )
    assert retry == taken_over


def test_reconciler_cannot_take_over_a_live_or_changed_operation():
    registry = _registry()
    operation = _acquire(registry, lease_ttl_seconds=31)
    with pytest.raises(ReleaseLeaseBusy, match="live"):
        takeover_release_lease(
            registry,
            operation_id=operation.operation_id,
            owner_principal="reconciler@example.test",
            read_external_state=lambda _name: ReleaseExternalState(),
        )

    registry._server_read_time = _NOW + timedelta(seconds=32)

    def mutate(_service_name):
        current = registry.get_service_release_state("predictor")
        registry.put_service_release_state(
            type(current)(
                **{**current.__dict__, "revision": current.revision + 1}
            )
        )
        return ReleaseExternalState()

    with pytest.raises(ReleaseLeaseConflict, match="changed after"):
        takeover_release_lease(
            registry,
            operation_id=operation.operation_id,
            owner_principal="reconciler@example.test",
            read_external_state=mutate,
        )


def test_terminal_operation_allows_observed_successor_with_new_fence():
    registry = _registry()
    operation = _acquire(registry)
    state = registry.get_service_release_state("predictor")
    reconciling_state = replace(
        state,
        revision=state.revision + 1,
        desired_release_id=operation.target_release_id,
        desired_revision=1,
        observed_release_id=operation.target_release_id,
        observed_evidence_revision=1,
        traffic_release_id=operation.target_release_id,
        traffic_k8s_resource_version="52",
        champion_release_id=operation.target_release_id,
        active_operation_phase=ReleasePhase.RECONCILING,
    )
    reconciling = replace(
        operation,
        phase=ReleasePhase.RECONCILING,
        revision=operation.revision + 1,
        expected_service_revision=reconciling_state.revision,
    )
    registry.put_release_operation(reconciling)
    registry.put_service_release_state(reconciling_state)
    succeeded_state = replace(
        reconciling_state,
        revision=reconciling_state.revision + 1,
        active_operation_phase=ReleasePhase.SUCCEEDED,
    )
    succeeded = replace(
        reconciling,
        phase=ReleasePhase.SUCCEEDED,
        revision=reconciling.revision + 1,
        expected_service_revision=succeeded_state.revision,
        lease_expires_at=None,
        finished_at=_NOW.isoformat(),
    )
    registry.put_release_operation(succeeded)
    registry.put_service_release_state(succeeded_state)

    successor = _acquire(
        registry,
        idempotency_key="publish-2",
        owner_principal="publisher-b@example.test",
        read_external_state=lambda _name: ReleaseExternalState(
            service_resource_version="52"
        ),
    )

    assert successor.operation_id != succeeded.operation_id
    assert successor.fencing_token == succeeded.fencing_token + 1
    assert successor.expected_service_resource_version == "52"
    assert registry.get_release_operation(succeeded.operation_id) == succeeded

from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_drain import (
    ReleaseDrainUncertain,
    complete_release_drain,
)
from tests.management.v2.test_release_finalize import _finalize, _switched


def _finalized():
    values, port, traffic_switch = _switched()
    finalized = _finalize(values, port, traffic_switch)
    return values, port, finalized


def _drain(values, port, finalized, **overrides):
    deployed = values[5]
    arguments = dict(
        manifest=values[4],
        finalize=finalized,
        operation_id=deployed.operation.operation_id,
        owner_principal=deployed.operation.owner_principal,
        fencing_token=deployed.operation.fencing_token,
        evidence_issuer_principal="controller@example.test",
    )
    arguments.update(overrides)
    return complete_release_drain(values[0], port, **arguments)


def _add_previous_release(values, port, finalized, *, connections, close_per_drain):
    registry = values[0]
    previous_id = TypedId.from_bare("ab" * 32)
    candidate = values[5].deployment
    old_request = replace(
        candidate.request,
        name=f"release-{previous_id.bare}",
        release_id=previous_id,
        image_reference=f"repo/predictor@{previous_id.typed}",
        manifest_digest=previous_id,
        deployment_spec_digest=TypedId.from_bare("ac" * 32),
        fencing_token=1,
    )
    old_deployment = port.create_deployment(old_request)
    port.set_active_connections(
        old_deployment.uid,
        count=connections,
        close_per_drain=close_per_drain,
    )

    def update(tx):
        operation = tx.get_release_operation(finalized.operation.operation_id)
        state = tx.get_service_release_state("predictor")
        new_state = replace(
            state,
            revision=state.revision + 1,
            previous_release_id=previous_id,
        )
        tx.put_release_operation(
            replace(
                operation,
                revision=operation.revision + 1,
                expected_service_revision=new_state.revision,
            )
        )
        tx.put_service_release_state(new_state)

    registry.run_atomic(update)
    return old_deployment


def test_selector_switch_alone_is_nonterminal_then_noop_drain_succeeds():
    values, port, finalized = _finalized()
    assert finalized.operation.phase.value == "finalizing"
    assert finalized.operation.finished_at is None

    result = _drain(values, port, finalized)

    assert result.operation.phase.value == "succeeded"
    assert result.operation.drain_evidence_id == result.drain_evidence.evidence_id
    assert result.service_state.active_operation_phase.value == "succeeded"
    assert result.service_state.desired_release_id == values[4].release_id
    assert result.service_state.traffic_release_id == values[4].release_id
    assert result.service_state.champion_release_id == values[4].release_id


def test_previous_release_connections_reach_zero_before_success():
    values, port, finalized = _finalized()
    old = _add_previous_release(
        values,
        port,
        finalized,
        connections=2,
        close_per_drain=1,
    )

    result = _drain(values, port, finalized, max_drain_reads=2)

    assert result.operation.phase.value == "succeeded"
    assert "active-connections=0" in result.drain_evidence.binding_tuple
    assert port.drain(old.uid).active_connections == 0


def test_drain_timeout_remains_nonterminal_and_reconciling():
    values, port, finalized = _finalized()
    _add_previous_release(
        values,
        port,
        finalized,
        connections=5,
        close_per_drain=1,
    )

    with pytest.raises(ReleaseDrainUncertain, match="read bound"):
        _drain(values, port, finalized, max_drain_reads=2)

    operation = values[0].get_release_operation(finalized.operation.operation_id)
    state = values[0].get_service_release_state("predictor")
    assert operation.phase.value == "reconciling"
    assert operation.finished_at is None
    assert operation.drain_evidence_id is None
    assert state.active_operation_phase.value == "reconciling"
    assert port.get_service("predictor").traffic_release_id == values[4].release_id

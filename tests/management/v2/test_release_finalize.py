from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.fake_registry import IdentityConflict
from aigear.management.v2.release_finalize import (
    ReleaseFinalizeUncertain,
    finalize_release_traffic,
)
from aigear.management.v2.release_kubernetes import ServiceTrafficPatch
from tests.management.v2.test_traffic_switch import _switch, _verified


def _switched():
    values, port, verification = _verified()
    traffic_switch = _switch(values, port, verification)
    return values, port, traffic_switch


def _finalize(values, port, traffic_switch):
    deployed = values[5]
    return finalize_release_traffic(
        values[0],
        port,
        manifest=values[4],
        traffic_switch=traffic_switch,
        operation_id=deployed.operation.operation_id,
        owner_principal=deployed.operation.owner_principal,
        fencing_token=deployed.operation.fencing_token,
    )


def test_finalize_atomically_aligns_all_registry_traffic_facts():
    values, port, traffic_switch = _switched()

    result = _finalize(values, port, traffic_switch)

    state = result.service_state
    assert result.operation.phase.value == "finalizing"
    assert state.desired_release_id == values[4].release_id
    assert state.observed_release_id == values[4].release_id
    assert state.traffic_release_id == values[4].release_id
    assert state.champion_release_id == values[4].release_id
    assert state.traffic_k8s_resource_version == (
        traffic_switch.service_after.resource_version
    )
    assert state.active_operation_id == result.operation.operation_id


def test_registry_finalize_failure_conditionally_rolls_back_without_drain():
    values, port, traffic_switch = _switched()
    registry = values[0]
    original = registry.put_service_release_state

    def fail_finalizing(record):
        if record.active_operation_phase.value == "finalizing":
            raise IdentityConflict("injected finalize failure")
        return original(record)

    registry.put_service_release_state = fail_finalizing

    with pytest.raises(ReleaseFinalizeUncertain, match="reconciliation"):
        _finalize(values, port, traffic_switch)

    operation = registry.get_release_operation(values[5].operation.operation_id)
    state = registry.get_service_release_state("predictor")
    deployment = port.get_deployment(values[5].deployment.request.name)
    assert operation.phase.value == "reconciling"
    assert state.active_operation_phase.value == "reconciling"
    assert state.traffic_release_id is None
    assert port.get_service("predictor").traffic_release_id is None
    assert deployment.available_replicas == deployment.request.replicas


def test_concurrent_service_change_prevents_blind_finalize_or_rollback():
    values, port, traffic_switch = _switched()
    current = port.get_service("predictor")
    port.patch_service_traffic(
        ServiceTrafficPatch(
            service_name=current.service_name,
            expected_uid=current.uid,
            expected_resource_version=current.resource_version,
            target_release_id=current.traffic_release_id,
            fencing_token=current.fencing_token,
        )
    )

    with pytest.raises(ReleaseFinalizeUncertain, match="reconciliation"):
        _finalize(values, port, traffic_switch)

    assert values[0].get_release_operation(
        values[5].operation.operation_id
    ).phase.value == "reconciling"
    assert port.get_service("predictor").traffic_release_id == values[4].release_id
    assert values[0].get_service_release_state("predictor").traffic_release_id is None


def test_tampered_switch_evidence_is_not_finalized():
    values, port, traffic_switch = _switched()
    tampered = replace(
        traffic_switch,
        service_after=replace(
            traffic_switch.service_after,
            resource_version="tampered-rv",
        ),
    )

    with pytest.raises(ReleaseFinalizeUncertain, match="reconciliation"):
        _finalize(values, port, tampered)

    assert values[0].get_service_release_state("predictor").traffic_release_id is None

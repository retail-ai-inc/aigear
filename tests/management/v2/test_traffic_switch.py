from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.release_kubernetes import (
    FakeKubernetesReleasePort,
    MutationFault,
)
from aigear.management.v2.traffic_switch import (
    TrafficSwitchUncertain,
    switch_release_traffic,
)
from tests.management.v2.test_release_verify import (
    _inputs,
    _probe_result,
    _verify,
)


def _verified():
    values, startup, port = _inputs()
    port.set_probe_result(
        startup.observed_evidence.pod_uid, _probe_result(values[4])
    )
    verification = _verify(values, startup, port)
    return values, port, verification


def _switch(values, port, verification, **overrides):
    deployed = values[5]
    arguments = dict(
        manifest=values[4],
        verification=verification,
        operation_id=deployed.operation.operation_id,
        owner_principal=deployed.operation.owner_principal,
        fencing_token=deployed.operation.fencing_token,
        evidence_issuer_principal="controller@example.test",
    )
    arguments.update(overrides)
    return switch_release_traffic(values[0], port, **arguments)


def test_conditional_switch_records_exact_before_and_after_evidence():
    values, port, verification = _verified()

    result = _switch(values, port, verification)

    assert result.operation.phase.value == "switching_traffic"
    assert result.operation.expected_service_resource_version == (
        result.service_after.resource_version
    )
    assert result.operation.traffic_evidence_id == result.traffic_evidence.evidence_id
    assert result.service_before.traffic_release_id is None
    assert result.service_after.traffic_release_id == values[4].release_id
    bindings = " ".join(result.traffic_evidence.binding_tuple)
    assert result.endpoint_slice_before.resource_version in bindings
    assert result.endpoint_slice_after.resource_version in bindings
    assert result.service_state.traffic_release_id is None
    assert result.service_state.champion_release_id is None


def test_ack_loss_enters_reconciling_without_blind_patch_retry():
    values, port, verification = _verified()
    port.queue_fault("patch_service_traffic", MutationFault.ACK_LOST_AFTER_COMMIT)

    with pytest.raises(TrafficSwitchUncertain, match="reconciliation"):
        _switch(values, port, verification)

    operation = values[0].get_release_operation(values[5].operation.operation_id)
    assert operation.phase.value == "reconciling"
    assert port.get_service("predictor").traffic_release_id == values[4].release_id
    assert values[0].get_service_release_state("predictor").traffic_release_id is None


def test_endpoint_nonconvergence_enters_reconciling():
    values, port, verification = _verified()
    port.configure_endpoint_delay(values[4].release_id, reads=5)

    with pytest.raises(TrafficSwitchUncertain, match="convergence"):
        _switch(values, port, verification, max_endpoint_reads=2)

    assert values[0].get_release_operation(
        values[5].operation.operation_id
    ).phase.value == "reconciling"
    assert values[0].get_service_release_state("predictor").traffic_release_id is None


class _ConcurrentServicePort(FakeKubernetesReleasePort):
    def patch_service_traffic(self, patch):
        current = self.get_service(patch.service_name)
        self._services[patch.service_name] = replace(
            current,
            resource_version=self._next_resource_version(),
        )
        return super().patch_service_traffic(patch)


def test_concurrent_service_change_fails_the_resource_version_cas():
    values, old_port, verification = _verified()
    port = _ConcurrentServicePort()
    port.seed_service("predictor")
    port.create_deployment(values[5].deployment.request)
    assert port.get_service("predictor") == old_port.get_service("predictor")

    with pytest.raises(TrafficSwitchUncertain, match="reconciliation"):
        _switch(values, port, verification)

    assert values[0].get_release_operation(
        values[5].operation.operation_id
    ).phase.value == "reconciling"
    assert port.get_service("predictor").traffic_release_id is None

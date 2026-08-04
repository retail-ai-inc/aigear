from __future__ import annotations

from datetime import datetime, timedelta

from aigear.management.v2.release_kubernetes import ServiceTrafficPatch
from aigear.management.v2.release_reconcile import (
    ReleaseReconcileAction,
    reconcile_release_once,
)
from tests.management.v2.test_release_drain import _finalized
from tests.management.v2.test_release_finalize import _switched
from tests.management.v2.test_release_verify import _inputs, _probe_result, _verify


def _expire(registry, operation):
    registry._server_read_time = datetime.fromisoformat(
        operation.lease_expires_at
    ) + timedelta(seconds=1)


def _reconcile(values, port, **overrides):
    operation = values[0].get_release_operation(values[5].operation.operation_id)
    arguments = dict(
        operation_id=operation.operation_id,
        owner_principal="reconciler@example.test",
        evidence_issuer_principal="reconciler@example.test",
    )
    arguments.update(overrides)
    return reconcile_release_once(values[0], port, **arguments)


def test_reconcile_switch_crash_converges_and_second_run_is_noop():
    values, port, traffic_switch = _switched()
    _expire(values[0], traffic_switch.operation)

    result = _reconcile(values, port)

    assert result.actions == (
        ReleaseReconcileAction.ADOPT_FENCE,
        ReleaseReconcileAction.FINALIZE_TRAFFIC,
        ReleaseReconcileAction.COMPLETE_DRAIN,
    )
    assert result.operation.phase.value == "succeeded"
    retry = _reconcile(values, port)
    assert retry.actions == ()
    assert retry.operation == result.operation


def test_selector_without_traffic_evidence_is_conditionally_compensated():
    values, startup, port = _inputs()
    port.set_probe_result(
        startup.observed_evidence.pod_uid, _probe_result(values[4])
    )
    verification = _verify(values, startup, port)
    service = port.get_service("predictor")
    port.patch_service_traffic(
        ServiceTrafficPatch(
            service_name=service.service_name,
            expected_uid=service.uid,
            expected_resource_version=service.resource_version,
            target_release_id=values[4].release_id,
            fencing_token=verification.operation.fencing_token,
        )
    )
    _expire(values[0], verification.operation)

    result = _reconcile(values, port)

    assert result.actions == (ReleaseReconcileAction.COMPENSATE_TRAFFIC,)
    assert result.operation.phase.value == "reconciling"
    assert port.get_service("predictor").traffic_release_id is None
    retry = _reconcile(values, port)
    assert retry.actions == ()
    assert retry.operation.phase.value == "reconciling"


def test_unknown_early_crash_never_fabricates_a_terminal_result():
    values, startup, port = _inputs()
    operation = values[5].operation
    _expire(values[0], operation)

    first = _reconcile(values, port)
    second = _reconcile(values, port)

    assert first.actions == ()
    assert first.unknown is True
    assert second.actions == ()
    assert second.operation == first.operation
    assert second.operation.phase.value == "reconciling"
    assert second.operation.finished_at is None


def test_reconcile_finalized_crash_completes_drain_and_success():
    values, port, finalized = _finalized()
    _expire(values[0], finalized.operation)

    result = _reconcile(values, port)

    assert result.actions == (
        ReleaseReconcileAction.ADOPT_FENCE,
        ReleaseReconcileAction.COMPLETE_DRAIN,
    )
    assert result.operation.phase.value == "succeeded"


def test_max_actions_stops_a_pass_at_the_requested_boundary():
    values, port, traffic_switch = _switched()
    _expire(values[0], traffic_switch.operation)

    result = _reconcile(values, port, max_actions=1)

    assert result.actions == (ReleaseReconcileAction.ADOPT_FENCE,)
    assert result.operation.phase.value == "reconciling"
    assert result.operation.finished_at is None

from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.release import (
    AliasRecord,
    InvalidReleasePhaseTransitionError,
    InvalidReleaseRecordError,
    ReleaseOperationRecord,
    ReleasePhase,
    ReleaseRecord,
    ServiceReleaseState,
    validate_release_phase_transition,
)

_FP = TypedId.from_bare("aa" * 32)
_RELEASE = TypedId.from_bare("bb" * 32)
_OLD = TypedId.from_bare("cc" * 32)
_DIGEST = TypedId.from_bare("dd" * 32)


@pytest.mark.parametrize(
    "current,target",
    [
        (ReleasePhase.RESERVED, ReleasePhase.PREPARING),
        (ReleasePhase.PREPARING, ReleasePhase.DEPLOYING),
        (ReleasePhase.DEPLOYING, ReleasePhase.VERIFYING),
        (ReleasePhase.VERIFYING, ReleasePhase.SWITCHING_TRAFFIC),
        (ReleasePhase.SWITCHING_TRAFFIC, ReleasePhase.FINALIZING),
        (ReleasePhase.FINALIZING, ReleasePhase.DRAINING),
        (ReleasePhase.DRAINING, ReleasePhase.SUCCEEDED),
        (ReleasePhase.VERIFYING, ReleasePhase.COMPENSATING),
        (ReleasePhase.COMPENSATING, ReleasePhase.ROLLED_BACK),
        (ReleasePhase.DEPLOYING, ReleasePhase.RECONCILING),
        (ReleasePhase.RECONCILING, ReleasePhase.DRAINING),
    ],
)
def test_valid_release_transitions(current, target):
    validate_release_phase_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (ReleasePhase.RESERVED, ReleasePhase.DEPLOYING),
        (ReleasePhase.VERIFYING, ReleasePhase.SUCCEEDED),
        (ReleasePhase.COMPENSATING, ReleasePhase.SUCCEEDED),
        (ReleasePhase.SUCCEEDED, ReleasePhase.RECONCILING),
        (ReleasePhase.ROLLED_BACK, ReleasePhase.RESERVED),
        (ReleasePhase.FAILED, ReleasePhase.COMPENSATING),
    ],
)
def test_invalid_release_transitions(current, target):
    with pytest.raises(InvalidReleasePhaseTransitionError):
        validate_release_phase_transition(current, target)


def test_release_record_exposes_stable_immutable_identity():
    record = ReleaseRecord(
        schema_version="2.0",
        environment_fingerprint=_FP,
        release_id=_RELEASE,
        service_name="predictor",
        deployment_target_id="prod-cluster",
        manifest_digest=_DIGEST,
        signature_attestation_id=_DIGEST,
        creation_operation_id="release-op-1",
        display_version="service-v1",
    )
    assert record.release_id in record.immutable_identity


def test_alias_requires_positive_revision():
    with pytest.raises(InvalidReleaseRecordError, match="revision"):
        AliasRecord(
            schema_version="2.0",
            environment_fingerprint=_FP,
            service_name="predictor",
            alias_name="champion",
            release_id=_RELEASE,
            revision=0,
            updated_by_operation_id="release-op-1",
        )


def _service_state(**overrides) -> ServiceReleaseState:
    values = {
        "schema_version": "2.0",
        "environment_fingerprint": _FP,
        "service_name": "predictor",
        "revision": 2,
        "display_version_counter": 1,
        "desired_release_id": _RELEASE,
        "desired_revision": 2,
        "observed_release_id": _OLD,
        "observed_evidence_revision": 1,
        "traffic_release_id": _OLD,
        "traffic_k8s_resource_version": "100",
        "champion_release_id": _OLD,
        "active_operation_id": "release-op-1",
        "active_operation_phase": ReleasePhase.DEPLOYING,
        "fencing_token": 3,
    }
    values.update(overrides)
    return ServiceReleaseState(**values)


def test_nonterminal_service_state_exposes_three_distinct_facts():
    state = _service_state()
    assert state.desired_release_id == _RELEASE
    assert state.observed_release_id == _OLD
    assert state.traffic_release_id == _OLD


def test_succeeded_service_state_requires_all_release_facts_equal():
    with pytest.raises(InvalidReleaseRecordError, match="equality"):
        _service_state(active_operation_phase=ReleasePhase.SUCCEEDED)
    state = _service_state(
        active_operation_phase=ReleasePhase.SUCCEEDED,
        observed_release_id=_RELEASE,
        traffic_release_id=_RELEASE,
        champion_release_id=_RELEASE,
    )
    assert state.champion_release_id == _RELEASE


def test_service_state_rejects_partial_operation_binding():
    with pytest.raises(InvalidReleaseRecordError, match="set together"):
        _service_state(active_operation_phase=None)


def test_service_state_rejects_traffic_without_resource_version():
    with pytest.raises(InvalidReleaseRecordError, match="traffic_k8s_resource_version"):
        _service_state(traffic_k8s_resource_version=None)


def _operation(**overrides) -> ReleaseOperationRecord:
    values = {
        "schema_version": "2.0",
        "environment_fingerprint": _FP,
        "operation_id": "release-op-1",
        "idempotency_key_hash": "ab" * 32,
        "request_fingerprint": _DIGEST,
        "service_name": "predictor",
        "target_release_id": _RELEASE,
        "phase": ReleasePhase.DEPLOYING,
        "owner_principal": "deployer@example.iam.gserviceaccount.com",
        "fencing_token": 3,
        "revision": 2,
        "expected_service_revision": 2,
        "lease_expires_at": "2026-07-28T00:02:00+00:00",
    }
    values.update(overrides)
    return ReleaseOperationRecord(**values)


def test_release_operation_rejects_naive_lease():
    with pytest.raises(InvalidReleaseRecordError, match="timezone-aware"):
        _operation(lease_expires_at="2026-07-28T00:02:00")


def test_terminal_release_operation_requires_finished_at():
    with pytest.raises(InvalidReleaseRecordError, match="finished_at"):
        _operation(phase=ReleasePhase.SUCCEEDED)
    operation = _operation(
        phase=ReleasePhase.SUCCEEDED,
        finished_at="2026-07-28T00:03:00+00:00",
    )
    assert operation.phase == ReleasePhase.SUCCEEDED


def test_release_operation_error_fields_are_atomic():
    with pytest.raises(InvalidReleaseRecordError, match="both"):
        _operation(error_class="Timeout")


def test_terminal_release_operation_cannot_reopen():
    operation = _operation(
        phase=ReleasePhase.ROLLED_BACK,
        finished_at="2026-07-28T00:03:00+00:00",
    )
    with pytest.raises(InvalidReleasePhaseTransitionError):
        validate_release_phase_transition(operation.phase, ReleasePhase.RECONCILING)


def test_replacing_service_state_with_invalid_terminal_facts_fails_closed():
    state = _service_state()
    with pytest.raises(InvalidReleaseRecordError, match="equality"):
        replace(state, active_operation_phase=ReleasePhase.SUCCEEDED)

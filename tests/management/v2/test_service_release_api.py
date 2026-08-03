from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from aigear.management.pipeline_asset import (
    PipelineAssetManagement,
    PipelineAssetManagementError,
)
from aigear.management.v2.attestation import HmacTestVerifier
from aigear.management.v2.environment import EnvironmentIdentity
from tests.management.v2.test_release_prepare import (
    _RELEASE_KEY,
    _inputs as _prepare_inputs,
)
from tests.management.v2.test_release_rollback import (
    _rollback_inputs,
)
from tests.management.v2.test_release_finalize import _switched
from tests.management.v2.test_service_runtime import _runtime_inputs


_CAPABILITIES = (
    "pipeline_v2",
    "service_release_v2",
    "service_runtime_authorization_v2",
)


def _manager(registry, control, layout, *, port=None):
    control = replace(control, required_capabilities=_CAPABILITIES)
    registry.control = control
    identity = EnvironmentIdentity(
        environment_id=control.environment_id,
        gcp_project_number="123456789012",
        project_name=layout.project_name,
        pipeline_version=layout.pipeline_version,
        asset_bucket_name=layout.bucket_name,
        asset_bucket_location="asia-east1",
        kms_trust_domain="projects/p/locations/l/keyRings/r",
    )
    manager = PipelineAssetManagement(
        identity,
        registry=registry,
        control_document=control,
        attestation_verifier=HmacTestVerifier(),
        release_attestation_verifier=HmacTestVerifier(
            key_version=_RELEASE_KEY
        ),
        release_kubernetes=port,
    )
    # The shared release fixtures intentionally use a fixed synthetic digest.
    manager.environment_fingerprint = control.environment_fingerprint
    manager.layout = layout
    return manager


def _prepare_api(manager, runtime, manifest, **overrides):
    arguments = dict(
        expected_runtime_contract=runtime,
        deployment_target_id="prod-cluster",
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        idempotency_key="api-publish-1",
        actor="publisher@example.test",
        reason="publish approved release",
        expected_revision=0,
    )
    arguments.update(overrides)
    return manager.prepare_service_release(manifest, **arguments)


def test_prepare_and_status_return_authoritative_nonterminal_operation():
    registry, control, layout, _asset, runtime, manifest = _prepare_inputs()
    manager = _manager(registry, control, layout)

    first = _prepare_api(manager, runtime, manifest)
    retry = _prepare_api(manager, runtime, manifest)
    status = manager.get_service_release_status("predictor")

    assert retry == first
    assert first.phase.value == "preparing"
    assert status.operation == first
    assert status.state.desired_release_id == manifest.release_id
    assert status.state.observed_release_id is None
    assert status.state.traffic_release_id is None
    assert status.state.champion_release_id is None


def test_release_api_authority_capability_and_envelope_fail_closed():
    registry, control, layout, _asset, runtime, manifest = _prepare_inputs()
    manager = _manager(registry, control, layout)
    manager.control_document = replace(
        manager.control_document,
        required_capabilities=("pipeline_v2",),
    )
    registry.control = manager.control_document

    with pytest.raises(PipelineAssetManagementError, match="capability"):
        _prepare_api(manager, runtime, manifest)
    assert registry.get_service_release_state("predictor") is None

    manager.control_document = replace(
        manager.control_document,
        required_capabilities=_CAPABILITIES,
        authority="v1",
        phase="v1_only",
    )
    registry.control = manager.control_document
    with pytest.raises(PipelineAssetManagementError, match="not writable"):
        _prepare_api(manager, runtime, manifest)
    assert registry.get_service_release_state("predictor") is None


def test_release_mutations_require_reason_and_expected_revision():
    registry, control, layout, _asset, runtime, manifest = _prepare_inputs()
    manager = _manager(registry, control, layout)
    with pytest.raises(PipelineAssetManagementError, match="reason"):
        _prepare_api(manager, runtime, manifest, reason="")
    with pytest.raises(PipelineAssetManagementError, match="expected revision"):
        _prepare_api(manager, runtime, manifest, expected_revision=2)


def test_rollback_api_returns_the_new_prepare_operation():
    values = _rollback_inputs()
    registry, control, layout, _asset, runtime, manifest, state = values
    manager = _manager(registry, control, layout)

    operation = manager.rollback_service_release(
        manifest,
        expected_runtime_contract=runtime,
        deployment_target_id="prod-cluster",
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        verify_current_image=lambda _value, _at: None,
        verify_current_config_reference=lambda _value, _at: None,
        idempotency_key="api-rollback-1",
        actor="operator@example.test",
        reason="restore prior champion",
        expected_revision=state.revision,
    )

    assert operation.phase.value == "preparing"
    assert operation.target_release_id == manifest.release_id


def test_reconcile_api_exposes_the_terminal_operation():
    values, port, traffic_switch = _switched()
    registry, control, layout = values[:3]
    registry._server_read_time = datetime.fromisoformat(
        traffic_switch.operation.lease_expires_at
    ) + timedelta(seconds=1)
    manager = _manager(registry, control, layout, port=port)
    state = registry.get_service_release_state("predictor")

    operation = manager.reconcile_service_release(
        "predictor",
        operation_id=traffic_switch.operation.operation_id,
        idempotency_key="api-reconcile-1",
        actor="reconciler@example.test",
        reason="recover interrupted traffic switch",
        expected_revision=state.revision,
    )

    assert operation.phase.value == "succeeded"
    assert operation.finished_at is not None


def test_runtime_authorization_api_is_idempotent_at_same_registry_time():
    values = _runtime_inputs()
    registry, control, layout, runtime, manifest, _deployed, observation, journal = values
    manager = _manager(registry, control, layout)
    state = registry.get_service_release_state("predictor")
    arguments = dict(
        deployment_target_id="prod-cluster",
        pod_uid=observation.pod_uid,
        expected_runtime_contract=runtime,
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        journal=journal,
        idempotency_key="runtime-auth-1",
        actor="runtime-controller@example.test",
        reason="authorize exact ready pod",
        expected_revision=state.revision,
    )

    first = manager.issue_service_runtime_authorization(manifest, **arguments)
    second = manager.issue_service_runtime_authorization(manifest, **arguments)

    assert second == first
    assert first.release_id == manifest.release_id

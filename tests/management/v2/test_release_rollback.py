from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.attestation import HmacTestVerifier
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import TrustState
from aigear.management.v2.records.release import ServiceReleaseState
from aigear.management.v2.release_lease import (
    ReleaseExternalState,
    acquire_release_lease,
)
from aigear.management.v2.release_rollback import (
    ReleaseRollbackConflict,
    ReleaseRollbackError,
    rollback_release,
)
from tests.management.v2.test_release_prepare import (
    _RELEASE_KEY,
    _inputs,
)
from tests.management.v2.test_resolver import _NOW


def _rollback_inputs():
    registry, control, layout, asset, runtime, manifest = _inputs()
    target = manifest.to_release_record(
        creation_operation_id="release-history-1",
        display_version="service-v1",
        created_at=_NOW.isoformat(),
    )
    registry.put_release(target)
    current_id = TypedId.from_bare("10" * 32)
    registry.put_release(
        replace(
            target,
            release_id=current_id,
            manifest_digest=current_id,
            signature_attestation_id=TypedId.from_bare("11" * 32),
            creation_operation_id="release-current-2",
            display_version="service-v2",
        )
    )
    state = ServiceReleaseState(
        schema_version="2.0",
        environment_fingerprint=control.environment_fingerprint,
        service_name="predictor",
        revision=7,
        display_version_counter=2,
        desired_release_id=current_id,
        desired_revision=2,
        observed_release_id=current_id,
        observed_evidence_revision=2,
        traffic_release_id=current_id,
        traffic_k8s_resource_version="52",
        champion_release_id=current_id,
        previous_release_id=manifest.release_id,
        fencing_token=5,
        updated_at=_NOW.isoformat(),
    )
    registry.put_service_release_state(state)
    return registry, control, layout, asset, runtime, manifest, state


def _rollback(values, **overrides):
    registry, control, layout, _asset, runtime, manifest, state = values
    arguments = dict(
        control=control,
        layout=layout,
        manifest=manifest,
        service_name="predictor",
        deployment_target_id="prod-cluster",
        expected_service_revision=state.revision,
        expected_runtime_contract=runtime,
        asset_attestation_verifier=HmacTestVerifier(),
        release_attestation_verifier=HmacTestVerifier(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        verify_current_image=lambda _image, _at: None,
        verify_current_config_reference=lambda _reference, _at: None,
        idempotency_key="rollback-1",
        owner_principal="operator@example.test",
        read_external_state=lambda _name: ReleaseExternalState(
            service_resource_version="52"
        ),
    )
    arguments.update(overrides)
    return rollback_release(registry, **arguments)


def test_exact_previous_release_reuses_normal_prepare_saga():
    values = _rollback_inputs()

    result = _rollback(values)

    assert result.from_release_id == values[6].champion_release_id
    assert result.target_release.release_id == values[5].release_id
    assert result.prepared.operation.phase.value == "preparing"
    assert result.prepared.operation.fencing_token == values[6].fencing_token + 1
    assert result.prepared.service_state.desired_release_id == values[5].release_id
    assert result.prepared.service_state.traffic_release_id == (
        values[6].traffic_release_id
    )


def test_revoked_or_expired_historical_release_cannot_roll_back():
    values = _rollback_inputs()
    registry, _control, _layout, asset, _runtime, _manifest, _state = values
    registry.put_asset_version(replace(asset, trust_state=TrustState.REVOKED))

    with pytest.raises(ReleaseRollbackError, match="current release preparation"):
        _rollback(values)


def test_expired_policy_head_blocks_historical_rollback():
    values = _rollback_inputs()
    registry, _control, _layout, asset, _runtime, _manifest, _state = values
    head = registry.get_policy_decision_head(asset.asset_version_id)
    registry.put_policy_decision_head(
        replace(
            head,
            revision=head.revision + 1,
            valid_until=_NOW.isoformat(),
        )
    )

    with pytest.raises(ReleaseRollbackError, match="current release preparation"):
        _rollback(values)


@pytest.mark.parametrize("kind", ["image", "config"])
def test_current_vulnerable_image_or_drifted_config_blocks_rollback(kind):
    values = _rollback_inputs()

    def reject(_value, _at):
        raise ValueError("current policy rejected")

    overrides = (
        {"verify_current_image": reject}
        if kind == "image"
        else {"verify_current_config_reference": reject}
    )
    with pytest.raises(ReleaseRollbackError, match="no longer allowed"):
        _rollback(values, **overrides)

    assert values[0].get_service_release_state("predictor") == values[6]


def test_wrong_manifest_or_stale_revision_fails_before_lease_acquisition():
    values = _rollback_inputs()
    with pytest.raises(ReleaseRollbackConflict, match="expected revision"):
        _rollback(values, expected_service_revision=6)
    with pytest.raises(ReleaseRollbackConflict, match="exact target"):
        _rollback(
            values,
            target_release_id=TypedId.from_bare("12" * 32),
        )
    assert values[0].get_release_operation("rollback-1") is None


def test_concurrent_normal_publish_holds_the_same_service_lease():
    values = _rollback_inputs()
    registry, control, _layout, _asset, _runtime, manifest, _state = values
    acquire_release_lease(
        registry,
        environment_fingerprint=control.environment_fingerprint,
        service_name="predictor",
        target_release_id=manifest.release_id,
        idempotency_key="normal-publish",
        owner_principal="publisher@example.test",
    )
    current = registry.get_service_release_state("predictor")

    with pytest.raises(ReleaseRollbackError, match="current release preparation"):
        _rollback(values, expected_service_revision=current.revision)

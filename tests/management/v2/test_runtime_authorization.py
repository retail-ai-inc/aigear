from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from aigear.management.v2.attestation import HmacTestVerifier
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import TrustState
from aigear.management.v2.runtime_authorization import (
    RuntimeAuthorizationConflict,
    RuntimeAuthorizationError,
    RuntimeAuthorizationExpired,
    VerifiedJournalWatermark,
    issue_runtime_authorization,
    renew_runtime_authorization,
    require_runtime_readiness,
)
from tests.management.v2.test_release_prepare import (
    _RELEASE_KEY,
    _inputs,
    _prepare,
)
from tests.management.v2.test_resolver import _NOW


def _ready_inputs():
    registry, control, layout, asset, runtime, manifest = _inputs()
    _prepare(registry, control, layout, runtime, manifest)
    journal = VerifiedJournalWatermark(
        security_watermark=0,
        head_entry_id=TypedId.from_bare("fa" * 32),
        verified_at=_NOW,
        fresh_for_seconds=60,
    )
    return registry, control, layout, asset, runtime, manifest, journal


def _issue(registry, control, layout, runtime, manifest, journal, **overrides):
    values = dict(
        control=control,
        layout=layout,
        manifest=manifest,
        service_name="predictor",
        deployment_target_id="prod-cluster",
        pod_uid="pod-uid-1",
        expected_runtime_contract=runtime,
        asset_attestation_verifier=HmacTestVerifier(),
        release_attestation_verifier=HmacTestVerifier(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        journal=journal,
        issuer_principal="controller@example.test",
    )
    values.update(overrides)
    return issue_runtime_authorization(registry, **values)


def _renew(
    registry, control, layout, runtime, manifest, journal, previous_lease, **overrides
):
    values = dict(
        control=control,
        layout=layout,
        manifest=manifest,
        service_name="predictor",
        deployment_target_id="prod-cluster",
        pod_uid="pod-uid-1",
        expected_runtime_contract=runtime,
        asset_attestation_verifier=HmacTestVerifier(),
        release_attestation_verifier=HmacTestVerifier(key_version=_RELEASE_KEY),
        release_key_versions=(_RELEASE_KEY,),
        non_release_key_versions=("test-only",),
        journal=journal,
        issuer_principal="controller@example.test",
        previous_lease=previous_lease,
    )
    values.update(overrides)
    return renew_runtime_authorization(registry, **values)


def test_issue_expiry_is_minimum_of_policy_journal_and_max_ttl():
    registry, control, layout, _asset, runtime, manifest, journal = _ready_inputs()
    lease = _issue(registry, control, layout, runtime, manifest, journal)

    assert lease.expires_at == journal.fresh_until.isoformat()
    assert lease.release_id == manifest.release_id
    assert lease.policy_attestation_ids == (
        manifest.core.assets[0].policy_decision_head_id,
    )

    short = _issue(
        registry,
        control,
        layout,
        runtime,
        manifest,
        journal,
        pod_uid="pod-uid-2",
        max_ttl_seconds=20,
    )
    assert short.expires_at == (_NOW + timedelta(seconds=20)).isoformat()


def test_renew_re_resolves_current_security_state_and_creates_new_lease():
    registry, control, layout, _asset, runtime, manifest, journal = _ready_inputs()
    first = _issue(registry, control, layout, runtime, manifest, journal)
    registry._server_read_time = _NOW + timedelta(seconds=10)
    renewed_journal = replace(journal, verified_at=registry._server_read_time)

    second = _renew(
        registry,
        control,
        layout,
        runtime,
        manifest,
        renewed_journal,
        first,
    )

    assert second.lease_id != first.lease_id
    assert second.issued_at == registry._server_read_time.isoformat()
    assert registry.get_runtime_authorization_lease(
        "predictor", first.lease_id
    ) == first


def test_revocation_immediately_blocks_runtime_renewal():
    registry, control, layout, asset, runtime, manifest, journal = _ready_inputs()
    first = _issue(registry, control, layout, runtime, manifest, journal)
    registry.put_asset_version(replace(asset, trust_state=TrustState.REVOKED))
    registry._server_read_time = _NOW + timedelta(seconds=10)

    with pytest.raises(RuntimeAuthorizationError, match="verification failed"):
        _renew(
            registry,
            control,
            layout,
            runtime,
            manifest,
            replace(journal, verified_at=registry._server_read_time),
            first,
        )


def test_renew_rejects_a_lease_not_present_in_the_registry():
    registry, control, layout, _asset, runtime, manifest, journal = _ready_inputs()
    issued = _issue(registry, control, layout, runtime, manifest, journal)
    registry._runtime_authorization_leases.clear()
    registry._server_read_time = _NOW + timedelta(seconds=10)

    with pytest.raises(RuntimeAuthorizationConflict, match="authoritative"):
        _renew(
            registry,
            control,
            layout,
            runtime,
            manifest,
            replace(journal, verified_at=registry._server_read_time),
            issued,
        )


def test_pod_or_security_watermark_change_rejects_renewal():
    registry, control, layout, _asset, runtime, manifest, journal = _ready_inputs()
    first = _issue(registry, control, layout, runtime, manifest, journal)
    registry._server_read_time = _NOW + timedelta(seconds=10)
    refreshed = replace(journal, verified_at=registry._server_read_time)

    with pytest.raises(RuntimeAuthorizationConflict, match="identity changed"):
        _renew(
            registry,
            control,
            layout,
            runtime,
            manifest,
            refreshed,
            first,
            pod_uid="pod-uid-2",
        )

    state = registry.get_service_release_state("predictor")
    registry.put_service_release_state(
        replace(
            state,
            revision=state.revision + 1,
            security_watermark=1,
            updated_at=registry._server_read_time.isoformat(),
        )
    )
    with pytest.raises(RuntimeAuthorizationConflict, match="identity changed"):
        _renew(
            registry,
            control,
            layout,
            runtime,
            manifest,
            replace(refreshed, security_watermark=1),
            first,
        )


def test_stale_journal_fails_before_authorization_write():
    registry, control, layout, _asset, runtime, manifest, journal = _ready_inputs()
    registry._server_read_time = _NOW + timedelta(seconds=61)

    with pytest.raises(RuntimeAuthorizationExpired, match="not fresh"):
        _issue(registry, control, layout, runtime, manifest, journal)


def test_readiness_fails_closed_before_expiry_without_renewal():
    registry, control, layout, _asset, runtime, manifest, journal = _ready_inputs()
    lease = _issue(registry, control, layout, runtime, manifest, journal)
    expiry = journal.fresh_until

    assert require_runtime_readiness(
        lease, at=expiry - timedelta(seconds=31)
    ) == lease
    with pytest.raises(RuntimeAuthorizationExpired, match="fail closed"):
        require_runtime_readiness(lease, at=expiry - timedelta(seconds=30))

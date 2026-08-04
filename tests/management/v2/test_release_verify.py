from __future__ import annotations

import pytest

from aigear.management.v2.attestation import HmacTestVerifier
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.release_kubernetes import (
    FakeKubernetesReleasePort,
    ProbeResult,
    compute_probe_evidence_digest,
)
from aigear.management.v2.release_verify import (
    CandidateVerificationFailed,
    CandidateVerificationUncertain,
    verify_release_candidate,
)
from tests.management.v2.test_service_runtime import (
    _RUNTIME_KEY,
    _runtime_inputs,
    _startup,
)


def _inputs():
    values = _runtime_inputs()
    startup = _startup(*values)
    manifest = values[4]
    deployed = values[5]
    port = FakeKubernetesReleasePort()
    port.seed_service(manifest.core.service_name)
    port.create_deployment(deployed.deployment.request)
    return values, startup, port


def _probe_result(manifest, *, passed=True, release_id=None):
    release_id = manifest.release_id if release_id is None else release_id
    asset_ids = tuple(
        sorted(
            (value.asset_version_id for value in manifest.core.assets),
            key=lambda value: value.typed,
        )
    )
    values = dict(
        passed=passed,
        release_id=release_id,
        image_digest=manifest.core.image.image_digest,
        asset_version_ids=asset_ids,
        runtime_contract_digest=manifest.core.runtime_contract.shape_contract_digest,
    )
    return ProbeResult(
        **values,
        evidence_digest=compute_probe_evidence_digest(**values),
        summary="ok" if passed else "contract failed",
    )


def _verify(values, startup, port):
    manifest = values[4]
    deployed = values[5]
    return verify_release_candidate(
        values[0],
        port,
        manifest=manifest,
        startup=startup,
        operation_id=deployed.operation.operation_id,
        owner_principal=deployed.operation.owner_principal,
        fencing_token=deployed.operation.fencing_token,
        runtime_attestation_verifier=HmacTestVerifier(key_version=_RUNTIME_KEY),
        runtime_key_versions=(_RUNTIME_KEY,),
        non_runtime_key_versions=("release-key-version-1", "test-only"),
        evidence_issuer_principal="controller@example.test",
        renewal_margin_seconds=0,
    )


def test_exact_candidate_smoke_commits_fenced_observed_evidence():
    values, startup, port = _inputs()
    manifest = values[4]
    stable_before = port.get_service(manifest.core.service_name)
    port.set_probe_result(startup.observed_evidence.pod_uid, _probe_result(manifest))

    result = _verify(values, startup, port)

    assert result.operation.phase.value == "verifying"
    assert result.service_state.observed_release_id == manifest.release_id
    assert result.smoke_evidence.fencing_token == result.operation.fencing_token
    assert values[0].get_runtime_evidence(
        manifest.core.service_name, result.smoke_evidence.evidence_id
    ) == result.smoke_evidence
    assert values[0].get_attestation(
        startup.observed_attestation.attestation_id
    ) == startup.observed_attestation
    assert port.get_service(manifest.core.service_name) == stable_before


def test_ready_candidate_with_failed_smoke_never_changes_stable_service():
    values, startup, port = _inputs()
    manifest = values[4]
    stable_before = port.get_service(manifest.core.service_name)
    port.set_probe_result(
        startup.observed_evidence.pod_uid,
        _probe_result(manifest, passed=False),
    )

    with pytest.raises(CandidateVerificationFailed, match="smoke contract"):
        _verify(values, startup, port)

    assert values[0].get_release_operation(
        values[5].operation.operation_id
    ).phase.value == "reconciling"
    assert port.get_service(manifest.core.service_name) == stable_before


def test_smoke_identity_drift_enters_reconciling_without_traffic_switch():
    values, startup, port = _inputs()
    manifest = values[4]
    stable_before = port.get_service(manifest.core.service_name)
    port.set_probe_result(
        startup.observed_evidence.pod_uid,
        _probe_result(manifest, release_id=TypedId.from_bare("ee" * 32)),
    )

    with pytest.raises(CandidateVerificationFailed, match="identity"):
        _verify(values, startup, port)

    assert values[0].get_service_release_state(
        manifest.core.service_name
    ).active_operation_phase.value == "reconciling"
    assert port.get_service(manifest.core.service_name) == stable_before


def test_indeterminate_probe_enters_reconciling_without_traffic_switch():
    values, startup, port = _inputs()
    manifest = values[4]
    stable_before = port.get_service(manifest.core.service_name)

    with pytest.raises(CandidateVerificationUncertain, match="reconciliation"):
        _verify(values, startup, port)

    assert values[0].get_release_operation(
        values[5].operation.operation_id
    ).phase.value == "reconciling"
    assert port.get_service(manifest.core.service_name) == stable_before

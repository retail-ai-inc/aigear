from __future__ import annotations

import pytest

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    AssetVersionRecord,
    InputBinding,
    InvalidAssetVersionRecordError,
    InvalidLifecycleTransitionError,
    InvalidTrustTransitionError,
    LifecycleState,
    ProducerSpec,
    TrustState,
    compute_asset_version_id,
    compute_component_key,
    validate_lifecycle_transition,
    validate_trust_transition,
)

_FINGERPRINT_HEX = "aa" * 32
_MODEL_BLOB_HEX = "bb" * 32
_SCALER_BLOB_HEX = "cc" * 32
_SCHEMA_DIGEST_HEX = "dd" * 32
_RUNTIME_DIGEST_HEX = "ee" * 32
_IMAGE_DIGEST_HEX = "11" * 32
_CODE_DIGEST_HEX = "22" * 32
_CONFIG_DIGEST_HEX = "33" * 32
_ATTESTATION_HEX = "44" * 32
_UPSTREAM_ASSET_VERSION_HEX = "55" * 32


def _producer_spec() -> ProducerSpec:
    return ProducerSpec(
        source_commit="abc123",
        image_digest=TypedId.from_bare(_IMAGE_DIGEST_HEX),
        code_digest=TypedId.from_bare(_CODE_DIGEST_HEX),
        config_digest=TypedId.from_bare(_CONFIG_DIGEST_HEX),
    )


def _components() -> tuple:
    return (
        AssetComponent(
            role="model",
            blob_id=TypedId.from_bare(_MODEL_BLOB_HEX),
            logical_name="model.onnx",
            media_type="application/onnx",
        ),
        AssetComponent(
            role="scaler",
            blob_id=TypedId.from_bare(_SCALER_BLOB_HEX),
            logical_name="scaler.pkl",
            media_type="application/octet-stream",
        ),
    )


def _input_bindings() -> tuple:
    return (
        InputBinding(
            binding_name="training_features",
            asset_version_id=TypedId.from_bare(_UPSTREAM_ASSET_VERSION_HEX),
        ),
    )


def _manifest_dict() -> dict:
    return {
        "environment_id": "production",
        "environment_fingerprint": f"sha256:{_FINGERPRINT_HEX}",
        "asset_type": "model",
        "name": "logistic_regression",
        "components": [component.to_manifest_dict() for component in _components()],
        "input_bindings": [binding.to_manifest_dict() for binding in _input_bindings()],
        "producer_spec": _producer_spec().to_manifest_dict(),
        "schema_contract_digest": f"sha256:{_SCHEMA_DIGEST_HEX}",
        "runtime_contract_digest": f"sha256:{_RUNTIME_DIGEST_HEX}",
        "policy_version": "policy-v1",
    }


def _asset_version_id() -> TypedId:
    return compute_asset_version_id(_manifest_dict())


def _asset_version_record(**overrides) -> AssetVersionRecord:
    asset_version_id = _asset_version_id()
    defaults = dict(
        schema_version="2.0",
        environment_id="production",
        environment_fingerprint=TypedId.from_bare(_FINGERPRINT_HEX),
        asset_version_id=asset_version_id,
        asset_type="model",
        name="logistic_regression",
        manifest_digest=asset_version_id,
        record_revision=1,
        components=_components(),
        input_bindings=_input_bindings(),
        producer_spec=_producer_spec(),
        schema_contract_digest=TypedId.from_bare(_SCHEMA_DIGEST_HEX),
        runtime_contract_digest=TypedId.from_bare(_RUNTIME_DIGEST_HEX),
        lifecycle_state=LifecycleState.ACTIVE,
        trust_state=TrustState.VERIFIED,
        policy_version="policy-v1",
        manifest_integrity_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
    )
    defaults.update(overrides)
    return AssetVersionRecord(**defaults)


# ── compute_component_key ─────────────────────────────────────────────────────────


def test_compute_component_key_matches_manual_jcs_hash():
    expected = digest_sha256_of_jcs(["aigear.component-key.v2", "model", "model.onnx"])
    assert compute_component_key("model", "model.onnx").bare == expected


def test_compute_component_key_differs_per_role_and_logical_name():
    assert compute_component_key("model", "model.onnx") != compute_component_key(
        "scaler", "model.onnx"
    )
    assert compute_component_key("model", "model.onnx") != compute_component_key(
        "model", "scaler.pkl"
    )


# ── AssetComponent / InputBinding / ProducerSpec ──────────────────────────────────


def test_asset_component_rejects_invalid_role_segment():
    with pytest.raises(ValueError):
        AssetComponent(
            role="../escape",
            blob_id=TypedId.from_bare(_MODEL_BLOB_HEX),
            logical_name="model.onnx",
            media_type="application/onnx",
        )


def test_input_binding_rejects_non_typed_id_asset_version_id():
    with pytest.raises(ValueError):
        InputBinding(binding_name="training_features", asset_version_id=_UPSTREAM_ASSET_VERSION_HEX)  # type: ignore[arg-type]


def test_producer_spec_rejects_empty_source_commit():
    with pytest.raises(ValueError):
        ProducerSpec(
            source_commit="",
            image_digest=TypedId.from_bare(_IMAGE_DIGEST_HEX),
            code_digest=TypedId.from_bare(_CODE_DIGEST_HEX),
            config_digest=TypedId.from_bare(_CONFIG_DIGEST_HEX),
        )


# ── compute_asset_version_id / canonical_manifest round trip ─────────────────────


def test_compute_asset_version_id_matches_manual_jcs_hash():
    manifest = _manifest_dict()
    expected = digest_sha256_of_jcs(manifest)
    assert compute_asset_version_id(manifest).bare == expected


def test_asset_version_record_round_trips_through_its_own_canonical_manifest():
    record = _asset_version_record()
    assert compute_asset_version_id(record.canonical_manifest()) == record.asset_version_id


def test_asset_version_record_canonical_manifest_excludes_mutable_fields():
    manifest = _asset_version_record().canonical_manifest()
    for forbidden in ("record_revision", "lifecycle_state", "trust_state", "created_at", "reference_epoch"):
        assert forbidden not in manifest


# ── AssetVersionRecord construction validation ────────────────────────────────────


def test_asset_version_record_accepts_well_formed_fields():
    record = _asset_version_record()
    assert record.lifecycle_state == LifecycleState.ACTIVE
    assert record.trust_state == TrustState.VERIFIED
    assert record.reference_epoch == 0
    assert record.policy_decision_head_ref is None


def test_asset_version_record_rejects_asset_version_id_manifest_digest_mismatch():
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(manifest_digest=TypedId.from_bare("66" * 32))


def test_asset_version_record_rejects_mismatched_asset_version_id_for_its_own_manifest():
    # Same manifest content but a digest that does not match it: this is
    # exactly the IdentityConflict-shaped bug the self-verification guards.
    wrong_id = TypedId.from_bare("77" * 32)
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(asset_version_id=wrong_id, manifest_digest=wrong_id)


def test_asset_version_record_rejects_empty_components():
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(components=())


def test_asset_version_record_rejects_unsorted_components():
    components = _components()[::-1]
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(components=components)


def test_asset_version_record_rejects_duplicate_component_role_logical_name():
    duplicate = (_components()[0], _components()[0])
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(components=duplicate)


def test_asset_version_record_rejects_unsorted_input_bindings():
    bindings = (
        InputBinding(binding_name="z_feature", asset_version_id=TypedId.from_bare(_UPSTREAM_ASSET_VERSION_HEX)),
        InputBinding(binding_name="a_feature", asset_version_id=TypedId.from_bare(_UPSTREAM_ASSET_VERSION_HEX)),
    )
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(input_bindings=bindings)


def test_asset_version_record_rejects_duplicate_input_binding_names():
    bindings = (
        InputBinding(binding_name="training_features", asset_version_id=TypedId.from_bare(_UPSTREAM_ASSET_VERSION_HEX)),
        InputBinding(binding_name="training_features", asset_version_id=TypedId.from_bare("88" * 32)),
    )
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(input_bindings=bindings)


def test_asset_version_record_rejects_non_positive_record_revision():
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(record_revision=0)


def test_asset_version_record_rejects_non_enum_lifecycle_state():
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(lifecycle_state="active")  # type: ignore[arg-type]


def test_asset_version_record_rejects_non_enum_trust_state():
    with pytest.raises(InvalidAssetVersionRecordError):
        _asset_version_record(trust_state="verified")  # type: ignore[arg-type]


# ── lifecycle_state transitions (spec 12.1) ───────────────────────────────────────


@pytest.mark.parametrize(
    "current,target",
    [
        (LifecycleState.ACTIVE, LifecycleState.ARCHIVED),
        (LifecycleState.ARCHIVED, LifecycleState.DELETE_PENDING),
        (LifecycleState.ARCHIVED, LifecycleState.ACTIVE),
        (LifecycleState.DELETE_PENDING, LifecycleState.DELETED),
        (LifecycleState.DELETE_PENDING, LifecycleState.ARCHIVED),
        (LifecycleState.DELETED, LifecycleState.RESTORING),
        (LifecycleState.RESTORING, LifecycleState.ARCHIVED),
    ],
)
def test_valid_lifecycle_transitions_are_accepted(current, target):
    validate_lifecycle_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (LifecycleState.ACTIVE, LifecycleState.DELETE_PENDING),
        (LifecycleState.ACTIVE, LifecycleState.DELETED),
        (LifecycleState.DELETE_PENDING, LifecycleState.ACTIVE),
        (LifecycleState.DELETED, LifecycleState.ACTIVE),
        (LifecycleState.DELETED, LifecycleState.ARCHIVED),
        (LifecycleState.RESTORING, LifecycleState.ACTIVE),
        (LifecycleState.RESTORING, LifecycleState.DELETED),
    ],
)
def test_invalid_lifecycle_transitions_are_rejected(current, target):
    with pytest.raises(InvalidLifecycleTransitionError):
        validate_lifecycle_transition(current, target)


# ── trust_state transitions (spec 12.2) ───────────────────────────────────────────


@pytest.mark.parametrize(
    "current,target",
    [
        (TrustState.QUARANTINED, TrustState.VERIFIED),
        (TrustState.VERIFIED, TrustState.APPROVED),
        (TrustState.APPROVED, TrustState.REVOKED),
    ],
)
def test_valid_trust_transitions_are_accepted(current, target):
    validate_trust_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (TrustState.QUARANTINED, TrustState.APPROVED),
        (TrustState.VERIFIED, TrustState.QUARANTINED),
        (TrustState.APPROVED, TrustState.VERIFIED),
        (TrustState.REVOKED, TrustState.APPROVED),
        (TrustState.REVOKED, TrustState.QUARANTINED),
    ],
)
def test_invalid_trust_transitions_are_rejected(current, target):
    with pytest.raises(InvalidTrustTransitionError):
        validate_trust_transition(current, target)


def test_revoked_trust_state_is_terminal():
    for target in TrustState:
        with pytest.raises(InvalidTrustTransitionError):
            validate_trust_transition(TrustState.REVOKED, target)

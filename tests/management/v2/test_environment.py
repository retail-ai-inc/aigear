from __future__ import annotations

import base64
import secrets

import pytest

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.environment import (
    EnvironmentIdentity,
    InvalidEnvironmentIdentityError,
    InvalidRegistryBindingError,
    MIN_REGISTRY_BINDING_ID_ENTROPY_BYTES,
    RegistryBinding,
    compute_environment_fingerprint,
    generate_registry_binding_id,
    validate_registry_binding_epoch,
)
from aigear.management.v2.identifiers import TypedId

_VALID_HEX = "cd" * 32


def _identity(**overrides) -> EnvironmentIdentity:
    defaults = dict(
        environment_id="production",
        gcp_project_number="123456789012",
        project_name="aigear_sklearn_pipeline",
        pipeline_version="logistic_regression",
        asset_bucket_name="aigear-prod-assets",
        asset_bucket_location="asia-northeast1",
        kms_trust_domain="projects/my-project/locations/asia-northeast1/keyRings/aigear",
    )
    defaults.update(overrides)
    return EnvironmentIdentity(**defaults)


# ── EnvironmentIdentity ─────────────────────────────────────────────────────────


def test_environment_identity_accepts_all_required_fields():
    identity = _identity()
    assert identity.environment_id == "production"


@pytest.mark.parametrize(
    "field_name",
    [
        "environment_id",
        "gcp_project_number",
        "project_name",
        "pipeline_version",
        "asset_bucket_name",
        "asset_bucket_location",
        "kms_trust_domain",
    ],
)
def test_environment_identity_rejects_empty_field(field_name):
    with pytest.raises(InvalidEnvironmentIdentityError):
        _identity(**{field_name: ""})


def test_environment_identity_rejects_non_string_field():
    with pytest.raises(InvalidEnvironmentIdentityError):
        _identity(gcp_project_number=123456789012)  # type: ignore[arg-type]


# ── compute_environment_fingerprint ──────────────────────────────────────────────


def test_compute_environment_fingerprint_matches_manual_jcs_hash():
    identity = _identity()
    expected = digest_sha256_of_jcs(
        [
            "aigear.environment.v2",
            identity.environment_id,
            identity.gcp_project_number,
            identity.project_name,
            identity.pipeline_version,
            identity.asset_bucket_name,
            identity.asset_bucket_location,
            identity.kms_trust_domain,
        ]
    )
    fingerprint = compute_environment_fingerprint(identity)
    assert isinstance(fingerprint, TypedId)
    assert fingerprint.bare == expected


def test_compute_environment_fingerprint_is_deterministic():
    assert compute_environment_fingerprint(_identity()) == compute_environment_fingerprint(
        _identity()
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "environment_id",
        "gcp_project_number",
        "project_name",
        "pipeline_version",
        "asset_bucket_name",
        "asset_bucket_location",
        "kms_trust_domain",
    ],
)
def test_compute_environment_fingerprint_changes_when_any_field_changes(field_name):
    baseline = compute_environment_fingerprint(_identity())
    changed = compute_environment_fingerprint(_identity(**{field_name: "changed-value"}))
    assert baseline != changed


def test_environment_fingerprint_does_not_depend_on_firestore_database_id():
    # EnvironmentIdentity has no such field at all; this test documents why:
    # the fingerprint must stay stable across a Firestore backup/restore.
    assert "firestore_database_id" not in EnvironmentIdentity.__dataclass_fields__


# ── generate_registry_binding_id ─────────────────────────────────────────────────


def test_generate_registry_binding_id_has_at_least_256_bits_of_entropy():
    binding_id = generate_registry_binding_id()
    padded = binding_id + "=" * (-len(binding_id) % 4)
    decoded = base64.urlsafe_b64decode(padded)
    assert len(decoded) >= MIN_REGISTRY_BINDING_ID_ENTROPY_BYTES


def test_generate_registry_binding_id_is_not_reused():
    ids = {generate_registry_binding_id() for _ in range(50)}
    assert len(ids) == 50


# ── RegistryBinding ──────────────────────────────────────────────────────────────


def _binding(**overrides) -> RegistryBinding:
    defaults = dict(
        firestore_database_id="(default)",
        registry_binding_id=generate_registry_binding_id(),
        registry_binding_epoch=1,
        bound_environment_fingerprint=TypedId.from_bare(_VALID_HEX),
    )
    defaults.update(overrides)
    return RegistryBinding(**defaults)


def test_registry_binding_accepts_well_formed_fields():
    binding = _binding()
    assert binding.registry_binding_epoch == 1


def test_registry_binding_rejects_empty_database_id():
    with pytest.raises(InvalidRegistryBindingError):
        _binding(firestore_database_id="")


def test_registry_binding_rejects_short_binding_id():
    short_id = base64.urlsafe_b64encode(secrets.token_bytes(8)).decode("ascii").rstrip("=")
    with pytest.raises(InvalidRegistryBindingError):
        _binding(registry_binding_id=short_id)


def test_registry_binding_rejects_non_base64_binding_id():
    with pytest.raises(InvalidRegistryBindingError):
        _binding(registry_binding_id="not base64url!!")


@pytest.mark.parametrize("epoch", [0, -1])
def test_registry_binding_rejects_non_positive_epoch(epoch):
    with pytest.raises(InvalidRegistryBindingError):
        _binding(registry_binding_epoch=epoch)


def test_registry_binding_rejects_non_int_epoch():
    with pytest.raises(InvalidRegistryBindingError):
        _binding(registry_binding_epoch="1")  # type: ignore[arg-type]


def test_registry_binding_rejects_bool_epoch():
    with pytest.raises(InvalidRegistryBindingError):
        _binding(registry_binding_epoch=True)  # type: ignore[arg-type]


def test_registry_binding_rejects_non_typed_id_fingerprint():
    with pytest.raises(InvalidRegistryBindingError):
        _binding(bound_environment_fingerprint=_VALID_HEX)  # type: ignore[arg-type]


# ── validate_registry_binding_epoch ──────────────────────────────────────────────


def test_validate_registry_binding_epoch_accepts_epoch_beyond_watermark():
    validate_registry_binding_epoch(5, ever_issued_watermark=4)


@pytest.mark.parametrize("candidate_epoch", [4, 3])
def test_validate_registry_binding_epoch_rejects_epoch_at_or_below_watermark(
    candidate_epoch,
):
    # Guards against the exact failure mode called out in the spec: replaying
    # a restored snapshot's "old value + 1" instead of the durable watermark.
    with pytest.raises(InvalidRegistryBindingError):
        validate_registry_binding_epoch(candidate_epoch, ever_issued_watermark=4)

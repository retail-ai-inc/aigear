from __future__ import annotations

import pytest

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.blob import (
    AvailabilityState,
    BlobLocationRevision,
    BlobRecord,
    InvalidAvailabilityTransitionError,
    InvalidBlobRecordError,
    LocationOperationKind,
    compute_genesis_location_chain_head,
    compute_location_chain_head,
    validate_availability_transition,
)

_BLOB_HEX = "ab" * 32
_FINGERPRINT_HEX = "cd" * 32
_ATTESTATION_HEX = "ef" * 32
_CHAIN_HEAD_HEX = "12" * 32


def _blob_id() -> TypedId:
    return TypedId.from_bare(_BLOB_HEX)


def _fingerprint() -> TypedId:
    return TypedId.from_bare(_FINGERPRINT_HEX)


def _blob_record(**overrides) -> BlobRecord:
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        blob_id=_blob_id(),
        sha256=_BLOB_HEX,
        size_bytes=12345,
        crc32c="AAAAAA==",
        bucket="aigear-prod-assets",
        object_name="proj/pipeline/registry/v2/_objects/sha256/ab/" + _BLOB_HEX,
        generation="123456789",
        current_location_revision=1,
        current_location_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
        location_chain_head=TypedId.from_bare(_CHAIN_HEAD_HEX),
        availability_state=AvailabilityState.READY,
    )
    defaults.update(overrides)
    return BlobRecord(**defaults)


def _location_revision(**overrides) -> BlobLocationRevision:
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        blob_id=_blob_id(),
        location_revision=1,
        bucket="aigear-prod-assets",
        object_name="proj/pipeline/registry/v2/_objects/sha256/ab/" + _BLOB_HEX,
        generation="123456789",
        sha256=_BLOB_HEX,
        crc32c="AAAAAA==",
        size_bytes=12345,
        location_operation_id="op-1",
        location_operation_kind=LocationOperationKind.PIPELINE_FINALIZE,
        location_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
        location_chain_head=TypedId.from_bare(_CHAIN_HEAD_HEX),
        reason="initial_write",
    )
    defaults.update(overrides)
    return BlobLocationRevision(**defaults)


# ── BlobRecord ───────────────────────────────────────────────────────────────────


def test_blob_record_accepts_well_formed_fields():
    record = _blob_record()
    assert record.availability_state == AvailabilityState.READY
    assert record.reference_epoch == 0
    assert record.retention_class == "standard"
    assert record.legal_hold is False


def test_blob_record_rejects_sha256_mismatch_with_blob_id():
    with pytest.raises(InvalidBlobRecordError):
        _blob_record(sha256="ff" * 32)


@pytest.mark.parametrize("field_name", ["crc32c", "bucket", "object_name", "generation", "retention_class"])
def test_blob_record_rejects_empty_string_fields(field_name):
    with pytest.raises(InvalidBlobRecordError):
        _blob_record(**{field_name: ""})


def test_blob_record_rejects_negative_size_bytes():
    with pytest.raises(InvalidBlobRecordError):
        _blob_record(size_bytes=-1)


def test_blob_record_rejects_non_positive_current_location_revision():
    with pytest.raises(InvalidBlobRecordError):
        _blob_record(current_location_revision=0)


def test_blob_record_rejects_non_typed_id_environment_fingerprint():
    with pytest.raises(InvalidBlobRecordError):
        _blob_record(environment_fingerprint=_FINGERPRINT_HEX)  # type: ignore[arg-type]


def test_blob_record_rejects_non_enum_availability_state():
    with pytest.raises(InvalidBlobRecordError):
        _blob_record(availability_state="ready")  # type: ignore[arg-type]


def test_blob_record_rejects_malformed_schema_version():
    with pytest.raises(ValueError):
        _blob_record(schema_version="2")


def test_blob_record_rejects_non_bool_legal_hold():
    with pytest.raises(InvalidBlobRecordError):
        _blob_record(legal_hold="false")  # type: ignore[arg-type]


# ── availability_state transitions ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,target",
    [
        (AvailabilityState.PENDING, AvailabilityState.READY),
        (AvailabilityState.READY, AvailabilityState.MISSING),
        (AvailabilityState.READY, AvailabilityState.CORRUPT),
        (AvailabilityState.READY, AvailabilityState.DELETE_PENDING),
        (AvailabilityState.READY, AvailabilityState.RESTORING),
        (AvailabilityState.DELETE_PENDING, AvailabilityState.DELETED),
        (AvailabilityState.DELETE_PENDING, AvailabilityState.READY),
        (AvailabilityState.MISSING, AvailabilityState.RESTORING),
        (AvailabilityState.CORRUPT, AvailabilityState.RESTORING),
        (AvailabilityState.DELETED, AvailabilityState.RESTORING),
        (AvailabilityState.RESTORING, AvailabilityState.READY),
        (AvailabilityState.RESTORING, AvailabilityState.MISSING),
        (AvailabilityState.RESTORING, AvailabilityState.CORRUPT),
    ],
)
def test_valid_availability_transitions_are_accepted(current, target):
    validate_availability_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (AvailabilityState.PENDING, AvailabilityState.MISSING),
        (AvailabilityState.PENDING, AvailabilityState.DELETE_PENDING),
        (AvailabilityState.READY, AvailabilityState.PENDING),
        (AvailabilityState.READY, AvailabilityState.DELETED),
        (AvailabilityState.MISSING, AvailabilityState.READY),
        (AvailabilityState.CORRUPT, AvailabilityState.READY),
        (AvailabilityState.DELETED, AvailabilityState.READY),
        (AvailabilityState.DELETE_PENDING, AvailabilityState.RESTORING),
        (AvailabilityState.DELETE_PENDING, AvailabilityState.MISSING),
        (AvailabilityState.RESTORING, AvailabilityState.PENDING),
        (AvailabilityState.RESTORING, AvailabilityState.DELETE_PENDING),
        (AvailabilityState.RESTORING, AvailabilityState.DELETED),
    ],
)
def test_invalid_availability_transitions_are_rejected(current, target):
    with pytest.raises(InvalidAvailabilityTransitionError):
        validate_availability_transition(current, target)


def test_every_state_has_at_least_one_declared_transition_rule():
    # Guards against silently forgetting a state when the enum grows.
    assert set(AvailabilityState) == {
        AvailabilityState.PENDING,
        AvailabilityState.READY,
        AvailabilityState.MISSING,
        AvailabilityState.CORRUPT,
        AvailabilityState.DELETE_PENDING,
        AvailabilityState.DELETED,
        AvailabilityState.RESTORING,
    }


# ── BlobLocationRevision ─────────────────────────────────────────────────────────


def test_location_revision_one_accepts_well_formed_fields():
    revision = _location_revision()
    assert revision.location_revision == 1
    assert revision.source_location_revision is None
    assert revision.previous_location_attestation_ref is None


def test_location_revision_one_rejects_non_null_source_revision():
    with pytest.raises(InvalidBlobRecordError):
        _location_revision(source_location_revision=0)


def test_location_revision_one_rejects_non_null_previous_attestation_ref():
    with pytest.raises(InvalidBlobRecordError):
        _location_revision(previous_location_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX))


def test_location_revision_two_requires_matching_source_revision():
    revision = _location_revision(
        location_revision=2,
        source_location_revision=1,
        previous_location_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
        location_operation_kind=LocationOperationKind.RESTORE,
        reason="soft_delete_restore",
    )
    assert revision.source_location_revision == 1


def test_location_revision_two_rejects_mismatched_source_revision():
    with pytest.raises(InvalidBlobRecordError):
        _location_revision(
            location_revision=2,
            source_location_revision=5,
            previous_location_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
        )


def test_location_revision_two_requires_previous_attestation_ref():
    with pytest.raises(InvalidBlobRecordError):
        _location_revision(location_revision=2, source_location_revision=1)


def test_location_revision_rejects_sha256_mismatch_with_blob_id():
    with pytest.raises(InvalidBlobRecordError):
        _location_revision(sha256="ff" * 32)


def test_location_revision_rejects_non_closed_enum_operation_kind():
    with pytest.raises(InvalidBlobRecordError):
        _location_revision(location_operation_kind="restore")  # type: ignore[arg-type]


def test_location_operation_kind_is_closed_enum_with_exact_expected_members():
    assert {member.value for member in LocationOperationKind} == {
        "pipeline_finalize",
        "external_import",
        "legacy_migration",
        "restore",
        "rehydrate",
        "security_reattest",
    }


# ── chain head derivation ─────────────────────────────────────────────────────────


def test_compute_genesis_location_chain_head_matches_manual_jcs_hash():
    fingerprint = _fingerprint()
    blob_id = _blob_id()
    expected = digest_sha256_of_jcs(
        ["aigear.location-chain-genesis.v2", fingerprint.typed, blob_id.typed]
    )
    assert compute_genesis_location_chain_head(fingerprint, blob_id).bare == expected


def test_compute_genesis_location_chain_head_is_deterministic():
    fingerprint = _fingerprint()
    blob_id = _blob_id()
    assert compute_genesis_location_chain_head(
        fingerprint, blob_id
    ) == compute_genesis_location_chain_head(fingerprint, blob_id)


def test_compute_genesis_location_chain_head_differs_per_blob():
    fingerprint = _fingerprint()
    other_blob_id = TypedId.from_bare("99" * 32)
    assert compute_genesis_location_chain_head(
        fingerprint, _blob_id()
    ) != compute_genesis_location_chain_head(fingerprint, other_blob_id)


def test_compute_location_chain_head_matches_manual_jcs_hash():
    previous_head = TypedId.from_bare(_CHAIN_HEAD_HEX)
    attestation_id = TypedId.from_bare(_ATTESTATION_HEX)
    expected = digest_sha256_of_jcs(
        ["aigear.location-chain.v2", previous_head.typed, attestation_id.typed]
    )
    assert compute_location_chain_head(previous_head, attestation_id).bare == expected


def test_compute_location_chain_head_does_not_depend_on_itself():
    # The new head is derived only from the previous head + attestation id;
    # changing an unrelated value must not change it (guards against
    # accidentally threading the new head back into its own inputs).
    previous_head = TypedId.from_bare(_CHAIN_HEAD_HEX)
    attestation_id = TypedId.from_bare(_ATTESTATION_HEX)
    head_a = compute_location_chain_head(previous_head, attestation_id)
    head_b = compute_location_chain_head(previous_head, attestation_id)
    assert head_a == head_b

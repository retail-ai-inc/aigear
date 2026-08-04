from __future__ import annotations

import pytest

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.label import ReadableManifestProjection
from aigear.management.v2.records.occurrence import (
    AttachmentRef,
    InvalidOccurrenceRecordError,
    InvalidOccurrenceStatusTransitionError,
    OccurrenceRecord,
    OccurrenceStatus,
    ResolvedInputBinding,
    compute_committed_output_key,
    compute_metrics_digest,
    compute_occurrence_id,
    compute_resolved_inputs_digest,
    validate_occurrence_status_transition,
)

_FINGERPRINT_HEX = "aa" * 32
_ASSET_VERSION_HEX = "bb" * 32
_UPSTREAM_ASSET_VERSION_HEX = "cc" * 32
_LABEL_HEX = "dd" * 32
_ATTESTATION_HEX = "ee" * 32
_BLOB_HEX = "11" * 32


def _fingerprint() -> TypedId:
    return TypedId.from_bare(_FINGERPRINT_HEX)


# ── compute_occurrence_id / compute_committed_output_key ──────────────────────────


def test_compute_occurrence_id_matches_manual_jcs_hash():
    expected = digest_sha256_of_jcs(["aigear.occurrence.v2", "run-1", "training", 2, "model"])
    assert compute_occurrence_id("run-1", "training", 2, "model").bare == expected


def test_compute_occurrence_id_differs_per_attempt_no():
    assert compute_occurrence_id("run-1", "training", 1, "model") != compute_occurrence_id(
        "run-1", "training", 2, "model"
    )


def test_compute_committed_output_key_matches_manual_jcs_hash():
    expected = digest_sha256_of_jcs(["aigear.committed-output.v2", "run-1", "training", "model"])
    assert compute_committed_output_key("run-1", "training", "model").bare == expected


def test_compute_committed_output_key_is_stable_across_attempts():
    # committed_output_key intentionally excludes attempt_no: it identifies
    # the (run, step, output) slot across every attempt.
    key_for_attempt_metadata_only = compute_committed_output_key("run-1", "training", "model")
    assert key_for_attempt_metadata_only == compute_committed_output_key(
        "run-1", "training", "model"
    )


# ── ResolvedInputBinding / compute_resolved_inputs_digest ──────────────────────────


def _binding(name: str, **overrides) -> ResolvedInputBinding:
    defaults = dict(
        binding_name=name,
        asset_version_id=TypedId.from_bare(_UPSTREAM_ASSET_VERSION_HEX),
    )
    defaults.update(overrides)
    return ResolvedInputBinding(**defaults)


def test_resolved_input_binding_serializes_all_fields_even_when_null():
    binding = _binding("training_features")
    manifest_dict = binding.to_manifest_dict()
    assert set(manifest_dict) == {
        "binding_name",
        "asset_version_id",
        "occurrence_id",
        "source_label_id",
        "policy_decision_epoch",
    }
    assert manifest_dict["occurrence_id"] is None
    assert manifest_dict["source_label_id"] is None
    assert manifest_dict["policy_decision_epoch"] is None


def test_compute_resolved_inputs_digest_matches_manual_jcs_hash():
    bindings = (_binding("training_features"),)
    expected = digest_sha256_of_jcs(
        ["aigear.resolved-inputs.v2", [bindings[0].to_manifest_dict()]]
    )
    assert compute_resolved_inputs_digest(bindings).bare == expected


def test_compute_resolved_inputs_digest_rejects_unsorted_bindings():
    bindings = (_binding("z_feature"), _binding("a_feature"))
    with pytest.raises(InvalidOccurrenceRecordError):
        compute_resolved_inputs_digest(bindings)


def test_compute_resolved_inputs_digest_rejects_duplicate_binding_names():
    bindings = (_binding("training_features"), _binding("training_features"))
    with pytest.raises(InvalidOccurrenceRecordError):
        compute_resolved_inputs_digest(bindings)


def test_compute_resolved_inputs_digest_accepts_empty_bindings():
    digest = compute_resolved_inputs_digest(())
    assert digest == TypedId.from_bare(
        digest_sha256_of_jcs(["aigear.resolved-inputs.v2", []])
    )


# ── compute_metrics_digest ──────────────────────────────────────────────────────


def test_compute_metrics_digest_matches_manual_jcs_hash():
    metrics = {"accuracy": 1}
    expected = digest_sha256_of_jcs(["aigear.occurrence-metrics.v2", metrics])
    assert compute_metrics_digest(metrics).bare == expected


def test_compute_metrics_digest_rejects_non_dict():
    with pytest.raises(InvalidOccurrenceRecordError):
        compute_metrics_digest(["not", "a", "dict"])  # type: ignore[arg-type]


# ── OccurrenceRecord construction ─────────────────────────────────────────────────


def _committed_occurrence_record(**overrides) -> OccurrenceRecord:
    run_id, step_name, attempt_no, output_name = "run-1", "training", 2, "model"
    occurrence_id = compute_occurrence_id(run_id, step_name, attempt_no, output_name)
    committed_output_key = compute_committed_output_key(run_id, step_name, output_name)
    bindings = (_binding("training_features"),)
    metrics = {"accuracy": 1}
    defaults = dict(
        schema_version="2.0",
        environment_fingerprint=_fingerprint(),
        occurrence_id=occurrence_id,
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt_no,
        fencing_token=7,
        output_name=output_name,
        committed_output_key=committed_output_key,
        resolved_input_bindings=bindings,
        resolved_inputs_digest=compute_resolved_inputs_digest(bindings),
        metrics=metrics,
        metrics_digest=compute_metrics_digest(metrics),
        status=OccurrenceStatus.COMMITTED,
        operation_id="op-1",
        asset_version_id=TypedId.from_bare(_ASSET_VERSION_HEX),
        asset_type="model",
        asset_name="logistic_regression",
        label_id=TypedId.from_bare(_LABEL_HEX),
        display_version="v2026.07.23",
        finalization_attestation_ref=TypedId.from_bare(_ATTESTATION_HEX),
    )
    defaults.update(overrides)
    return OccurrenceRecord(**defaults)


def _provisional_occurrence_record(**overrides) -> OccurrenceRecord:
    base_overrides = dict(
        status=OccurrenceStatus.PROVISIONAL,
        asset_version_id=None,
        asset_type=None,
        asset_name=None,
        label_id=None,
        display_version=None,
        finalization_attestation_ref=None,
    )
    base_overrides.update(overrides)
    return _committed_occurrence_record(**base_overrides)


def test_committed_occurrence_record_accepts_well_formed_fields():
    record = _committed_occurrence_record()
    assert record.status == OccurrenceStatus.COMMITTED
    assert record.asset_version_id is not None


def test_provisional_occurrence_record_accepts_null_asset_fields():
    record = _provisional_occurrence_record()
    assert record.asset_version_id is None
    assert record.asset_type is None
    assert record.finalization_attestation_ref is None


def test_provisional_occurrence_record_rejects_non_null_asset_version_id():
    with pytest.raises(InvalidOccurrenceRecordError):
        _provisional_occurrence_record(asset_version_id=TypedId.from_bare(_ASSET_VERSION_HEX))


def test_committed_occurrence_record_requires_asset_version_id():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(
            asset_version_id=None, asset_type=None, asset_name=None,
            label_id=None, display_version=None, finalization_attestation_ref=None,
        )


def test_occurrence_record_rejects_occurrence_id_mismatch():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(occurrence_id=TypedId.from_bare("99" * 32))


def test_occurrence_record_rejects_committed_output_key_mismatch():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(committed_output_key=TypedId.from_bare("99" * 32))


def test_occurrence_record_rejects_resolved_inputs_digest_mismatch():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(resolved_inputs_digest=TypedId.from_bare("99" * 32))


def test_occurrence_record_rejects_metrics_digest_mismatch():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(metrics_digest=TypedId.from_bare("99" * 32))


def test_occurrence_record_rejects_label_without_display_version():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(display_version=None)


def test_occurrence_record_rejects_display_version_without_label():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(label_id=None)


def test_occurrence_record_rejects_non_positive_attempt_no():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(attempt_no=0)


def test_occurrence_record_rejects_negative_fencing_token():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(fencing_token=-1)


def test_occurrence_record_accepts_attachment_refs_sorted_and_unique():
    refs = (
        AttachmentRef(
            attachment_kind="metrics",
            logical_name="evaluation.json",
            blob_id=TypedId.from_bare(_BLOB_HEX),
            media_type="application/json",
        ),
    )
    record = _committed_occurrence_record(attachment_refs=refs)
    assert record.attachment_refs == refs


def test_occurrence_record_rejects_unsorted_attachment_refs():
    refs = (
        AttachmentRef(
            attachment_kind="metrics",
            logical_name="z.json",
            blob_id=TypedId.from_bare(_BLOB_HEX),
            media_type="application/json",
        ),
        AttachmentRef(
            attachment_kind="metrics",
            logical_name="a.json",
            blob_id=TypedId.from_bare(_BLOB_HEX),
            media_type="application/json",
        ),
    )
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(attachment_refs=refs)


def test_occurrence_record_rejects_duplicate_attachment_refs():
    ref = AttachmentRef(
        attachment_kind="metrics",
        logical_name="evaluation.json",
        blob_id=TypedId.from_bare(_BLOB_HEX),
        media_type="application/json",
    )
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(attachment_refs=(ref, ref))


def test_occurrence_record_accepts_readable_occurrence_projection():
    projection = ReadableManifestProjection.initial(
        uri="gs://bucket/proj/pipeline/registry/v2/runs/run-1/steps/training/outputs/model/occurrence.json"
    )
    record = _committed_occurrence_record(readable_occurrence=projection)
    assert record.readable_occurrence is projection


def test_occurrence_record_rejects_non_projection_readable_occurrence():
    with pytest.raises(InvalidOccurrenceRecordError):
        _committed_occurrence_record(readable_occurrence=object())  # type: ignore[arg-type]


# ── status transitions (spec 12.1) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current,target",
    [
        (OccurrenceStatus.PROVISIONAL, OccurrenceStatus.COMMITTED),
        (OccurrenceStatus.PROVISIONAL, OccurrenceStatus.ABORTED),
        (OccurrenceStatus.COMMITTED, OccurrenceStatus.ARCHIVED),
        (OccurrenceStatus.ARCHIVED, OccurrenceStatus.DELETE_PENDING),
        (OccurrenceStatus.DELETE_PENDING, OccurrenceStatus.DELETED_TOMBSTONE),
        (OccurrenceStatus.DELETE_PENDING, OccurrenceStatus.ARCHIVED),
    ],
)
def test_valid_occurrence_status_transitions_are_accepted(current, target):
    validate_occurrence_status_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (OccurrenceStatus.PROVISIONAL, OccurrenceStatus.ARCHIVED),
        (OccurrenceStatus.COMMITTED, OccurrenceStatus.PROVISIONAL),
        (OccurrenceStatus.COMMITTED, OccurrenceStatus.DELETE_PENDING),
        (OccurrenceStatus.ARCHIVED, OccurrenceStatus.COMMITTED),
        (OccurrenceStatus.DELETED_TOMBSTONE, OccurrenceStatus.ARCHIVED),
        (OccurrenceStatus.ABORTED, OccurrenceStatus.PROVISIONAL),
    ],
)
def test_invalid_occurrence_status_transitions_are_rejected(current, target):
    with pytest.raises(InvalidOccurrenceStatusTransitionError):
        validate_occurrence_status_transition(current, target)

from __future__ import annotations

from dataclasses import replace

import pytest

from aigear.management.v2.content_policy import (
    ContentGovernance,
    ContentPolicyError,
    FormatRule,
    ImportContentPolicy,
    ProducerEvidence,
    ScanVerdict,
    create_scanner_result,
    inspect_import_content,
)
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_executor import (
    ImportExecutorCompletion,
    QuarantineObjectDescriptor,
    compute_import_payload_set_digest,
)

_FP = TypedId.from_bare("aa" * 32)
_TICKET = TypedId.from_bare("bb" * 32)
_PRODUCER_EVIDENCE = TypedId.from_bare("cc" * 32)
_AT = "2026-07-28T00:01:00+00:00"


def _descriptor(gcs, *, data=b"model", name="model.onnx", media_type="model/onnx"):
    snapshot = gcs.put_object(
        f"project/pipeline/registry/v2/_quarantine/import-1/components/primary/{name}",
        data,
    )
    return QuarantineObjectDescriptor(
        payload_kind="components",
        payload_key="primary",
        file_name=name,
        media_type=media_type,
        source_object_name=f"external/{name}",
        source_generation="7",
        object_name=snapshot.object_name,
        generation=snapshot.generation,
        sha256=snapshot.sha256,
        crc32c=snapshot.crc32c,
        size_bytes=snapshot.size_bytes,
    )


def _completion(gcs, **descriptor_overrides):
    descriptor = _descriptor(gcs, **descriptor_overrides)
    manifest = QuarantineObjectDescriptor(
        payload_kind="source_manifest",
        payload_key="source_manifest",
        file_name="source-manifest.json",
        media_type="application/json",
        source_object_name="external/source-manifest.json",
        source_generation="1",
        object_name=(
            "project/pipeline/registry/v2/_quarantine/import-1/source-manifest.json"
        ),
        generation="1",
        sha256="11" * 32,
        crc32c="crc",
        size_bytes=10,
    )
    return ImportExecutorCompletion(
        operation_id="import-1",
        ticket_digest=_TICKET,
        environment_fingerprint=_FP,
        executor_principal="executor@example.com",
        fencing_token=1,
        source_manifest=manifest,
        payloads=(descriptor,),
        payload_set_digest=compute_import_payload_set_digest(
            manifest, (descriptor,)
        ),
        completed_at=_AT,
    )


def _policy(**overrides):
    values = {
        "policy_version": "policy-7",
        "format_rules": (
            FormatRule(
                format_name="onnx",
                media_types=("model/onnx",),
                file_suffixes=(".onnx",),
                max_size_bytes=1024,
            ),
        ),
        "allowed_scanners": (("scanner", "1"),),
        "allowed_producers": ("trusted-build",),
        "required_logical_paths": (("components", "primary"),),
    }
    values.update(overrides)
    return ImportContentPolicy(**values)


def _governance():
    return ContentGovernance(
        owner="ml-team",
        data_classification="internal",
        purpose="fraud-detection",
        license_or_consent_ref="policy/license/1",
        residency="asia-east1",
        retention_class="standard",
        legal_hold=False,
        policy_version="policy-7",
    )


class Scanner:
    scanner_id = "scanner"
    scanner_version = "1"

    def __init__(self, verdict=ScanVerdict.CLEAN):
        self.verdict = verdict

    def scan(self, data, *, media_type, expected_sha256):
        return create_scanner_result(
            scanner_id="scanner",
            scanner_version="1",
            payload_sha256=expected_sha256,
            verdict=self.verdict,
            finding_codes=() if self.verdict is ScanVerdict.CLEAN else ("malware",),
            completed_at=_AT,
        )


def _inspect(completion, gcs, **overrides):
    values = {
        "quarantine_gcs": gcs,
        "policy": _policy(),
        "governance": _governance(),
        "producer": ProducerEvidence(
            producer_id="trusted-build",
            evidence_digest=_PRODUCER_EVIDENCE,
            verified=True,
        ),
        "scanner": Scanner(),
        "inspected_at": _AT,
    }
    values.update(overrides)
    return inspect_import_content(completion, **values)


def test_clean_exact_payload_produces_digest_only_evidence():
    gcs = FakeGcsClient()
    completion = _completion(gcs)

    evidence = _inspect(completion, gcs)

    assert evidence.payloads[0].format_name == "onnx"
    assert evidence.payloads[0].scanner_report_digest
    assert not hasattr(evidence.payloads[0], "raw_sample")
    assert evidence.payload_set_digest == completion.payload_set_digest


@pytest.mark.parametrize(
    "name,media,data",
    [
        ("model.pkl", "application/python-pickle", b"safe-looking"),
        ("model.joblib", "model/onnx", b"safe-looking"),
        ("model.onnx", "model/onnx", b"\x80\x04pickle"),
    ],
)
def test_external_pickle_variants_are_rejected_by_default(name, media, data):
    gcs = FakeGcsClient()
    completion = _completion(gcs, name=name, media_type=media, data=data)

    with pytest.raises(ContentPolicyError, match="pickle"):
        _inspect(completion, gcs)


def test_unknown_format_and_missing_required_schema_fail_closed():
    gcs = FakeGcsClient()
    completion = _completion(gcs, name="model.bin", media_type="application/octet-stream")
    with pytest.raises(ContentPolicyError, match="unknown"):
        _inspect(completion, gcs)

    with pytest.raises(ContentPolicyError, match="required"):
        _inspect(
            _completion(FakeGcsClient()),
            FakeGcsClient(),
            policy=_policy(
                required_logical_paths=(
                    ("components", "primary"),
                    ("attachments", "schema"),
                )
            ),
        )


def test_scanner_timeout_block_and_unknown_scanner_fail_closed():
    gcs = FakeGcsClient()
    completion = _completion(gcs)

    class TimeoutScanner:
        scanner_id = "scanner"
        scanner_version = "1"

        def scan(self, *args, **kwargs):
            raise TimeoutError

    with pytest.raises(ContentPolicyError, match="timed out"):
        _inspect(completion, gcs, scanner=TimeoutScanner())
    with pytest.raises(ContentPolicyError, match="clean"):
        _inspect(completion, gcs, scanner=Scanner(ScanVerdict.BLOCKED))
    with pytest.raises(ContentPolicyError, match="allowlisted"):
        _inspect(
            completion,
            gcs,
            policy=_policy(allowed_scanners=(("other", "1"),)),
        )


def test_unverified_or_unapproved_producer_and_governance_mismatch_are_rejected():
    gcs = FakeGcsClient()
    completion = _completion(gcs)
    with pytest.raises(ContentPolicyError, match="producer"):
        _inspect(
            completion,
            gcs,
            producer=ProducerEvidence(
                producer_id="trusted-build",
                evidence_digest=_PRODUCER_EVIDENCE,
                verified=False,
            ),
        )
    with pytest.raises(ContentPolicyError, match="policy_version"):
        _inspect(
            completion,
            gcs,
            governance=replace(_governance(), policy_version="other"),
        )


def test_quarantine_generation_or_digest_drift_is_rejected():
    gcs = FakeGcsClient()
    completion = _completion(gcs)
    original = completion.payloads[0]
    changed = gcs.put_object(original.object_name, b"changed")
    assert changed.generation != original.generation

    evidence = _inspect(completion, gcs)
    assert evidence.payloads[0].quarantine_generation == original.generation

    corrupted_descriptor = replace(original, sha256="00" * 32)
    corrupted = replace(
        completion,
        payloads=(corrupted_descriptor,),
        payload_set_digest=compute_import_payload_set_digest(
            completion.source_manifest, (corrupted_descriptor,)
        ),
    )
    with pytest.raises(ContentPolicyError, match="integrity"):
        _inspect(corrupted, gcs)

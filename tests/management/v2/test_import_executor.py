from __future__ import annotations

import json

import pytest

from aigear.management.v2.attestation import HmacTestSigner, HmacTestVerifier
from aigear.management.v2.fake_gcs import (
    FakeGcsClient,
    GenerationPreconditionError,
)
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_executor import (
    ImportExecutionError,
    PartialImportCopyError,
    execute_import_to_quarantine,
)
from aigear.management.v2.import_ticket import issue_import_ticket
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportControlSnapshot,
    ImportOperationRecord,
    ImportPhase,
)

_FP = TypedId.from_bare("aa" * 32)
_KEY = "projects/p/locations/l/keyRings/r/cryptoKeys/import/cryptoKeyVersions/1"
_PREFIX = "project/pipeline/registry/v2/_quarantine/import-1/"


def _manifest(payloads):
    return json.dumps(
        {
            "schema_version": "2.0",
            "declaration": {"asset_type": "model"},
            "payloads": payloads,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _payload(kind, key, name, source, *, size=None):
    return {
        "payload_kind": kind,
        "payload_key": key,
        "file_name": name,
        "media_type": "application/octet-stream",
        "object_name": source.object_name,
        "generation": source.generation,
        "size_bytes": source.size_bytes if size is None else size,
    }


def _ticket(source):
    operation = ImportOperationRecord(
        schema_version="2.0",
        operation_id="import-1",
        idempotency_key_hash="ab" * 32,
        request_fingerprint=TypedId.from_bare("bb" * 32),
        source=ExactImportSource(
            environment_id="production",
            project_id="source-project",
            bucket="approved-bucket",
            object_name=source.object_name,
            generation=source.generation,
            region="asia-east1",
            size_bytes=source.size_bytes,
        ),
        target_environment_id="production",
        target_quarantine_prefix=_PREFIX,
        control_snapshot=ImportControlSnapshot(
            environment_id="production",
            environment_fingerprint=_FP,
            firestore_database_id="(default)",
            registry_binding_id="binding-1",
            registry_binding_epoch=1,
            write_epoch=1,
        ),
        owner_principal="controller@example.com",
        write_budget=100,
        fencing_token=2,
        phase=ImportPhase.RESERVED,
        revision=1,
        lease_expires_at="2026-07-28T00:05:00+00:00",
    )
    return issue_import_ticket(
        operation,
        audience="aigear-import",
        executor_principal="import-executor@example.com",
        issued_at="2026-07-28T00:00:00+00:00",
        expires_at="2026-07-28T00:04:00+00:00",
        signer=HmacTestSigner(b"secret", key_version=_KEY),
    )


def _execute(source_gcs, quarantine_gcs, manifest_snapshot, **overrides):
    values = {
        "source_gcs": source_gcs,
        "quarantine_gcs": quarantine_gcs,
        "layout": GcsLayoutV2("target-bucket", "project", "pipeline"),
        "verifier": HmacTestVerifier(b"secret", key_version=_KEY),
        "at": "2026-07-28T00:01:00+00:00",
        "expected_audience": "aigear-import",
        "expected_executor_principal": "import-executor@example.com",
        "expected_environment_fingerprint": _FP,
    }
    values.update(overrides)
    return execute_import_to_quarantine(_ticket(manifest_snapshot), **values)


def _bundle():
    source = FakeGcsClient()
    component = source.put_object("bundle/model.onnx", b"model")
    attachment = source.put_object("bundle/schema.json", b'{"type":"object"}')
    manifest = source.put_object(
        "bundle/source-manifest.json",
        _manifest(
            [
                _payload("components", "primary", "model.onnx", component),
                _payload("attachments", "schema", "schema.json", attachment),
            ]
        ),
    )
    return source, component, attachment, manifest


def test_exact_copy_publishes_complete_payload_evidence():
    source, component, attachment, manifest = _bundle()
    target = FakeGcsClient()

    completion = _execute(source, target, manifest)

    assert [item.payload_key for item in completion.payloads] == ["primary", "schema"]
    assert completion.payloads[0].source_generation == component.generation
    assert completion.payloads[1].source_generation == attachment.generation
    assert completion.to_record().payload_set_digest == completion.payload_set_digest
    assert target.get_live_object(
        "project/pipeline/registry/v2/_quarantine/import-1/components/primary/model.onnx"
    ).data == b"model"


def test_later_source_generation_does_not_replace_pinned_bytes():
    source, _, _, manifest = _bundle()
    source.put_object("bundle/model.onnx", b"changed")

    completion = _execute(source, FakeGcsClient(), manifest)

    assert completion.payloads[0].sha256 == (
        "9372c470eeadd5ecd9c3c74c2b3cb633f8e2f2fad799250a0f70d652b6b825e4"
    )


def test_missing_exact_source_generation_fails_before_any_write():
    source, component, attachment, _ = _bundle()
    manifest = source.put_object(
        "bundle/drift.json",
        _manifest(
            [
                {
                    **_payload("components", "primary", "model.onnx", component),
                    "generation": "999",
                },
                _payload("attachments", "schema", "schema.json", attachment),
            ]
        ),
    )
    target = FakeGcsClient()

    with pytest.raises(Exception, match="no object"):
        _execute(source, target, manifest)

    assert target.get_live_object(
        "project/pipeline/registry/v2/_quarantine/import-1/source-manifest.json"
    ) is None


def test_size_drift_and_oversize_fail_before_any_write():
    source, component, _, _ = _bundle()
    bad_manifest = source.put_object(
        "bundle/bad-size.json",
        _manifest(
            [_payload("components", "primary", "model.onnx", component, size=999)]
        ),
    )
    target = FakeGcsClient()
    with pytest.raises(ImportExecutionError, match="metadata drifted"):
        _execute(source, target, bad_manifest)
    assert target.get_live_object(
        "project/pipeline/registry/v2/_quarantine/import-1/source-manifest.json"
    ) is None

    source2, _, _, manifest2 = _bundle()
    with pytest.raises(ImportExecutionError, match="total byte limit"):
        _execute(source2, FakeGcsClient(), manifest2, max_total_size_bytes=1)


def test_duplicate_logical_path_is_rejected():
    source = FakeGcsClient()
    first = source.put_object("a", b"a")
    second = source.put_object("b", b"b")
    manifest = source.put_object(
        "manifest",
        _manifest(
            [
                _payload("components", "primary", "a", first),
                _payload("components", "primary", "b", second),
            ]
        ),
    )

    with pytest.raises(ImportExecutionError, match="duplicate logical"):
        _execute(source, FakeGcsClient(), manifest)


def test_unknown_existing_target_is_never_overwritten():
    source, _, _, manifest = _bundle()
    target = FakeGcsClient()
    object_name = (
        "project/pipeline/registry/v2/_quarantine/import-1/components/primary/model.onnx"
    )
    existing = target.put_object(object_name, b"attacker")

    with pytest.raises(ImportExecutionError, match="unknown generation"):
        _execute(source, target, manifest)

    assert target.get_live_object(object_name) == existing


def test_known_matching_generation_allows_idempotent_retry():
    source, _, _, manifest = _bundle()
    target = FakeGcsClient()
    first = _execute(source, target, manifest)
    known = {
        item.object_name: item.generation
        for item in (first.source_manifest, *first.payloads)
    }

    second = _execute(
        source,
        target,
        manifest,
        known_quarantine_generations=known,
    )

    assert second == first


def test_partial_copy_reports_written_generations_without_completion():
    class FailingTarget(FakeGcsClient):
        def put_object(self, object_name, data, *, if_generation_match=None):
            if object_name.endswith("schema.json"):
                raise GenerationPreconditionError("simulated race")
            return super().put_object(
                object_name, data, if_generation_match=if_generation_match
            )

    source, _, _, manifest = _bundle()
    target = FailingTarget()
    with pytest.raises(PartialImportCopyError) as raised:
        _execute(source, target, manifest)

    assert [item.payload_key for item in raised.value.copied] == [
        "source_manifest",
        "primary",
    ]
    assert target.get_live_object(
        "project/pipeline/registry/v2/_quarantine/import-1/components/primary/model.onnx"
    ) is not None

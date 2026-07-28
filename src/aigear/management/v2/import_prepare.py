"""Prepare immutable manifests and signed evidence for external imports.

All GCS reads and all signer calls happen in this module, before the short
Registry commit transaction implemented by the following Phase C tasks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Tuple

from aigear.management.v2.attestation import (
    AttestationRecord,
    DigestSigner,
    create_attestation,
)
from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.content_policy import (
    ContentGovernance,
    ContentInspectionEvidence,
    compute_governance_digest,
)
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_executor import (
    ImportExecutorCompletion,
    parse_import_source_manifest,
)
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    ProducerSpec,
    compute_asset_version_id,
)
from aigear.management.v2.records.import_operation import (
    ImportOperationRecord,
    ImportPhase,
)

__all__ = [
    "ImportPrepareError",
    "DeclaredComponent",
    "DeclaredAttachment",
    "ExternalImportDeclaration",
    "PreparedImportPayload",
    "PreparedExternalImport",
    "parse_external_import_declaration",
    "prepare_external_import",
]

_DECLARATION_KEYS = frozenset(
    {
        "asset_type",
        "name",
        "display_version",
        "components",
        "attachments",
        "producer_spec",
        "schema_contract_digest",
        "runtime_contract_digest",
        "policy_version",
        "governance",
    }
)
_COMPONENT_KEYS = frozenset({"payload_key", "role", "logical_name"})
_ATTACHMENT_KEYS = frozenset(
    {"payload_key", "attachment_kind", "logical_name"}
)
_PRODUCER_KEYS = frozenset(
    {"source_commit", "image_digest", "code_digest", "config_digest"}
)
_GOVERNANCE_KEYS = frozenset(
    {
        "owner",
        "data_classification",
        "purpose",
        "license_or_consent_ref",
        "residency",
        "retention_class",
        "legal_hold",
        "policy_version",
    }
)


class ImportPrepareError(ValueError):
    pass


def _aware(field_name: str, value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImportPrepareError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportPrepareError(f"{field_name} must be timezone-aware")


def _closed_mapping(field_name: str, value: object, keys: frozenset[str]) -> dict:
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise ImportPrepareError(f"{field_name} has unknown or missing fields")
    return value


def _typed(field_name: str, value: object) -> TypedId:
    try:
        return TypedId.from_typed(value)
    except Exception as exc:
        raise ImportPrepareError(f"{field_name} must be a typed SHA-256") from exc


@dataclass(frozen=True)
class DeclaredComponent:
    payload_key: str
    role: str
    logical_name: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "payload_key", validate_segment(self.payload_key, field_name="payload_key")
        )
        object.__setattr__(self, "role", validate_segment(self.role, field_name="role"))
        object.__setattr__(
            self,
            "logical_name",
            validate_segment(self.logical_name, field_name="logical_name"),
        )


@dataclass(frozen=True)
class DeclaredAttachment:
    payload_key: str
    attachment_kind: str
    logical_name: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "payload_key", validate_segment(self.payload_key, field_name="payload_key")
        )
        object.__setattr__(
            self,
            "attachment_kind",
            validate_segment(self.attachment_kind, field_name="attachment_kind"),
        )
        object.__setattr__(
            self,
            "logical_name",
            validate_segment(self.logical_name, field_name="logical_name"),
        )


@dataclass(frozen=True)
class ExternalImportDeclaration:
    asset_type: str
    name: str
    display_version: str
    components: Tuple[DeclaredComponent, ...]
    attachments: Tuple[DeclaredAttachment, ...]
    producer_spec: ProducerSpec
    schema_contract_digest: TypedId
    runtime_contract_digest: TypedId
    policy_version: str
    governance: ContentGovernance
    declaration_digest: TypedId

    def __post_init__(self) -> None:
        for field_name in ("asset_type", "name", "display_version"):
            object.__setattr__(
                self,
                field_name,
                validate_segment(getattr(self, field_name), field_name=field_name),
            )
        if not self.components or not all(
            isinstance(value, DeclaredComponent) for value in self.components
        ):
            raise ImportPrepareError("declaration must contain components")
        if not all(
            isinstance(value, DeclaredAttachment) for value in self.attachments
        ):
            raise ImportPrepareError("attachments must contain DeclaredAttachment values")
        component_keys = [value.payload_key for value in self.components]
        attachment_keys = [value.payload_key for value in self.attachments]
        if len(set(component_keys)) != len(component_keys):
            raise ImportPrepareError("component payload keys must be unique")
        if len(set(attachment_keys)) != len(attachment_keys):
            raise ImportPrepareError("attachment payload keys must be unique")
        semantic_components = [
            (value.role, value.logical_name) for value in self.components
        ]
        semantic_attachments = [
            (value.attachment_kind, value.logical_name)
            for value in self.attachments
        ]
        if len(set(semantic_components)) != len(semantic_components):
            raise ImportPrepareError("component semantic identities must be unique")
        if len(set(semantic_attachments)) != len(semantic_attachments):
            raise ImportPrepareError("attachment semantic identities must be unique")
        if not isinstance(self.producer_spec, ProducerSpec):
            raise ImportPrepareError("producer_spec must be a ProducerSpec")
        if not isinstance(self.governance, ContentGovernance):
            raise ImportPrepareError("governance must be ContentGovernance")
        if self.governance.policy_version != self.policy_version:
            raise ImportPrepareError("governance and declaration policy versions differ")


@dataclass(frozen=True)
class PreparedImportPayload:
    payload_kind: str
    payload_key: str
    semantic_kind: str
    logical_name: str
    media_type: str
    blob_id: TypedId
    size_bytes: int
    crc32c: str
    source_bucket: str
    source_object_name: str
    source_generation: str
    quarantine_bucket: str
    quarantine_object_name: str
    quarantine_generation: str

    def canonical_dict(self) -> dict:
        return {
            "payload_kind": self.payload_kind,
            "payload_key": self.payload_key,
            "semantic_kind": self.semantic_kind,
            "logical_name": self.logical_name,
            "media_type": self.media_type,
            "blob_id": self.blob_id.typed,
            "size_bytes": self.size_bytes,
            "crc32c": self.crc32c,
            "source_bucket": self.source_bucket,
            "source_object_name": self.source_object_name,
            "source_generation": self.source_generation,
            "quarantine_bucket": self.quarantine_bucket,
            "quarantine_object_name": self.quarantine_object_name,
            "quarantine_generation": self.quarantine_generation,
        }


@dataclass(frozen=True)
class PreparedExternalImport:
    operation_id: str
    ticket_digest: TypedId
    environment_fingerprint: TypedId
    fencing_token: int
    completion_payload_set_digest: TypedId
    inspection_evidence_digest: TypedId
    declaration: ExternalImportDeclaration
    canonical_manifest: Mapping[str, object]
    asset_version_id: TypedId
    payloads: Tuple[PreparedImportPayload, ...]
    manifest_integrity_attestation: AttestationRecord
    source_provenance_attestation: AttestationRecord
    prepared_at: str
    prepared_digest: TypedId

    def __post_init__(self) -> None:
        _aware("prepared_at", self.prepared_at)
        if self.asset_version_id != compute_asset_version_id(dict(self.canonical_manifest)):
            raise ImportPrepareError("asset_version_id does not match canonical manifest")
        manifest_subject = self.manifest_integrity_attestation.unsigned_envelope[
            "subject"
        ]
        if (
            self.manifest_integrity_attestation.attestation_kind
            != "asset_manifest_integrity"
            or manifest_subject.get("asset_version_id") != self.asset_version_id.typed
            or manifest_subject.get("manifest_digest") != self.asset_version_id.typed
        ):
            raise ImportPrepareError("manifest attestation does not match prepared asset")
        provenance_subject = self.source_provenance_attestation.unsigned_envelope[
            "subject"
        ]
        if (
            self.source_provenance_attestation.attestation_kind
            != "source_provenance"
            or provenance_subject.get("operation_id") != self.operation_id
            or provenance_subject.get("fencing_token") != self.fencing_token
            or provenance_subject.get("asset_version_id")
            != self.asset_version_id.typed
        ):
            raise ImportPrepareError("provenance attestation does not match import")
        if self.prepared_digest != _prepared_digest(self):
            raise ImportPrepareError("prepared_digest does not match prepared import")


def _declaration_core(value: Mapping[str, object]) -> dict:
    return {"domain": "aigear.external-import-declaration.v2", **value}


def parse_external_import_declaration(
    value: Mapping[str, object],
) -> ExternalImportDeclaration:
    raw = _closed_mapping("declaration", value, _DECLARATION_KEYS)
    raw_components = raw["components"]
    raw_attachments = raw["attachments"]
    if not isinstance(raw_components, list) or not isinstance(raw_attachments, list):
        raise ImportPrepareError("components and attachments must be arrays")
    components = []
    for item in raw_components:
        item = _closed_mapping("component", item, _COMPONENT_KEYS)
        components.append(DeclaredComponent(**item))
    attachments = []
    for item in raw_attachments:
        item = _closed_mapping("attachment", item, _ATTACHMENT_KEYS)
        attachments.append(DeclaredAttachment(**item))
    producer = _closed_mapping("producer_spec", raw["producer_spec"], _PRODUCER_KEYS)
    governance = _closed_mapping("governance", raw["governance"], _GOVERNANCE_KEYS)
    declaration_digest = TypedId.from_bare(
        hashlib.sha256(canonicalize_json(_declaration_core(raw))).hexdigest()
    )
    return ExternalImportDeclaration(
        asset_type=raw["asset_type"],
        name=raw["name"],
        display_version=raw["display_version"],
        components=tuple(components),
        attachments=tuple(attachments),
        producer_spec=ProducerSpec(
            source_commit=producer["source_commit"],
            image_digest=_typed("producer_spec.image_digest", producer["image_digest"]),
            code_digest=_typed("producer_spec.code_digest", producer["code_digest"]),
            config_digest=_typed("producer_spec.config_digest", producer["config_digest"]),
        ),
        schema_contract_digest=_typed(
            "schema_contract_digest", raw["schema_contract_digest"]
        ),
        runtime_contract_digest=_typed(
            "runtime_contract_digest", raw["runtime_contract_digest"]
        ),
        policy_version=raw["policy_version"],
        governance=ContentGovernance(**governance),
        declaration_digest=declaration_digest,
    )


def _prepared_digest(value: PreparedExternalImport) -> TypedId:
    core = {
        "domain": "aigear.prepared-external-import.v2",
        "operation_id": value.operation_id,
        "ticket_digest": value.ticket_digest.typed,
        "environment_fingerprint": value.environment_fingerprint.typed,
        "fencing_token": value.fencing_token,
        "completion_payload_set_digest": value.completion_payload_set_digest.typed,
        "inspection_evidence_digest": value.inspection_evidence_digest.typed,
        "declaration_digest": value.declaration.declaration_digest.typed,
        "asset_version_id": value.asset_version_id.typed,
        "payloads": [item.canonical_dict() for item in value.payloads],
        "manifest_integrity_attestation_id": (
            value.manifest_integrity_attestation.attestation_id.typed
        ),
        "source_provenance_attestation_id": (
            value.source_provenance_attestation.attestation_id.typed
        ),
        "prepared_at": value.prepared_at,
    }
    return TypedId.from_bare(hashlib.sha256(canonicalize_json(core)).hexdigest())


def _verify_inputs(
    operation: ImportOperationRecord,
    completion: ImportExecutorCompletion,
    inspection: ContentInspectionEvidence,
) -> None:
    if (
        not isinstance(operation, ImportOperationRecord)
        or operation.phase != ImportPhase.INSPECTING
        or operation.ticket is None
        or operation.completion != completion.to_record()
    ):
        raise ImportPrepareError(
            "prepare requires the current inspecting operation and completion"
        )
    if (
        inspection.operation_id != operation.operation_id
        or inspection.ticket_digest != operation.ticket.ticket_digest
        or inspection.environment_fingerprint
        != operation.control_snapshot.environment_fingerprint
        or inspection.fencing_token != operation.fencing_token
        or inspection.payload_set_digest != completion.payload_set_digest
    ):
        raise ImportPrepareError("inspection evidence is not bound to the operation")


def prepare_external_import(
    operation: ImportOperationRecord,
    completion: ImportExecutorCompletion,
    inspection: ContentInspectionEvidence,
    *,
    quarantine_gcs: GcsClientV2,
    quarantine_bucket: str,
    manifest_signer: DigestSigner,
    provenance_signer: DigestSigner,
    prepared_at: str,
) -> PreparedExternalImport:
    """Exact-read, canonicalize, and sign; never call from a Registry transaction."""

    _verify_inputs(operation, completion, inspection)
    _aware("prepared_at", prepared_at)
    if not isinstance(quarantine_bucket, str) or not quarantine_bucket:
        raise ImportPrepareError("quarantine_bucket must be non-empty")
    manifest_descriptor = completion.source_manifest
    if (
        manifest_descriptor.source_object_name != operation.source.object_name
        or manifest_descriptor.source_generation != operation.source.generation
        or manifest_descriptor.size_bytes != operation.source.size_bytes
        or manifest_descriptor.object_name
        != f"{operation.target_quarantine_prefix}source-manifest.json"
    ):
        raise ImportPrepareError("completion source manifest is not bound to operation")
    try:
        source_manifest_snapshot = quarantine_gcs.get_object(
            manifest_descriptor.object_name,
            generation=manifest_descriptor.generation,
        )
    except Exception as exc:
        raise ImportPrepareError("exact quarantine source manifest is unavailable") from exc
    manifest_sha = hashlib.sha256(source_manifest_snapshot.data).hexdigest()
    if (
        source_manifest_snapshot.object_name != manifest_descriptor.object_name
        or source_manifest_snapshot.generation != manifest_descriptor.generation
        or source_manifest_snapshot.sha256 != manifest_descriptor.sha256
        or manifest_sha != manifest_descriptor.sha256
        or source_manifest_snapshot.size_bytes != manifest_descriptor.size_bytes
        or source_manifest_snapshot.crc32c != manifest_descriptor.crc32c
    ):
        raise ImportPrepareError("quarantine source manifest drifted")
    source_manifest = parse_import_source_manifest(
        source_manifest_snapshot.data, ticket_source=operation.source
    )
    declaration = parse_external_import_declaration(source_manifest.declaration)
    if compute_governance_digest(declaration.governance) != inspection.governance_digest:
        raise ImportPrepareError("inspection governance does not match source declaration")

    component_declarations = {
        value.payload_key: value for value in declaration.components
    }
    attachment_declarations = {
        value.payload_key: value for value in declaration.attachments
    }
    completion_by_path = {
        (value.payload_kind, value.payload_key): value
        for value in completion.payloads
    }
    manifest_payloads_by_path = {
        (value.payload_kind, value.payload_key): value
        for value in source_manifest.payloads
    }
    expected_paths = {
        *(("components", value.payload_key) for value in declaration.components),
        *(("attachments", value.payload_key) for value in declaration.attachments),
    }
    if (
        set(completion_by_path) != expected_paths
        or set(manifest_payloads_by_path) != expected_paths
    ):
        raise ImportPrepareError(
            "source declaration does not cover the complete payload set"
        )
    inspection_by_path = {
        (value.payload_kind, value.payload_key): value
        for value in inspection.payloads
    }
    if set(inspection_by_path) != expected_paths:
        raise ImportPrepareError(
            "inspection evidence does not cover the complete payload set"
        )

    prepared_payloads = []
    components = []
    for path in sorted(expected_paths):
        descriptor = completion_by_path[path]
        manifest_payload = manifest_payloads_by_path[path]
        inspected = inspection_by_path[path]
        if (
            descriptor.file_name != manifest_payload.file_name
            or descriptor.media_type != manifest_payload.media_type
            or descriptor.source_object_name != manifest_payload.source.object_name
            or descriptor.source_generation != manifest_payload.source.generation
            or descriptor.size_bytes != manifest_payload.source.size_bytes
        ):
            raise ImportPrepareError(
                "completion payload does not match the exact source manifest"
            )
        try:
            snapshot = quarantine_gcs.get_object(
                descriptor.object_name, generation=descriptor.generation
            )
        except Exception as exc:
            raise ImportPrepareError("exact quarantine payload is unavailable") from exc
        digest = hashlib.sha256(snapshot.data).hexdigest()
        if (
            not descriptor.object_name.startswith(operation.target_quarantine_prefix)
            or snapshot.object_name != descriptor.object_name
            or snapshot.generation != descriptor.generation
            or snapshot.sha256 != descriptor.sha256
            or digest != descriptor.sha256
            or snapshot.size_bytes != descriptor.size_bytes
            or snapshot.crc32c != descriptor.crc32c
            or inspected.quarantine_object_name != descriptor.object_name
            or inspected.quarantine_generation != descriptor.generation
            or inspected.sha256 != descriptor.sha256
            or inspected.size_bytes != descriptor.size_bytes
        ):
            raise ImportPrepareError("quarantine payload or inspection evidence drifted")
        blob_id = TypedId.from_bare(digest)
        if descriptor.payload_kind == "components":
            declared = component_declarations[descriptor.payload_key]
            semantic_kind = declared.role
            logical_name = declared.logical_name
            components.append(
                AssetComponent(
                    role=declared.role,
                    logical_name=declared.logical_name,
                    blob_id=blob_id,
                    media_type=descriptor.media_type,
                )
            )
        else:
            declared = attachment_declarations[descriptor.payload_key]
            semantic_kind = declared.attachment_kind
            logical_name = declared.logical_name
        prepared_payloads.append(
            PreparedImportPayload(
                payload_kind=descriptor.payload_kind,
                payload_key=descriptor.payload_key,
                semantic_kind=semantic_kind,
                logical_name=logical_name,
                media_type=descriptor.media_type,
                blob_id=blob_id,
                size_bytes=descriptor.size_bytes,
                crc32c=descriptor.crc32c,
                source_bucket=operation.source.bucket,
                source_object_name=descriptor.source_object_name,
                source_generation=descriptor.source_generation,
                quarantine_bucket=quarantine_bucket,
                quarantine_object_name=descriptor.object_name,
                quarantine_generation=descriptor.generation,
            )
        )

    components.sort(key=lambda value: (value.role, value.logical_name))
    canonical_manifest = {
        "environment_id": operation.target_environment_id,
        "environment_fingerprint": (
            operation.control_snapshot.environment_fingerprint.typed
        ),
        "asset_type": declaration.asset_type,
        "name": declaration.name,
        "components": [value.to_manifest_dict() for value in components],
        "input_bindings": [],
        "producer_spec": declaration.producer_spec.to_manifest_dict(),
        "schema_contract_digest": declaration.schema_contract_digest.typed,
        "runtime_contract_digest": declaration.runtime_contract_digest.typed,
        "policy_version": declaration.policy_version,
    }
    asset_version_id = compute_asset_version_id(canonical_manifest)
    manifest_attestation = create_attestation(
        schema_version=operation.schema_version,
        attestation_kind="asset_manifest_integrity",
        environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        subject={
            "asset_version_id": asset_version_id.typed,
            "manifest_digest": asset_version_id.typed,
            "manifest_schema_version": operation.schema_version,
        },
        signer=manifest_signer,
    )
    provenance_attestation = create_attestation(
        schema_version=operation.schema_version,
        attestation_kind="source_provenance",
        environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        subject={
            "provenance_kind": "external_import",
            "asset_version_id": asset_version_id.typed,
            "operation_id": operation.operation_id,
            "fencing_token": operation.fencing_token,
            "ticket_digest": operation.ticket.ticket_digest.typed,
            "completion_payload_set_digest": completion.payload_set_digest.typed,
            "inspection_evidence_digest": inspection.evidence_digest.typed,
            "declaration_digest": declaration.declaration_digest.typed,
            "source_manifest": {
                "environment_id": operation.source.environment_id,
                "project_id": operation.source.project_id,
                "bucket": operation.source.bucket,
                "object_name": operation.source.object_name,
                "generation": operation.source.generation,
                "region": operation.source.region,
                "size_bytes": operation.source.size_bytes,
                "quarantine_bucket": quarantine_bucket,
                "quarantine_object_name": manifest_descriptor.object_name,
                "quarantine_generation": manifest_descriptor.generation,
                "sha256": manifest_descriptor.sha256,
            },
            "payloads": [
                value.canonical_dict() for value in prepared_payloads
            ],
            "governance_digest": inspection.governance_digest.typed,
            "producer_evidence_digest": inspection.producer_evidence_digest.typed,
        },
        signer=provenance_signer,
    )
    values = {
        "operation_id": operation.operation_id,
        "ticket_digest": operation.ticket.ticket_digest,
        "environment_fingerprint": operation.control_snapshot.environment_fingerprint,
        "fencing_token": operation.fencing_token,
        "completion_payload_set_digest": completion.payload_set_digest,
        "inspection_evidence_digest": inspection.evidence_digest,
        "declaration": declaration,
        "canonical_manifest": canonical_manifest,
        "asset_version_id": asset_version_id,
        "payloads": tuple(prepared_payloads),
        "manifest_integrity_attestation": manifest_attestation,
        "source_provenance_attestation": provenance_attestation,
        "prepared_at": prepared_at,
    }
    provisional = PreparedExternalImport.__new__(PreparedExternalImport)
    for field_name, value in values.items():
        object.__setattr__(provisional, field_name, value)
    return PreparedExternalImport(
        **values,
        prepared_digest=_prepared_digest(provisional),
    )

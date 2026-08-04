"""Pure short-transaction commit for a fully prepared external import."""

from __future__ import annotations

from dataclasses import dataclass, replace

from aigear.management.v2.attestation import AttestationVerifier, verify_attestation
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.import_adoption import AdoptedImportBlobs
from aigear.management.v2.import_identity_reservation import (
    ImportIdentityReservation,
    verify_import_identity_reservation,
)
from aigear.management.v2.import_prepare import PreparedExternalImport
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    AssetVersionRecord,
    LifecycleState,
    TrustState,
)
from aigear.management.v2.records.blob_claim import ClaimState
from aigear.management.v2.records.import_operation import (
    ImportOperationRecord,
    ImportPhase,
    ImportProvenanceIndexRecord,
)
from aigear.management.v2.records.label import (
    LabelRecord,
    ReadableManifestProjection,
)
from aigear.management.v2.records.lineage import (
    AttachmentEdge,
    ComponentEdge,
    OwnerKind,
    compute_attachment_edge_id,
    compute_component_edge_id,
)
from aigear.management.v2.records.outbox import (
    OutboxEventRecord,
    ProjectionKind,
)
from aigear.management.v2.security_journal import SecurityJournalEntry

__all__ = [
    "ImportCommitError",
    "VerifiedImportCommit",
    "verify_import_commit_evidence",
    "commit_external_import",
]


class ImportCommitError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedImportCommit:
    prepared: PreparedExternalImport
    adopted: AdoptedImportBlobs
    identity_reservation: ImportIdentityReservation
    journal_entry: SecurityJournalEntry


def verify_import_commit_evidence(
    prepared: PreparedExternalImport,
    adopted: AdoptedImportBlobs,
    identity_reservation: ImportIdentityReservation,
    journal_entry: SecurityJournalEntry,
    *,
    manifest_verifier: AttestationVerifier,
    provenance_verifier: AttestationVerifier,
    location_verifier: AttestationVerifier,
) -> VerifiedImportCommit:
    """Perform cryptographic work before entering the Registry transaction."""

    verify_import_identity_reservation(
        prepared,
        reservation=identity_reservation,
        journal_entry=journal_entry,
    )
    try:
        verify_attestation(prepared.manifest_integrity_attestation, manifest_verifier)
        verify_attestation(prepared.source_provenance_attestation, provenance_verifier)
        for material in adopted.materials:
            if not material.reused_existing:
                verify_attestation(material.location_attestation, location_verifier)
    except Exception as exc:
        raise ImportCommitError("prepared import signature verification failed") from exc
    if (
        adopted.operation_id != prepared.operation_id
        or adopted.fencing_token != prepared.fencing_token
        or adopted.prepared_digest != prepared.prepared_digest
    ):
        raise ImportCommitError("adopted Blob evidence does not match prepared import")
    material_ids = {value.blob.blob_id for value in adopted.materials}
    if material_ids != {value.blob_id for value in prepared.payloads}:
        raise ImportCommitError("adopted Blob evidence does not cover payload set")
    return VerifiedImportCommit(
        prepared=prepared,
        adopted=adopted,
        identity_reservation=identity_reservation,
        journal_entry=journal_entry,
    )


def _write_count(evidence: VerifiedImportCommit) -> int:
    new_blobs = sum(
        1 for value in evidence.adopted.materials if not value.reused_existing
    )
    components = sum(
        1 for value in evidence.prepared.payloads if value.payload_kind == "components"
    )
    attachments = len(evidence.prepared.payloads) - components
    return 8 + 4 * new_blobs + components + attachments


def commit_external_import(
    registry,
    layout: GcsLayoutV2,
    operation: ImportOperationRecord,
    evidence: VerifiedImportCommit,
    *,
    committed_at: str,
) -> ImportOperationRecord:
    """Commit Registry facts atomically; performs no GCS, scanner, or KMS call."""

    prepared = evidence.prepared
    if (
        operation.phase is not ImportPhase.COMMITTING
        or operation.operation_id != prepared.operation_id
        or operation.fencing_token != prepared.fencing_token
        or operation.ticket is None
        or operation.ticket.ticket_digest != prepared.ticket_digest
    ):
        raise ImportCommitError("commit evidence is stale for the import operation")
    writes = _write_count(evidence)
    if writes > operation.write_budget or writes >= 500:
        raise ImportCommitError(
            f"import requires {writes} writes but budget is {operation.write_budget}"
        )

    material_by_id = evidence.adopted.by_blob_id()
    component_payloads = [
        value
        for value in prepared.payloads
        if value.payload_kind == "components"
    ]
    attachment_payloads = [
        value
        for value in prepared.payloads
        if value.payload_kind == "attachments"
    ]
    components = tuple(
        sorted(
            (
                AssetComponent(
                    role=value.semantic_kind,
                    logical_name=value.logical_name,
                    blob_id=value.blob_id,
                    media_type=value.media_type,
                )
                for value in component_payloads
            ),
            key=lambda value: (value.role, value.logical_name),
        )
    )
    label_id = evidence.identity_reservation.label_id

    def commit(tx):
        current = tx.get_import_operation(operation.idempotency_key_hash)
        if current != operation:
            raise ImportCommitError("import operation changed before atomic commit")

        for blob_id, material in material_by_id.items():
            existing = tx.get_blob(blob_id)
            if material.reused_existing:
                if existing != material.blob:
                    raise ImportCommitError("reused Blob changed before atomic commit")
                continue
            claim = tx.get_blob_claim(blob_id)
            if (
                existing is not None
                or claim is None
                or claim.state is not ClaimState.ADOPTING
                or claim.operation_id != operation.operation_id
                or claim.fencing_token != operation.fencing_token
                or claim.expected_generation != material.blob.generation
            ):
                raise ImportCommitError("new Blob claim was lost before atomic commit")
            tx.put_attestation(material.location_attestation)
            tx.put_blob(material.blob)
            tx.put_blob_location_revision(material.location_revision)
            tx.put_blob_claim(replace(claim, state=ClaimState.CONSUMED))

        tx.put_attestation(prepared.manifest_integrity_attestation)
        tx.put_attestation(prepared.source_provenance_attestation)
        tx.put_import_provenance(
            ImportProvenanceIndexRecord(
                schema_version=operation.schema_version,
                environment_fingerprint=prepared.environment_fingerprint,
                asset_version_id=prepared.asset_version_id,
                source_provenance_attestation_ref=(
                    prepared.source_provenance_attestation.attestation_id
                ),
                operation_id=operation.operation_id,
                ticket_digest=prepared.ticket_digest,
                identity_reservation_entry_id=(
                    evidence.identity_reservation.journal_entry_id
                ),
            )
        )
        existing_asset = tx.get_asset_version(prepared.asset_version_id)
        if existing_asset is None:
            asset = AssetVersionRecord(
                schema_version=operation.schema_version,
                environment_id=operation.target_environment_id,
                environment_fingerprint=prepared.environment_fingerprint,
                asset_version_id=prepared.asset_version_id,
                asset_type=prepared.declaration.asset_type,
                name=prepared.declaration.name,
                manifest_digest=prepared.asset_version_id,
                record_revision=1,
                components=components,
                input_bindings=(),
                producer_spec=prepared.declaration.producer_spec,
                schema_contract_digest=prepared.declaration.schema_contract_digest,
                runtime_contract_digest=prepared.declaration.runtime_contract_digest,
                lifecycle_state=LifecycleState.ACTIVE,
                trust_state=TrustState.VERIFIED,
                policy_version=prepared.declaration.policy_version,
                manifest_integrity_attestation_ref=(
                    prepared.manifest_integrity_attestation.attestation_id
                ),
                reference_epoch=1,
                created_at=committed_at,
            )
            tx.put_asset_version(asset)
        else:
            if existing_asset.canonical_manifest() != prepared.canonical_manifest:
                raise ImportCommitError("existing AssetVersion manifest conflict")
            if (
                existing_asset.lifecycle_state is not LifecycleState.ACTIVE
                or existing_asset.trust_state is TrustState.REVOKED
            ):
                raise ImportCommitError(
                    "existing AssetVersion is not eligible for import reuse"
                )
            asset = existing_asset

        label = LabelRecord(
            schema_version=operation.schema_version,
            environment_fingerprint=prepared.environment_fingerprint,
            label_id=label_id,
            asset_type=prepared.declaration.asset_type,
            asset_name=prepared.declaration.name,
            display_version=prepared.declaration.display_version,
            asset_version_id=prepared.asset_version_id,
            projection_source_revision=1,
            readable_manifest=ReadableManifestProjection.initial(
                layout.to_uri(
                    layout.asset_projection(
                        prepared.declaration.asset_type,
                        prepared.declaration.name,
                        prepared.declaration.display_version,
                    )
                )
            ),
            created_by=operation.owner_principal,
            created_at=committed_at,
        )
        tx.put_label(label)
        for component in components:
            tx.put_component_edge(
                ComponentEdge(
                    schema_version=operation.schema_version,
                    environment_fingerprint=prepared.environment_fingerprint,
                    component_edge_id=compute_component_edge_id(
                        prepared.asset_version_id,
                        component.role,
                        component.logical_name,
                        component.blob_id,
                    ),
                    asset_version_id=prepared.asset_version_id,
                    blob_id=component.blob_id,
                    role=component.role,
                    logical_name=component.logical_name,
                    created_at=committed_at,
                )
            )
        for attachment in attachment_payloads:
            tx.put_attachment_edge(
                AttachmentEdge(
                    schema_version=operation.schema_version,
                    environment_fingerprint=prepared.environment_fingerprint,
                    attachment_edge_id=compute_attachment_edge_id(
                        OwnerKind.OPERATION,
                        operation.operation_id,
                        attachment.semantic_kind,
                        attachment.logical_name,
                        attachment.blob_id,
                    ),
                    owner_kind=OwnerKind.OPERATION,
                    owner_id=operation.operation_id,
                    attachment_kind=attachment.semantic_kind,
                    logical_name=attachment.logical_name,
                    blob_id=attachment.blob_id,
                    media_type=attachment.media_type,
                    created_at=committed_at,
                )
            )
        tx.put_outbox_event(
            OutboxEventRecord.pending(
                schema_version=operation.schema_version,
                kind=ProjectionKind.ASSET_MANIFEST,
                subject_id=label_id,
                projection_schema_version="1.0",
                projection_source_revision=1,
                created_at=committed_at,
            )
        )
        succeeded = replace(
            operation,
            phase=ImportPhase.SUCCEEDED,
            revision=operation.revision + 1,
            result_asset_version_id=prepared.asset_version_id,
            result_label_id=label_id,
            result_source_provenance_attestation_ref=(
                prepared.source_provenance_attestation.attestation_id
            ),
            identity_reservation_entry_id=(
                evidence.identity_reservation.journal_entry_id
            ),
            identity_reservation_sequence=(
                evidence.identity_reservation.journal_sequence
            ),
            lease_expires_at=None,
            updated_at=committed_at,
            finished_at=committed_at,
        )
        return tx.put_import_operation(succeeded)

    return registry.run_atomic(commit)

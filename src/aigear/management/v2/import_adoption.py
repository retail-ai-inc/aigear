"""Claim and adopt prepared import payloads into the canonical Blob pool."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Tuple

from aigear.management.v2.attestation import (
    AttestationRecord,
    AttestationVerifier,
    DigestSigner,
    create_attestation,
    verify_attestation,
)
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.fake_gcs import GenerationPreconditionError
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_prepare import (
    PreparedExternalImport,
    PreparedImportPayload,
)
from aigear.management.v2.records.blob import (
    AvailabilityState,
    BlobLocationRevision,
    BlobRecord,
    LocationOperationKind,
    compute_genesis_location_chain_head,
    compute_location_chain_head,
)
from aigear.management.v2.records.blob_claim import BlobClaim, ClaimState
from aigear.management.v2.records.import_operation import (
    ImportOperationRecord,
    ImportPhase,
)

__all__ = [
    "ImportAdoptionError",
    "AdoptedBlobMaterial",
    "AdoptedImportBlobs",
    "adopt_import_blobs",
]


class ImportAdoptionError(ValueError):
    pass


@dataclass(frozen=True)
class AdoptedBlobMaterial:
    blob: BlobRecord
    location_revision: BlobLocationRevision | None
    location_attestation: AttestationRecord | None
    reused_existing: bool


@dataclass(frozen=True)
class AdoptedImportBlobs:
    operation_id: str
    fencing_token: int
    prepared_digest: TypedId
    materials: Tuple[AdoptedBlobMaterial, ...]

    def by_blob_id(self) -> dict[TypedId, AdoptedBlobMaterial]:
        return {value.blob.blob_id: value for value in self.materials}


def _instant(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImportAdoptionError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportAdoptionError(f"{field_name} must be timezone-aware")
    return parsed


def _request_digest(operation_id: str, blob_id: TypedId) -> TypedId:
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            ["aigear.blob-claim-request.v2", operation_id, blob_id.typed]
        )
    )


def _validate_operation(
    operation: ImportOperationRecord, prepared: PreparedExternalImport
) -> None:
    if (
        not isinstance(operation, ImportOperationRecord)
        or operation.phase != ImportPhase.PREPARING
        or operation.operation_id != prepared.operation_id
        or operation.fencing_token != prepared.fencing_token
        or operation.control_snapshot.environment_fingerprint
        != prepared.environment_fingerprint
        or operation.ticket is None
        or operation.ticket.ticket_digest != prepared.ticket_digest
    ):
        raise ImportAdoptionError(
            "adoption requires the current preparing operation and evidence"
        )


def _validate_existing_blob(
    registry,
    gcs: GcsClientV2,
    *,
    blob: BlobRecord,
    expected: PreparedImportPayload,
    expected_environment_fingerprint: TypedId,
    verifier: AttestationVerifier,
) -> None:
    if (
        blob.environment_fingerprint != expected_environment_fingerprint
        or blob.availability_state is not AvailabilityState.READY
        or blob.blob_id != expected.blob_id
        or blob.sha256 != expected.blob_id.bare
        or blob.size_bytes != expected.size_bytes
    ):
        raise ImportAdoptionError("existing Blob record is not reusable")
    revision = registry.get_blob_location_revision(
        blob.blob_id, blob.current_location_revision
    )
    attestation = registry.get_attestation(blob.current_location_attestation_ref)
    if (
        revision is None
        or attestation is None
        or attestation.attestation_kind != "blob_location"
        or attestation.environment_fingerprint != expected_environment_fingerprint
        or revision.location_attestation_ref != attestation.attestation_id
        or revision.location_chain_head != blob.location_chain_head
        or revision.bucket != blob.bucket
        or revision.object_name != blob.object_name
        or revision.generation != blob.generation
        or revision.sha256 != blob.sha256
        or revision.size_bytes != blob.size_bytes
    ):
        raise ImportAdoptionError("existing Blob location chain is incomplete")
    subject = attestation.unsigned_envelope["subject"]
    try:
        previous_head = TypedId.from_typed(subject["previous_chain_head"])
    except Exception as exc:
        raise ImportAdoptionError(
            "existing Blob location attestation has an invalid previous head"
        ) from exc
    if blob.current_location_revision == 1:
        if previous_head != compute_genesis_location_chain_head(
            blob.environment_fingerprint, blob.blob_id
        ):
            raise ImportAdoptionError("existing Blob genesis location head is invalid")
    else:
        previous_revision = registry.get_blob_location_revision(
            blob.blob_id, blob.current_location_revision - 1
        )
        if (
            previous_revision is None
            or previous_revision.location_chain_head != previous_head
            or subject.get("previous_location_attestation_ref")
            != previous_revision.location_attestation_ref.typed
        ):
            raise ImportAdoptionError("existing Blob previous location link is invalid")
    expected_head = compute_location_chain_head(
        previous_head, attestation.attestation_id
    )
    if blob.location_chain_head != expected_head:
        raise ImportAdoptionError("existing Blob location chain head is invalid")
    try:
        verify_attestation(attestation, verifier)
        snapshot = gcs.get_object(blob.object_name, generation=blob.generation)
    except Exception as exc:
        raise ImportAdoptionError(
            "existing Blob location evidence cannot be verified"
        ) from exc
    if (
        subject.get("blob_id") != blob.blob_id.typed
        or subject.get("location_revision") != blob.current_location_revision
        or subject.get("bucket") != blob.bucket
        or subject.get("object_name") != blob.object_name
        or subject.get("generation") != blob.generation
        or subject.get("sha256") != blob.sha256
        or subject.get("crc32c") != blob.crc32c
        or subject.get("size_bytes") != blob.size_bytes
        or snapshot.object_name != blob.object_name
        or snapshot.generation != blob.generation
        or snapshot.sha256 != blob.sha256
        or snapshot.size_bytes != blob.size_bytes
    ):
        raise ImportAdoptionError("existing Blob location evidence does not match bytes")


def _reserve_claims(
    registry,
    operation: ImportOperationRecord,
    prepared: PreparedExternalImport,
    layout: GcsLayoutV2,
    *,
    lease_expires_at: str,
) -> None:
    unique = {value.blob_id: value for value in prepared.payloads}

    def reserve(tx):
        current = tx.get_import_operation(operation.idempotency_key_hash)
        if current != operation:
            raise ImportAdoptionError("import operation changed during claim reservation")
        for blob_id in sorted(unique, key=lambda value: value.bare):
            if tx.get_blob(blob_id) is not None:
                continue
            request_digest = _request_digest(operation.operation_id, blob_id)
            existing = tx.get_blob_claim(blob_id)
            if existing is not None:
                if (
                    existing.state is not ClaimState.ADOPTING
                    or existing.operation_id != operation.operation_id
                    or existing.request_digest != request_digest
                    or existing.fencing_token != operation.fencing_token
                ):
                    raise ImportAdoptionError(
                        f"Blob {blob_id.typed!r} is claimed by another operation"
                    )
                continue
            tx.put_blob_claim(
                BlobClaim(
                    blob_id=blob_id,
                    claim_epoch=1,
                    fencing_token=operation.fencing_token,
                    operation_id=operation.operation_id,
                    expected_object_name=layout.canonical_blob(blob_id),
                    request_digest=request_digest,
                    state=ClaimState.ADOPTING,
                    lease_expires_at=lease_expires_at,
                )
            )

    registry.run_atomic(reserve)


def _record_generation(
    registry,
    operation: ImportOperationRecord,
    *,
    blob_id: TypedId,
    generation: str,
) -> None:
    def record(tx):
        current = tx.get_import_operation(operation.idempotency_key_hash)
        claim = tx.get_blob_claim(blob_id)
        if (
            current != operation
            or claim is None
            or claim.state is not ClaimState.ADOPTING
            or claim.operation_id != operation.operation_id
            or claim.fencing_token != operation.fencing_token
            or (
                claim.expected_generation is not None
                and claim.expected_generation != generation
            )
        ):
            raise ImportAdoptionError("Blob claim changed before generation commit")
        tx.put_blob_claim(replace(claim, expected_generation=generation))

    registry.run_atomic(record)


def _adopt_new_blob(
    registry,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    operation: ImportOperationRecord,
    payload: PreparedImportPayload,
    *,
    signer: DigestSigner,
    retention_class: str,
    legal_hold: bool,
) -> AdoptedBlobMaterial:
    claim = registry.get_blob_claim(payload.blob_id)
    if (
        claim is None
        or claim.state is not ClaimState.ADOPTING
        or claim.operation_id != operation.operation_id
        or claim.fencing_token != operation.fencing_token
        or claim.request_digest
        != _request_digest(operation.operation_id, payload.blob_id)
    ):
        raise ImportAdoptionError("operation does not hold the Blob adoption claim")
    try:
        source = gcs.get_object(
            payload.quarantine_object_name,
            generation=payload.quarantine_generation,
        )
    except Exception as exc:
        raise ImportAdoptionError("exact quarantine source is unavailable") from exc
    if (
        source.object_name != payload.quarantine_object_name
        or source.generation != payload.quarantine_generation
        or source.sha256 != payload.blob_id.bare
        or source.size_bytes != payload.size_bytes
        or source.crc32c != payload.crc32c
    ):
        raise ImportAdoptionError("exact quarantine source does not match prepared bytes")
    canonical_name = layout.canonical_blob(payload.blob_id)
    try:
        target = gcs.copy_object(
            payload.quarantine_object_name,
            payload.quarantine_generation,
            canonical_name,
            if_generation_match=0,
        )
    except GenerationPreconditionError:
        target = gcs.get_live_object(canonical_name)
        if target is None:
            raise
        if target.sha256 != payload.blob_id.bare or target.size_bytes != payload.size_bytes:
            raise ImportAdoptionError(
                "canonical path already contains bytes with another identity"
            )
    try:
        exact_target = gcs.get_object(canonical_name, generation=target.generation)
    except Exception as exc:
        raise ImportAdoptionError("canonical target exact generation is unavailable") from exc
    if (
        exact_target.sha256 != payload.blob_id.bare
        or exact_target.size_bytes != payload.size_bytes
        or exact_target.crc32c != target.crc32c
    ):
        raise ImportAdoptionError("canonical target failed post-copy verification")
    _record_generation(
        registry,
        operation,
        blob_id=payload.blob_id,
        generation=exact_target.generation,
    )

    previous_head = compute_genesis_location_chain_head(
        operation.control_snapshot.environment_fingerprint, payload.blob_id
    )
    attestation = create_attestation(
        schema_version=operation.schema_version,
        attestation_kind="blob_location",
        environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        subject={
            "blob_id": payload.blob_id.typed,
            "location_revision": 1,
            "previous_location_attestation_ref": None,
            "previous_chain_head": previous_head.typed,
            "bucket": layout.bucket_name,
            "object_name": canonical_name,
            "generation": exact_target.generation,
            "sha256": payload.blob_id.bare,
            "crc32c": exact_target.crc32c,
            "size_bytes": payload.size_bytes,
            "location_operation_kind": LocationOperationKind.EXTERNAL_IMPORT.value,
            "location_operation_id": operation.operation_id,
            "fencing_token": operation.fencing_token,
        },
        signer=signer,
    )
    chain_head = compute_location_chain_head(previous_head, attestation.attestation_id)
    blob = BlobRecord(
        schema_version=operation.schema_version,
        environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        blob_id=payload.blob_id,
        sha256=payload.blob_id.bare,
        size_bytes=payload.size_bytes,
        crc32c=exact_target.crc32c,
        bucket=layout.bucket_name,
        object_name=canonical_name,
        generation=exact_target.generation,
        current_location_revision=1,
        current_location_attestation_ref=attestation.attestation_id,
        location_chain_head=chain_head,
        availability_state=AvailabilityState.READY,
        retention_class=retention_class,
        legal_hold=legal_hold,
    )
    revision = BlobLocationRevision(
        schema_version=operation.schema_version,
        environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        blob_id=payload.blob_id,
        location_revision=1,
        bucket=layout.bucket_name,
        object_name=canonical_name,
        generation=exact_target.generation,
        sha256=payload.blob_id.bare,
        crc32c=exact_target.crc32c,
        size_bytes=payload.size_bytes,
        location_operation_id=operation.operation_id,
        location_operation_kind=LocationOperationKind.EXTERNAL_IMPORT,
        location_attestation_ref=attestation.attestation_id,
        location_chain_head=chain_head,
        reason="external import: initial canonical location",
    )
    return AdoptedBlobMaterial(blob, revision, attestation, False)


def adopt_import_blobs(
    registry,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    operation: ImportOperationRecord,
    prepared: PreparedExternalImport,
    *,
    location_signer: DigestSigner,
    existing_location_verifier: AttestationVerifier,
    now: str,
    claim_ttl: timedelta = timedelta(minutes=15),
) -> AdoptedImportBlobs:
    """Reserve all missing identities, then copy and sign outside Firestore."""

    _validate_operation(operation, prepared)
    instant = _instant("now", now)
    if claim_ttl <= timedelta(0):
        raise ImportAdoptionError("claim_ttl must be positive")
    if any(value.quarantine_bucket != layout.bucket_name for value in prepared.payloads):
        raise ImportAdoptionError("prepared quarantine bucket does not match layout")
    _reserve_claims(
        registry,
        operation,
        prepared,
        layout,
        lease_expires_at=(instant + claim_ttl).isoformat(),
    )

    unique = {}
    for payload in prepared.payloads:
        previous = unique.get(payload.blob_id)
        if previous is not None and (
            previous.size_bytes != payload.size_bytes
            or previous.crc32c != payload.crc32c
        ):
            raise ImportAdoptionError("same Blob identity has inconsistent metadata")
        unique[payload.blob_id] = payload

    materials = []
    for blob_id in sorted(unique, key=lambda value: value.bare):
        payload = unique[blob_id]
        existing = registry.get_blob(blob_id)
        if existing is not None:
            _validate_existing_blob(
                registry,
                gcs,
                blob=existing,
                expected=payload,
                expected_environment_fingerprint=(
                    operation.control_snapshot.environment_fingerprint
                ),
                verifier=existing_location_verifier,
            )
            materials.append(AdoptedBlobMaterial(existing, None, None, True))
        else:
            materials.append(
                _adopt_new_blob(
                    registry,
                    gcs,
                    layout,
                    operation,
                    payload,
                    signer=location_signer,
                    retention_class=prepared.declaration.governance.retention_class,
                    legal_hold=prepared.declaration.governance.legal_hold,
                )
            )
    return AdoptedImportBlobs(
        operation_id=operation.operation_id,
        fencing_token=operation.fencing_token,
        prepared_digest=prepared.prepared_digest,
        materials=tuple(materials),
    )

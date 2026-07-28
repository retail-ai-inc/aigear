"""Issue and verify short-lived, fenced external-import tickets."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime

from aigear.management.v2.attestation import AttestationVerifier, DigestSigner
from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.import_operation import (
    ImportOperationRecord,
    ImportPhase,
    ImportTicketRecord,
)

__all__ = [
    "ImportTicketError",
    "SignedImportTicket",
    "issue_import_ticket",
    "verify_import_ticket",
]


class ImportTicketError(ValueError):
    pass


def _aware(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImportTicketError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportTicketError(f"{field_name} must be timezone-aware")
    return parsed


def _unsigned(ticket: ImportTicketRecord, key_version: str) -> dict:
    return {
        "domain": "aigear.import-ticket.v2",
        "schema_version": ticket.schema_version,
        "environment_fingerprint": None,
        "operation_id": ticket.operation_id,
        "request_fingerprint": ticket.request_fingerprint.typed,
        "source": {
            "environment_id": ticket.source.environment_id,
            "project_id": ticket.source.project_id,
            "bucket": ticket.source.bucket,
            "object_name": ticket.source.object_name,
            "generation": ticket.source.generation,
            "region": ticket.source.region,
            "size_bytes": ticket.source.size_bytes,
        },
        "target_environment_id": ticket.target_environment_id,
        "target_quarantine_prefix": ticket.target_quarantine_prefix,
        "audience": ticket.audience,
        "executor_principal": ticket.executor_principal,
        "fencing_token": ticket.fencing_token,
        "issued_at": ticket.issued_at,
        "expires_at": ticket.expires_at,
        "key_version": key_version,
    }


@dataclass(frozen=True)
class SignedImportTicket:
    ticket: ImportTicketRecord
    environment_fingerprint: TypedId
    key_version: str
    signature_b64: str

    def __post_init__(self) -> None:
        if not isinstance(self.ticket, ImportTicketRecord):
            raise ImportTicketError("ticket must be an ImportTicketRecord")
        if not isinstance(self.environment_fingerprint, TypedId):
            raise ImportTicketError("environment_fingerprint must be a TypedId")
        if not isinstance(self.key_version, str) or not self.key_version:
            raise ImportTicketError("key_version must be non-empty")
        unsigned = _unsigned(self.ticket, self.key_version)
        unsigned["environment_fingerprint"] = self.environment_fingerprint.typed
        expected = TypedId.from_bare(hashlib.sha256(canonicalize_json(unsigned)).hexdigest())
        if expected != self.ticket.ticket_digest:
            raise ImportTicketError("ticket_digest does not match signed ticket fields")
        try:
            signature = base64.b64decode(self.signature_b64, validate=True)
        except (TypeError, ValueError) as exc:
            raise ImportTicketError("signature_b64 must be canonical base64") from exc
        if not signature:
            raise ImportTicketError("ticket signature must be non-empty")


def issue_import_ticket(
    operation: ImportOperationRecord,
    *,
    audience: str,
    executor_principal: str,
    issued_at: str,
    expires_at: str,
    signer: DigestSigner,
) -> SignedImportTicket:
    if not isinstance(operation, ImportOperationRecord):
        raise ImportTicketError("operation must be an ImportOperationRecord")
    if operation.phase != ImportPhase.RESERVED:
        raise ImportTicketError("only reserved import operations may receive a ticket")
    issued = _aware("issued_at", issued_at)
    expires = _aware("expires_at", expires_at)
    if expires <= issued:
        raise ImportTicketError("expires_at must be later than issued_at")
    if operation.lease_expires_at is None:
        raise ImportTicketError("import operation must have an active lease")
    if expires > _aware("operation.lease_expires_at", operation.lease_expires_at):
        raise ImportTicketError("ticket cannot outlive the import operation lease")
    if not audience or not executor_principal:
        raise ImportTicketError("audience and executor_principal must be non-empty")
    provisional = ImportTicketRecord(
        schema_version=operation.schema_version,
        ticket_digest=TypedId.from_bare("00" * 32),
        operation_id=operation.operation_id,
        request_fingerprint=operation.request_fingerprint,
        source=operation.source,
        target_environment_id=operation.target_environment_id,
        target_quarantine_prefix=operation.target_quarantine_prefix,
        audience=audience,
        executor_principal=executor_principal,
        fencing_token=operation.fencing_token,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    unsigned = _unsigned(provisional, signer.key_version)
    unsigned["environment_fingerprint"] = (
        operation.control_snapshot.environment_fingerprint.typed
    )
    canonical = canonicalize_json(unsigned)
    digest = hashlib.sha256(canonical).digest()
    ticket = ImportTicketRecord(
        **{
            **provisional.__dict__,
            "ticket_digest": TypedId.from_bare(digest.hex()),
        }
    )
    signature = signer.sign_sha256_digest(digest)
    return SignedImportTicket(
        ticket=ticket,
        environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        key_version=signer.key_version,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )


def verify_import_ticket(
    signed: SignedImportTicket,
    *,
    verifier: AttestationVerifier,
    at: str,
    expected_audience: str,
    expected_executor_principal: str,
    expected_environment_fingerprint: TypedId,
) -> None:
    if not isinstance(signed, SignedImportTicket):
        raise ImportTicketError("signed must be a SignedImportTicket")
    if signed.ticket.audience != expected_audience:
        raise ImportTicketError("import ticket audience mismatch")
    if signed.ticket.executor_principal != expected_executor_principal:
        raise ImportTicketError("import ticket executor principal mismatch")
    if signed.environment_fingerprint != expected_environment_fingerprint:
        raise ImportTicketError("import ticket environment mismatch")
    instant = _aware("at", at)
    if instant < _aware("issued_at", signed.ticket.issued_at):
        raise ImportTicketError("import ticket is not active yet")
    if instant >= _aware("expires_at", signed.ticket.expires_at):
        raise ImportTicketError("import ticket is expired")
    unsigned = _unsigned(signed.ticket, signed.key_version)
    unsigned["environment_fingerprint"] = signed.environment_fingerprint.typed
    digest = hashlib.sha256(canonicalize_json(unsigned)).digest()
    try:
        signature = base64.b64decode(signed.signature_b64, validate=True)
        verifier.verify_sha256_digest(
            key_version=signed.key_version,
            digest=digest,
            signature=signature,
        )
    except Exception as exc:
        raise ImportTicketError("import ticket signature verification failed") from exc

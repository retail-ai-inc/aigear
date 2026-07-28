"""Append-only, signed security journal backed by generation-CAS objects."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from aigear.management.v2.attestation import (
    AttestationVerifier,
    DigestSigner,
)
from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.fake_gcs import GenerationPreconditionError
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment

__all__ = [
    "SecurityJournalError",
    "SecurityJournalConflictError",
    "JournalEntryPhase",
    "SecurityJournalEntry",
    "SecurityJournalHead",
    "SecurityJournal",
]


class SecurityJournalError(ValueError):
    pass


class SecurityJournalConflictError(SecurityJournalError):
    pass


class JournalEntryPhase(str, Enum):
    PREPARED = "prepared"
    COMMITTED = "committed"


def _digest(value: dict) -> tuple[TypedId, bytes]:
    canonical = canonicalize_json(value)
    raw = hashlib.sha256(canonical).digest()
    return TypedId.from_bare(raw.hex()), raw


def _verify_signature(
    *, verifier: AttestationVerifier, key_version: str, unsigned: dict, signature_b64: str
) -> None:
    _, digest = _digest(unsigned)
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (TypeError, ValueError) as exc:
        raise SecurityJournalError("journal signature must be canonical base64") from exc
    try:
        verifier.verify_sha256_digest(
            key_version=key_version,
            digest=digest,
            signature=signature,
        )
    except Exception as exc:
        raise SecurityJournalError("journal signature verification failed") from exc


@dataclass(frozen=True)
class SecurityJournalEntry:
    entry_id: TypedId
    unsigned: dict
    key_version: str
    signature_b64: str
    object_name: str
    generation: str

    @property
    def sequence(self) -> int:
        return self.unsigned["sequence"]

    @property
    def previous_entry_id(self) -> Optional[TypedId]:
        value = self.unsigned["previous_entry_id"]
        return None if value is None else TypedId.from_typed(value)


@dataclass(frozen=True)
class SecurityJournalHead:
    head_id: TypedId
    unsigned: dict
    key_version: str
    signature_b64: str
    generation: str

    @property
    def sequence(self) -> int:
        return self.unsigned["sequence"]

    @property
    def entry_id(self) -> TypedId:
        return TypedId.from_typed(self.unsigned["entry_id"])

    @property
    def entry_object_name(self) -> str:
        return self.unsigned["entry_object_name"]

    @property
    def entry_generation(self) -> str:
        return self.unsigned["entry_generation"]


class SecurityJournal:
    """One journal in a dedicated locked bucket.

    Callers provide the expected previous sequence and entry identity. This
    makes retry and concurrency explicit instead of silently appending after
    an unexpected security fact.
    """

    def __init__(
        self,
        *,
        gcs: GcsClientV2,
        object_prefix: str,
        environment_fingerprint: TypedId,
        signer: DigestSigner,
        verifier: AttestationVerifier,
    ) -> None:
        if not object_prefix or object_prefix.startswith("/") or object_prefix.endswith("/"):
            raise SecurityJournalError(
                "object_prefix must be a non-empty relative prefix without trailing '/'"
            )
        if not isinstance(environment_fingerprint, TypedId):
            raise SecurityJournalError("environment_fingerprint must be a TypedId")
        self._gcs = gcs
        self._prefix = object_prefix
        self._environment_fingerprint = environment_fingerprint
        self._signer = signer
        self._verifier = verifier

    @property
    def head_object_name(self) -> str:
        return f"{self._prefix}/head.json"

    @property
    def object_prefix(self) -> str:
        return self._prefix

    def read_head(self) -> Optional[SecurityJournalHead]:
        snapshot = self._gcs.get_live_object(self.head_object_name)
        if snapshot is None:
            return None
        head = self._decode_head(snapshot.data, generation=snapshot.generation)
        entry_snapshot = self._gcs.get_object(
            head.entry_object_name, generation=head.entry_generation
        )
        entry = self._decode_entry(
            entry_snapshot.data,
            object_name=entry_snapshot.object_name,
            generation=entry_snapshot.generation,
        )
        if entry.entry_id != head.entry_id or entry.sequence != head.sequence:
            raise SecurityJournalError("journal head does not match its exact entry")
        return head

    def append(
        self,
        *,
        operation_id: str,
        event_kind: str,
        phase: JournalEntryPhase,
        subject_id: str,
        evidence_digest: TypedId,
        issued_at: str,
        expected_previous_sequence: int,
        expected_previous_entry_id: Optional[TypedId],
    ) -> SecurityJournalEntry:
        operation_id = validate_segment(operation_id, field_name="operation_id")
        event_kind = validate_segment(event_kind, field_name="event_kind")
        if not isinstance(phase, JournalEntryPhase):
            raise SecurityJournalError("phase must be a JournalEntryPhase")
        if not subject_id:
            raise SecurityJournalError("subject_id must be non-empty")
        if not isinstance(evidence_digest, TypedId):
            raise SecurityJournalError("evidence_digest must be a TypedId")
        if (
            isinstance(expected_previous_sequence, bool)
            or not isinstance(expected_previous_sequence, int)
            or expected_previous_sequence < 0
        ):
            raise SecurityJournalError("expected_previous_sequence must be non-negative")
        if expected_previous_sequence == 0:
            if expected_previous_entry_id is not None:
                raise SecurityJournalError("sequence zero cannot have a previous entry")
        elif not isinstance(expected_previous_entry_id, TypedId):
            raise SecurityJournalError("non-zero sequence requires expected previous entry")

        current = self.read_head()
        candidate = self._sign_entry(
            operation_id=operation_id,
            event_kind=event_kind,
            phase=phase,
            subject_id=subject_id,
            evidence_digest=evidence_digest,
            issued_at=issued_at,
            sequence=expected_previous_sequence + 1,
            previous_entry_id=expected_previous_entry_id,
        )
        if current is not None and current.sequence == candidate.sequence:
            if current.entry_id == candidate.entry_id:
                snapshot = self._gcs.get_object(
                    current.entry_object_name, generation=current.entry_generation
                )
                return self._decode_entry(
                    snapshot.data,
                    object_name=snapshot.object_name,
                    generation=snapshot.generation,
                )
            raise SecurityJournalConflictError(
                "journal sequence is already committed to different evidence"
            )
        current_sequence = 0 if current is None else current.sequence
        current_entry_id = None if current is None else current.entry_id
        if (
            current_sequence != expected_previous_sequence
            or current_entry_id != expected_previous_entry_id
        ):
            raise SecurityJournalConflictError("journal head changed before append")

        entry_name = (
            f"{self._prefix}/entries/{candidate.sequence:020d}-{candidate.entry_id.bare}.json"
        )
        entry_snapshot = self._gcs.put_object(
            entry_name,
            canonicalize_json(self._entry_document(candidate)),
            if_generation_match=0,
        )
        committed_entry = SecurityJournalEntry(
            entry_id=candidate.entry_id,
            unsigned=candidate.unsigned,
            key_version=candidate.key_version,
            signature_b64=candidate.signature_b64,
            object_name=entry_name,
            generation=entry_snapshot.generation,
        )
        head = self._sign_head(committed_entry, previous_head=current)
        expected_head_generation = 0 if current is None else int(current.generation)
        try:
            self._gcs.put_object(
                self.head_object_name,
                canonicalize_json(self._head_document(head)),
                if_generation_match=expected_head_generation,
            )
        except GenerationPreconditionError as exc:
            raise SecurityJournalConflictError("journal head CAS failed") from exc
        return committed_entry

    def _sign_entry(
        self,
        *,
        operation_id: str,
        event_kind: str,
        phase: JournalEntryPhase,
        subject_id: str,
        evidence_digest: TypedId,
        issued_at: str,
        sequence: int,
        previous_entry_id: Optional[TypedId],
    ) -> SecurityJournalEntry:
        unsigned = {
            "domain": "aigear.security-journal-entry.v2",
            "schema_version": "2.0",
            "environment_fingerprint": self._environment_fingerprint.typed,
            "sequence": sequence,
            "previous_entry_id": (
                None if previous_entry_id is None else previous_entry_id.typed
            ),
            "operation_id": operation_id,
            "event_kind": event_kind,
            "phase": phase.value,
            "subject_id": subject_id,
            "evidence_digest": evidence_digest.typed,
            "issued_at": issued_at,
            "key_version": self._signer.key_version,
        }
        entry_id, raw_digest = _digest(unsigned)
        signature = self._signer.sign_sha256_digest(raw_digest)
        return SecurityJournalEntry(
            entry_id=entry_id,
            unsigned=unsigned,
            key_version=self._signer.key_version,
            signature_b64=base64.b64encode(signature).decode("ascii"),
            object_name="",
            generation="",
        )

    def _sign_head(
        self,
        entry: SecurityJournalEntry,
        *,
        previous_head: Optional[SecurityJournalHead],
    ) -> SecurityJournalHead:
        unsigned = {
            "domain": "aigear.security-journal-head.v2",
            "schema_version": "2.0",
            "environment_fingerprint": self._environment_fingerprint.typed,
            "sequence": entry.sequence,
            "entry_id": entry.entry_id.typed,
            "entry_object_name": entry.object_name,
            "entry_generation": entry.generation,
            "previous_head_id": (
                None if previous_head is None else previous_head.head_id.typed
            ),
            "key_version": self._signer.key_version,
        }
        head_id, raw_digest = _digest(unsigned)
        signature = self._signer.sign_sha256_digest(raw_digest)
        return SecurityJournalHead(
            head_id=head_id,
            unsigned=unsigned,
            key_version=self._signer.key_version,
            signature_b64=base64.b64encode(signature).decode("ascii"),
            generation="",
        )

    @staticmethod
    def _entry_document(entry: SecurityJournalEntry) -> dict:
        return {
            "entry_id": entry.entry_id.typed,
            "unsigned": entry.unsigned,
            "key_version": entry.key_version,
            "signature_b64": entry.signature_b64,
        }

    @staticmethod
    def _head_document(head: SecurityJournalHead) -> dict:
        return {
            "head_id": head.head_id.typed,
            "unsigned": head.unsigned,
            "key_version": head.key_version,
            "signature_b64": head.signature_b64,
        }

    def _decode_entry(
        self, data: bytes, *, object_name: str, generation: str
    ) -> SecurityJournalEntry:
        document = self._load_document(data, {"entry_id", "unsigned", "key_version", "signature_b64"})
        unsigned = document["unsigned"]
        self._validate_unsigned(unsigned, domain="aigear.security-journal-entry.v2")
        entry_id, _ = _digest(unsigned)
        if TypedId.from_typed(document["entry_id"]) != entry_id:
            raise SecurityJournalError("journal entry_id does not match unsigned content")
        _verify_signature(
            verifier=self._verifier,
            key_version=document["key_version"],
            unsigned=unsigned,
            signature_b64=document["signature_b64"],
        )
        return SecurityJournalEntry(
            entry_id=entry_id,
            unsigned=unsigned,
            key_version=document["key_version"],
            signature_b64=document["signature_b64"],
            object_name=object_name,
            generation=generation,
        )

    def _decode_head(self, data: bytes, *, generation: str) -> SecurityJournalHead:
        document = self._load_document(data, {"head_id", "unsigned", "key_version", "signature_b64"})
        unsigned = document["unsigned"]
        self._validate_unsigned(unsigned, domain="aigear.security-journal-head.v2")
        head_id, _ = _digest(unsigned)
        if TypedId.from_typed(document["head_id"]) != head_id:
            raise SecurityJournalError("journal head_id does not match unsigned content")
        _verify_signature(
            verifier=self._verifier,
            key_version=document["key_version"],
            unsigned=unsigned,
            signature_b64=document["signature_b64"],
        )
        return SecurityJournalHead(
            head_id=head_id,
            unsigned=unsigned,
            key_version=document["key_version"],
            signature_b64=document["signature_b64"],
            generation=generation,
        )

    @staticmethod
    def _load_document(data: bytes, expected_fields: set[str]) -> dict:
        try:
            document = json.loads(data)
        except (TypeError, ValueError, UnicodeDecodeError) as exc:
            raise SecurityJournalError("journal object is not valid JSON") from exc
        if not isinstance(document, dict) or set(document) != expected_fields:
            raise SecurityJournalError("journal document fields do not match schema")
        if not isinstance(document.get("unsigned"), dict):
            raise SecurityJournalError("journal unsigned content must be an object")
        return document

    def _validate_unsigned(self, unsigned: dict, *, domain: str) -> None:
        if unsigned.get("domain") != domain:
            raise SecurityJournalError("journal domain mismatch")
        if unsigned.get("schema_version") != "2.0":
            raise SecurityJournalError("journal schema_version mismatch")
        if (
            unsigned.get("environment_fingerprint")
            != self._environment_fingerprint.typed
        ):
            raise SecurityJournalError("journal environment fingerprint mismatch")

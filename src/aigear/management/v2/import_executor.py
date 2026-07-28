"""Exact-generation copy adapter for external imports.

The ticket source is a small JSON manifest.  Its exact GCS generation is the
authorization boundary; every payload listed by that immutable manifest is
read at an exact generation and written create-only below the operation's
quarantine prefix.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Tuple

from aigear.management.v2.attestation import AttestationVerifier
from aigear.management.v2.canonical import canonicalize_json
from aigear.management.v2.fake_gcs import GenerationPreconditionError, GcsObjectSnapshot
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_ticket import SignedImportTicket, verify_import_ticket
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportCompletionRecord,
)

__all__ = [
    "DEFAULT_MAX_IMPORT_BYTES",
    "DEFAULT_MAX_IMPORT_PAYLOADS",
    "ImportExecutionError",
    "PartialImportCopyError",
    "ImportPayloadSource",
    "ImportSourceManifest",
    "QuarantineObjectDescriptor",
    "ImportExecutorCompletion",
    "compute_import_payload_set_digest",
    "parse_import_source_manifest",
    "execute_import_to_quarantine",
]

DEFAULT_MAX_IMPORT_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_MAX_IMPORT_PAYLOADS = 100
_MANIFEST_KEYS = frozenset({"schema_version", "declaration", "payloads"})
_PAYLOAD_KEYS = frozenset(
    {
        "payload_kind",
        "payload_key",
        "file_name",
        "media_type",
        "object_name",
        "generation",
        "size_bytes",
    }
)


class ImportExecutionError(ValueError):
    """The import request is unsafe, inconsistent, or cannot be completed."""


class PartialImportCopyError(ImportExecutionError):
    """Some create-only writes succeeded, but no completion was produced."""

    def __init__(
        self, message: str, *, copied: Tuple["QuarantineObjectDescriptor", ...]
    ) -> None:
        super().__init__(message)
        self.copied = copied


def _aware(field_name: str, value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImportExecutionError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportExecutionError(f"{field_name} must be timezone-aware")


def _non_empty(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ImportExecutionError(f"{field_name} must be a non-empty str")
    return value


def _positive_int(field_name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ImportExecutionError(f"{field_name} must be a positive int")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImportExecutionError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


@dataclass(frozen=True)
class ImportPayloadSource:
    payload_kind: str
    payload_key: str
    file_name: str
    media_type: str
    source: ExactImportSource

    def __post_init__(self) -> None:
        if self.payload_kind not in ("components", "attachments"):
            raise ImportExecutionError(
                "payload_kind must be 'components' or 'attachments'"
            )
        for field_name in ("payload_key", "file_name", "media_type"):
            _non_empty(field_name, getattr(self, field_name))
        if not isinstance(self.source, ExactImportSource):
            raise ImportExecutionError("source must be an ExactImportSource")

    @property
    def logical_path(self) -> tuple[str, str]:
        return self.payload_kind, self.payload_key


@dataclass(frozen=True)
class ImportSourceManifest:
    declaration: Mapping[str, object]
    payloads: Tuple[ImportPayloadSource, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.declaration, dict) or not self.declaration:
            raise ImportExecutionError("source manifest declaration must be an object")
        try:
            canonicalize_json(self.declaration)
        except Exception as exc:
            raise ImportExecutionError(
                "source manifest declaration is not canonicalizable"
            ) from exc
        if not self.payloads:
            raise ImportExecutionError("source manifest must contain payloads")


@dataclass(frozen=True)
class QuarantineObjectDescriptor:
    payload_kind: str
    payload_key: str
    file_name: str
    media_type: str
    source_object_name: str
    source_generation: str
    object_name: str
    generation: str
    sha256: str
    crc32c: str
    size_bytes: int

    def canonical_dict(self) -> dict:
        return {
            "payload_kind": self.payload_kind,
            "payload_key": self.payload_key,
            "file_name": self.file_name,
            "media_type": self.media_type,
            "source_object_name": self.source_object_name,
            "source_generation": self.source_generation,
            "object_name": self.object_name,
            "generation": self.generation,
            "sha256": self.sha256,
            "crc32c": self.crc32c,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class ImportExecutorCompletion:
    operation_id: str
    ticket_digest: TypedId
    environment_fingerprint: TypedId
    executor_principal: str
    fencing_token: int
    source_manifest: QuarantineObjectDescriptor
    payloads: Tuple[QuarantineObjectDescriptor, ...]
    payload_set_digest: TypedId
    completed_at: str

    def __post_init__(self) -> None:
        _aware("completed_at", self.completed_at)
        if not self.payloads:
            raise ImportExecutionError("completion must contain at least one payload")
        expected = compute_import_payload_set_digest(
            self.source_manifest, self.payloads
        )
        if self.payload_set_digest != expected:
            raise ImportExecutionError("payload_set_digest does not match descriptors")

    def to_record(self) -> ImportCompletionRecord:
        return ImportCompletionRecord(
            schema_version="2.0",
            operation_id=self.operation_id,
            ticket_digest=self.ticket_digest,
            environment_fingerprint=self.environment_fingerprint,
            executor_principal=self.executor_principal,
            fencing_token=self.fencing_token,
            payload_set_digest=self.payload_set_digest,
            completed_at=self.completed_at,
        )


def parse_import_source_manifest(
    data: bytes, *, ticket_source: ExactImportSource
) -> ImportSourceManifest:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except ImportExecutionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportExecutionError("source manifest must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or frozenset(value) != _MANIFEST_KEYS:
        raise ImportExecutionError("source manifest has unknown or missing fields")
    if value["schema_version"] != "2.0" or not isinstance(value["payloads"], list):
        raise ImportExecutionError("source manifest schema is unsupported")
    if not value["payloads"]:
        raise ImportExecutionError("source manifest must contain at least one payload")

    payloads = []
    logical_paths = set()
    for raw in value["payloads"]:
        if not isinstance(raw, dict) or frozenset(raw) != _PAYLOAD_KEYS:
            raise ImportExecutionError("payload has unknown or missing fields")
        source = ExactImportSource(
            environment_id=ticket_source.environment_id,
            project_id=ticket_source.project_id,
            bucket=ticket_source.bucket,
            object_name=_non_empty("object_name", raw["object_name"]),
            generation=_non_empty("generation", raw["generation"]),
            region=ticket_source.region,
            size_bytes=raw["size_bytes"],
        )
        payload = ImportPayloadSource(
            payload_kind=raw["payload_kind"],
            payload_key=raw["payload_key"],
            file_name=raw["file_name"],
            media_type=raw["media_type"],
            source=source,
        )
        if payload.logical_path in logical_paths:
            raise ImportExecutionError(
                f"duplicate logical payload path {payload.logical_path!r}"
            )
        logical_paths.add(payload.logical_path)
        payloads.append(payload)
    return ImportSourceManifest(
        declaration=value["declaration"],
        payloads=tuple(payloads),
    )


def _verify_snapshot(source: ExactImportSource, snapshot: GcsObjectSnapshot) -> None:
    digest = hashlib.sha256(snapshot.data).hexdigest()
    if (
        snapshot.object_name != source.object_name
        or snapshot.generation != source.generation
        or snapshot.size_bytes != source.size_bytes
        or len(snapshot.data) != source.size_bytes
        or snapshot.sha256 != digest
    ):
        raise ImportExecutionError(
            f"exact source metadata drifted for {source.object_name!r}"
        )


def _put_create_only(
    *,
    gcs: GcsClientV2,
    object_name: str,
    data: bytes,
    known_generation: str | None,
) -> GcsObjectSnapshot:
    live = gcs.get_live_object(object_name)
    if live is not None:
        if known_generation is None or live.generation != known_generation:
            raise ImportExecutionError(
                f"quarantine target already exists at an unknown generation: {object_name!r}"
            )
        if live.data != data or live.sha256 != hashlib.sha256(data).hexdigest():
            raise ImportExecutionError(
                f"known quarantine target content changed: {object_name!r}"
            )
        return live
    try:
        return gcs.put_object(object_name, data, if_generation_match=0)
    except GenerationPreconditionError as exc:
        raise ImportExecutionError(
            f"quarantine target was concurrently created: {object_name!r}"
        ) from exc


def _descriptor(
    *,
    payload: ImportPayloadSource,
    source: GcsObjectSnapshot,
    target: GcsObjectSnapshot,
) -> QuarantineObjectDescriptor:
    return QuarantineObjectDescriptor(
        payload_kind=payload.payload_kind,
        payload_key=payload.payload_key,
        file_name=payload.file_name,
        media_type=payload.media_type,
        source_object_name=source.object_name,
        source_generation=source.generation,
        object_name=target.object_name,
        generation=target.generation,
        sha256=target.sha256,
        crc32c=target.crc32c,
        size_bytes=target.size_bytes,
    )


def compute_import_payload_set_digest(
    source_manifest: QuarantineObjectDescriptor,
    payloads: Tuple[QuarantineObjectDescriptor, ...],
) -> TypedId:
    value = {
        "domain": "aigear.import-payload-set.v2",
        "source_manifest": source_manifest.canonical_dict(),
        "payloads": [item.canonical_dict() for item in payloads],
    }
    return TypedId.from_bare(hashlib.sha256(canonicalize_json(value)).hexdigest())


def execute_import_to_quarantine(
    signed_ticket: SignedImportTicket,
    *,
    source_gcs: GcsClientV2,
    quarantine_gcs: GcsClientV2,
    layout: GcsLayoutV2,
    verifier: AttestationVerifier,
    at: str,
    expected_audience: str,
    expected_executor_principal: str,
    expected_environment_fingerprint: TypedId,
    max_payloads: int = DEFAULT_MAX_IMPORT_PAYLOADS,
    max_total_size_bytes: int = DEFAULT_MAX_IMPORT_BYTES,
    known_quarantine_generations: Mapping[str, str] | None = None,
) -> ImportExecutorCompletion:
    """Copy one ticket-authorized source manifest and all of its payloads."""

    verify_import_ticket(
        signed_ticket,
        verifier=verifier,
        at=at,
        expected_audience=expected_audience,
        expected_executor_principal=expected_executor_principal,
        expected_environment_fingerprint=expected_environment_fingerprint,
    )
    _positive_int("max_payloads", max_payloads)
    _positive_int("max_total_size_bytes", max_total_size_bytes)
    known = dict(known_quarantine_generations or {})
    ticket = signed_ticket.ticket
    expected_prefix = (
        layout.quarantine_manifest(ticket.operation_id).removesuffix(
            "source-manifest.json"
        )
    )
    if ticket.target_quarantine_prefix != expected_prefix:
        raise ImportExecutionError("ticket quarantine prefix does not match target layout")

    manifest_source = source_gcs.get_object(
        ticket.source.object_name, generation=ticket.source.generation
    )
    _verify_snapshot(ticket.source, manifest_source)
    source_manifest = parse_import_source_manifest(
        manifest_source.data, ticket_source=ticket.source
    )
    payloads = source_manifest.payloads
    if len(payloads) > max_payloads:
        raise ImportExecutionError("source manifest exceeds payload count limit")
    total_size = sum(payload.source.size_bytes for payload in payloads)
    if total_size > max_total_size_bytes:
        raise ImportExecutionError("source manifest exceeds total byte limit")

    # Read and validate the entire immutable source set before the first write.
    sources = []
    targets = []
    for payload in payloads:
        source = source_gcs.get_object(
            payload.source.object_name, generation=payload.source.generation
        )
        _verify_snapshot(payload.source, source)
        sources.append(source)
        targets.append(
            layout.quarantine(
                import_operation_id=ticket.operation_id,
                payload_kind=payload.payload_kind,
                payload_key=payload.payload_key,
                file_name=payload.file_name,
            )
        )
    manifest_target = layout.quarantine_manifest(ticket.operation_id)
    for object_name, data in (
        (manifest_target, manifest_source.data),
        *((target, source.data) for target, source in zip(targets, sources)),
    ):
        live = quarantine_gcs.get_live_object(object_name)
        if live is not None and (
            known.get(object_name) != live.generation or live.data != data
        ):
            raise ImportExecutionError(
                f"quarantine target already exists at an unknown generation: {object_name!r}"
            )

    copied = []
    try:
        manifest_snapshot = _put_create_only(
            gcs=quarantine_gcs,
            object_name=manifest_target,
            data=manifest_source.data,
            known_generation=known.get(manifest_target),
        )
        manifest_descriptor = QuarantineObjectDescriptor(
            payload_kind="source_manifest",
            payload_key="source_manifest",
            file_name="source-manifest.json",
            media_type="application/json",
            source_object_name=manifest_source.object_name,
            source_generation=manifest_source.generation,
            object_name=manifest_snapshot.object_name,
            generation=manifest_snapshot.generation,
            sha256=manifest_snapshot.sha256,
            crc32c=manifest_snapshot.crc32c,
            size_bytes=manifest_snapshot.size_bytes,
        )
        copied.append(manifest_descriptor)
        payload_descriptors = []
        for payload, source, target_name in zip(payloads, sources, targets):
            target = _put_create_only(
                gcs=quarantine_gcs,
                object_name=target_name,
                data=source.data,
                known_generation=known.get(target_name),
            )
            descriptor = _descriptor(payload=payload, source=source, target=target)
            copied.append(descriptor)
            payload_descriptors.append(descriptor)
    except ImportExecutionError as exc:
        raise PartialImportCopyError(str(exc), copied=tuple(copied)) from exc

    descriptors = tuple(payload_descriptors)
    return ImportExecutorCompletion(
        operation_id=ticket.operation_id,
        ticket_digest=ticket.ticket_digest,
        environment_fingerprint=signed_ticket.environment_fingerprint,
        executor_principal=ticket.executor_principal,
        fencing_token=ticket.fencing_token,
        source_manifest=manifest_descriptor,
        payloads=descriptors,
        payload_set_digest=compute_import_payload_set_digest(
            manifest_descriptor, descriptors
        ),
        completed_at=at,
    )

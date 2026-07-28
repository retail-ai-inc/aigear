"""Safe submission primitives for Pipeline V2 asset imports.

This module deliberately stops at an auditable ``RESERVED`` operation.  The
import executor, content scanner, preparer and finalizer advance that operation
through the state machine; the public API never turns uploaded metadata into a
committed asset directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence, Tuple

from aigear.management.v2.canonical import canonicalize_json, digest_sha256_of_jcs
from aigear.management.v2.environment import EnvironmentIdentity
from aigear.management.v2.external_source import ExternalSourceError
from aigear.management.v2.fake_gcs import GenerationPreconditionError, GcsObjectSnapshot
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.import_prepare import parse_external_import_declaration
from aigear.management.v2.import_recovery import RecoveredImportResult
from aigear.management.v2.import_reservation import compute_import_idempotency_key_hash
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportOperationRecord,
)

__all__ = [
    "DEFAULT_MAX_LOCAL_IMPORT_BYTES",
    "DEFAULT_MAX_LOCAL_IMPORT_PAYLOADS",
    "ImportApiError",
    "LocalImportConflict",
    "ExternalObjectMetadata",
    "ExternalSourceInspector",
    "GoogleExternalSourceInspector",
    "LocalImportPayload",
    "StagedLocalImport",
    "ImportSubmission",
    "compute_import_operation_id",
    "stage_local_import_bundle",
]

DEFAULT_MAX_LOCAL_IMPORT_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_MAX_LOCAL_IMPORT_PAYLOADS = 100


class ImportApiError(ValueError):
    """An import submission is incomplete, unsafe, or exceeds configured limits."""


class LocalImportConflict(ImportApiError):
    """A create-only local quarantine path already contains different bytes."""


@dataclass(frozen=True)
class ExternalObjectMetadata:
    region: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_segment(self.region, field_name="external source region")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ImportApiError("external source size_bytes must be non-negative")


class ExternalSourceInspector(Protocol):
    """Trusted metadata lookup for one already syntax-checked exact GCS source."""

    def inspect(self, source: ExactImportSource) -> ExternalObjectMetadata: ...


class GoogleExternalSourceInspector:
    """Read exact object and bucket metadata through a Google Storage client."""

    def __init__(self, *, client=None) -> None:
        if client is None:
            try:
                from google.cloud import storage
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "GoogleExternalSourceInspector requires google-cloud-storage"
                ) from exc
            client = storage.Client()
        self.client = client

    def inspect(self, source: ExactImportSource) -> ExternalObjectMetadata:
        if not isinstance(source, ExactImportSource):
            raise ImportApiError("source must be an ExactImportSource")
        bucket = self.client.bucket(source.bucket)
        blob = bucket.blob(source.object_name, generation=int(source.generation))
        try:
            bucket.reload()
            blob.reload(if_generation_match=int(source.generation))
        except BaseException as exc:
            raise ImportApiError(
                "cannot inspect the exact external source generation"
            ) from exc
        if str(blob.generation) != source.generation:
            raise ImportApiError("external source generation changed during inspection")
        region = str(getattr(bucket, "location", "") or "").lower()
        size = getattr(blob, "size", None)
        if not region or isinstance(size, bool) or not isinstance(size, int):
            raise ImportApiError("external source metadata is incomplete")
        return ExternalObjectMetadata(region=region, size_bytes=size)


@dataclass(frozen=True)
class LocalImportPayload:
    path: Path
    payload_kind: str
    payload_key: str
    file_name: str
    media_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if self.payload_kind not in ("components", "attachments"):
            raise ImportApiError(
                "payload_kind must be 'components' or 'attachments'"
            )
        object.__setattr__(
            self,
            "payload_key",
            validate_segment(self.payload_key, field_name="payload_key"),
        )
        object.__setattr__(
            self,
            "file_name",
            validate_segment(self.file_name, field_name="file_name"),
        )
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ImportApiError("media_type must be a non-empty str")


@dataclass(frozen=True)
class StagedLocalImport:
    operation_id: str
    source: ExactImportSource
    target_quarantine_prefix: str


@dataclass(frozen=True)
class ImportSubmission:
    """Current authoritative operation plus a result only when it is committed."""

    operation: ImportOperationRecord
    result: RecoveredImportResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, ImportOperationRecord):
            raise ImportApiError("operation must be an ImportOperationRecord")
        if self.result is not None and self.result.operation != self.operation:
            raise ImportApiError("result is not bound to operation")


@dataclass(frozen=True)
class _LocalPayloadSnapshot:
    payload: LocalImportPayload
    sha256: str
    size_bytes: int


def compute_import_operation_id(
    environment_fingerprint: str, idempotency_key: str
) -> str:
    """Derive one stable, opaque quarantine scope from an idempotency key."""

    key_hash = compute_import_idempotency_key_hash(idempotency_key)
    digest = digest_sha256_of_jcs(
        ["aigear.import-operation.v2", environment_fingerprint, key_hash]
    )
    return f"import-{digest}"


def _snapshot_local_payload(value: LocalImportPayload) -> _LocalPayloadSnapshot:
    path = value.path
    if path.is_symlink() or not path.is_file():
        raise ImportApiError(f"local import payload must be a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise ImportApiError(f"cannot read local import payload: {path}") from exc
    return _LocalPayloadSnapshot(value, digest.hexdigest(), size)


def _validate_payload_declaration(
    declaration: Mapping[str, object],
    payloads: Tuple[LocalImportPayload, ...],
) -> None:
    parsed = parse_external_import_declaration(declaration)
    expected = {
        ("components", item.payload_key) for item in parsed.components
    } | {
        ("attachments", item.payload_key) for item in parsed.attachments
    }
    actual = {(item.payload_kind, item.payload_key) for item in payloads}
    if len(actual) != len(payloads):
        raise ImportApiError("local import payload identities must be unique")
    if actual != expected:
        raise ImportApiError(
            "local import payloads must exactly match declaration components/attachments"
        )


def _existing_or_raise(
    gcs: GcsClientV2,
    *,
    object_name: str,
    expected_sha256: str,
    expected_size_bytes: int,
) -> GcsObjectSnapshot:
    existing = gcs.get_live_object(object_name)
    if (
        existing is None
        or existing.sha256 != expected_sha256
        or existing.size_bytes != expected_size_bytes
    ):
        raise LocalImportConflict(
            f"quarantine object {object_name!r} already contains different bytes"
        )
    return existing


def _upload_create_or_validate(
    gcs: GcsClientV2,
    *,
    object_name: str,
    snapshot: _LocalPayloadSnapshot,
) -> GcsObjectSnapshot:
    try:
        return gcs.upload_file(
            object_name,
            snapshot.payload.path,
            if_generation_match=0,
            expected_sha256=snapshot.sha256,
        )
    except GenerationPreconditionError:
        return _existing_or_raise(
            gcs,
            object_name=object_name,
            expected_sha256=snapshot.sha256,
            expected_size_bytes=snapshot.size_bytes,
        )
    except ValueError as exc:
        raise ImportApiError("local payload changed while it was being uploaded") from exc


def _put_create_or_validate(
    gcs: GcsClientV2, *, object_name: str, data: bytes
) -> GcsObjectSnapshot:
    digest = hashlib.sha256(data).hexdigest()
    try:
        return gcs.put_object(object_name, data, if_generation_match=0)
    except GenerationPreconditionError:
        existing = _existing_or_raise(
            gcs,
            object_name=object_name,
            expected_sha256=digest,
            expected_size_bytes=len(data),
        )
        # A backend-provided digest is not enough for the small control manifest.
        exact = gcs.get_object(object_name, generation=existing.generation)
        if exact.data != data:
            raise LocalImportConflict(
                f"quarantine manifest {object_name!r} contains different bytes"
            )
        return exact


def stage_local_import_bundle(
    *,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    environment_identity: EnvironmentIdentity,
    environment_fingerprint: str,
    declaration: Mapping[str, object],
    payloads: Sequence[LocalImportPayload],
    idempotency_key: str,
    max_payloads: int = DEFAULT_MAX_LOCAL_IMPORT_PAYLOADS,
    max_total_size_bytes: int = DEFAULT_MAX_LOCAL_IMPORT_BYTES,
) -> StagedLocalImport:
    """Create or validate an operation-scoped, exact local source manifest."""

    if not isinstance(declaration, dict):
        raise ImportApiError("declaration must be a dict")
    values = tuple(payloads)
    if not values or not all(isinstance(item, LocalImportPayload) for item in values):
        raise ImportApiError("payloads must contain LocalImportPayload values")
    if (
        isinstance(max_payloads, bool)
        or not isinstance(max_payloads, int)
        or max_payloads < 1
        or len(values) > max_payloads
    ):
        raise ImportApiError("local import payload count exceeds configured limit")
    if (
        isinstance(max_total_size_bytes, bool)
        or not isinstance(max_total_size_bytes, int)
        or max_total_size_bytes < 1
    ):
        raise ImportApiError("max_total_size_bytes must be positive")
    _validate_payload_declaration(declaration, values)
    try:
        canonicalize_json(declaration)
    except Exception as exc:
        raise ImportApiError("declaration is not canonicalizable") from exc

    operation_id = compute_import_operation_id(
        environment_fingerprint, idempotency_key
    )
    target_prefix = layout.quarantine_manifest(operation_id).removesuffix(
        "source-manifest.json"
    )
    snapshots = tuple(
        sorted(
            (_snapshot_local_payload(item) for item in values),
            key=lambda item: (
                item.payload.payload_kind,
                item.payload.payload_key,
            ),
        )
    )
    total_size = sum(item.size_bytes for item in snapshots)
    if total_size > max_total_size_bytes:
        raise ImportApiError("local import bytes exceed configured limit")

    manifest_payloads = []
    for item in snapshots:
        payload = item.payload
        object_name = layout.quarantine(
            import_operation_id=operation_id,
            payload_kind=payload.payload_kind,
            payload_key=payload.payload_key,
            file_name=payload.file_name,
        )
        uploaded = _upload_create_or_validate(
            gcs, object_name=object_name, snapshot=item
        )
        manifest_payloads.append(
            {
                "payload_kind": payload.payload_kind,
                "payload_key": payload.payload_key,
                "file_name": payload.file_name,
                "media_type": payload.media_type,
                "object_name": uploaded.object_name,
                "generation": uploaded.generation,
                "size_bytes": uploaded.size_bytes,
            }
        )

    manifest_data = canonicalize_json(
        {
            "schema_version": "2.0",
            "declaration": declaration,
            "payloads": manifest_payloads,
        }
    )
    manifest = _put_create_or_validate(
        gcs,
        object_name=layout.quarantine_manifest(operation_id),
        data=manifest_data,
    )
    source = ExactImportSource(
        environment_id=environment_identity.environment_id,
        project_id=environment_identity.gcp_project_number,
        bucket=environment_identity.asset_bucket_name,
        object_name=manifest.object_name,
        generation=manifest.generation,
        region=environment_identity.asset_bucket_location,
        size_bytes=manifest.size_bytes,
    )
    return StagedLocalImport(operation_id, source, target_prefix)

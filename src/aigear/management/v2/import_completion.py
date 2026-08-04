"""Authenticate and bind import-executor completion events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Optional, Tuple

from aigear.management.v2.attestation import AttestationVerifier
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.import_executor import (
    ImportExecutorCompletion,
    ImportPayloadSource,
    QuarantineObjectDescriptor,
)
from aigear.management.v2.import_ticket import SignedImportTicket, verify_import_ticket
from aigear.management.v2.pubsub_auth import verify_pubsub_oidc_token
from aigear.management.v2.records.import_operation import (
    ImportCompletionRecord,
    ImportOperationRecord,
    ImportPhase,
)

__all__ = [
    "ImportCompletionError",
    "ExpectedImportPayload",
    "ImportCompletionEnvelope",
    "authenticate_import_completion",
]


class ImportCompletionError(ValueError):
    pass


def _parse_time(field_name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImportCompletionError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportCompletionError(f"{field_name} must be timezone-aware")
    return parsed


def _non_empty(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ImportCompletionError(f"{field_name} must be a non-empty str")


@dataclass(frozen=True)
class ExpectedImportPayload:
    payload_kind: str
    payload_key: str
    file_name: str
    media_type: str
    source_object_name: str
    source_generation: str
    source_size_bytes: int

    @classmethod
    def from_source(cls, value: ImportPayloadSource) -> "ExpectedImportPayload":
        if not isinstance(value, ImportPayloadSource):
            raise ImportCompletionError("value must be an ImportPayloadSource")
        return cls(
            payload_kind=value.payload_kind,
            payload_key=value.payload_key,
            file_name=value.file_name,
            media_type=value.media_type,
            source_object_name=value.source.object_name,
            source_generation=value.source.generation,
            source_size_bytes=value.source.size_bytes,
        )

    def __post_init__(self) -> None:
        if self.payload_kind not in ("components", "attachments"):
            raise ImportCompletionError("unsupported expected payload_kind")
        for field_name in (
            "payload_key",
            "file_name",
            "media_type",
            "source_object_name",
            "source_generation",
        ):
            _non_empty(field_name, getattr(self, field_name))
        if (
            isinstance(self.source_size_bytes, bool)
            or not isinstance(self.source_size_bytes, int)
            or self.source_size_bytes < 0
        ):
            raise ImportCompletionError("source_size_bytes must be a non-negative int")

    @property
    def logical_path(self) -> tuple[str, str]:
        return self.payload_kind, self.payload_key


@dataclass(frozen=True)
class ImportCompletionEnvelope:
    completion: ImportExecutorCompletion
    message_id: str
    audience: str
    publisher_principal: str
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.completion, ImportExecutorCompletion):
            raise ImportCompletionError(
                "completion must be an ImportExecutorCompletion"
            )
        for field_name in ("message_id", "audience", "publisher_principal"):
            _non_empty(field_name, getattr(self, field_name))
        issued = _parse_time("issued_at", self.issued_at)
        expires = _parse_time("expires_at", self.expires_at)
        if expires <= issued:
            raise ImportCompletionError("expires_at must be later than issued_at")


TokenVerifier = Callable[..., str]


def _validate_manifest_descriptor(
    descriptor: QuarantineObjectDescriptor,
    *,
    operation: ImportOperationRecord,
    layout: GcsLayoutV2,
) -> None:
    ticket = operation.ticket
    assert ticket is not None
    if (
        descriptor.payload_kind != "source_manifest"
        or descriptor.payload_key != "source_manifest"
        or descriptor.file_name != "source-manifest.json"
        or descriptor.media_type != "application/json"
        or descriptor.source_object_name != ticket.source.object_name
        or descriptor.source_generation != ticket.source.generation
        or descriptor.size_bytes != ticket.source.size_bytes
        or descriptor.object_name
        != layout.quarantine_manifest(operation.operation_id)
    ):
        raise ImportCompletionError(
            "completion source manifest does not match the ticket"
        )


def _validate_payload_inventory(
    completion: ImportExecutorCompletion,
    *,
    expected_payloads: Tuple[ExpectedImportPayload, ...],
    operation: ImportOperationRecord,
    layout: GcsLayoutV2,
) -> None:
    expected_by_path = {}
    for expected in expected_payloads:
        if not isinstance(expected, ExpectedImportPayload):
            raise ImportCompletionError(
                "expected_payloads must contain ExpectedImportPayload values"
            )
        if expected.logical_path in expected_by_path:
            raise ImportCompletionError("expected payload inventory contains duplicates")
        expected_by_path[expected.logical_path] = expected
    if not expected_by_path:
        raise ImportCompletionError("expected payload inventory must not be empty")

    actual_by_path = {}
    for descriptor in completion.payloads:
        path = descriptor.payload_kind, descriptor.payload_key
        if path in actual_by_path:
            raise ImportCompletionError("completion contains duplicate payloads")
        actual_by_path[path] = descriptor

    missing = expected_by_path.keys() - actual_by_path.keys()
    unknown = actual_by_path.keys() - expected_by_path.keys()
    if missing:
        raise ImportCompletionError(f"completion is missing payloads: {sorted(missing)!r}")
    if unknown:
        raise ImportCompletionError(
            f"completion contains unknown payloads: {sorted(unknown)!r}"
        )

    for path, expected in expected_by_path.items():
        actual = actual_by_path[path]
        expected_object_name = layout.quarantine(
            import_operation_id=operation.operation_id,
            payload_kind=expected.payload_kind,
            payload_key=expected.payload_key,
            file_name=expected.file_name,
        )
        if (
            actual.file_name != expected.file_name
            or actual.media_type != expected.media_type
            or actual.source_object_name != expected.source_object_name
            or actual.source_generation != expected.source_generation
            or actual.size_bytes != expected.source_size_bytes
            or actual.object_name != expected_object_name
        ):
            raise ImportCompletionError(
                f"completion payload descriptor does not match inventory: {path!r}"
            )


def authenticate_import_completion(
    envelope: ImportCompletionEnvelope,
    *,
    signed_ticket: SignedImportTicket,
    operation: ImportOperationRecord,
    expected_payloads: Iterable[ExpectedImportPayload],
    layout: GcsLayoutV2,
    oidc_token: str,
    expected_audience: str,
    allowed_publishers: Iterable[str],
    ticket_verifier: AttestationVerifier,
    at: str,
    oidc_request: Optional[object] = None,
    token_verifier: TokenVerifier = verify_pubsub_oidc_token,
) -> ImportCompletionRecord:
    """Verify message identity, validity window, fence, and full inventory."""

    if not isinstance(envelope, ImportCompletionEnvelope):
        raise ImportCompletionError("envelope must be an ImportCompletionEnvelope")
    if not isinstance(operation, ImportOperationRecord):
        raise ImportCompletionError("operation must be an ImportOperationRecord")
    if operation.phase != ImportPhase.QUARANTINING or operation.ticket is None:
        raise ImportCompletionError(
            "completion requires a ticketed quarantining operation"
        )
    if envelope.audience != expected_audience:
        raise ImportCompletionError("completion audience mismatch")

    allowed = tuple(allowed_publishers)
    if not allowed:
        raise ImportCompletionError("allowed_publishers must not be empty")
    try:
        principal = token_verifier(
            oidc_token,
            audience=expected_audience,
            allowed_service_accounts=allowed,
            request=oidc_request,
        )
    except Exception as exc:
        raise ImportCompletionError("completion OIDC authentication failed") from exc
    if (
        principal != envelope.publisher_principal
        or principal != operation.ticket.executor_principal
        or principal not in allowed
    ):
        raise ImportCompletionError("completion publisher principal mismatch")

    instant = _parse_time("at", at)
    issued = _parse_time("envelope.issued_at", envelope.issued_at)
    expires = _parse_time("envelope.expires_at", envelope.expires_at)
    completed = _parse_time(
        "completion.completed_at", envelope.completion.completed_at
    )
    ticket_issued = _parse_time("ticket.issued_at", operation.ticket.issued_at)
    ticket_expires = _parse_time("ticket.expires_at", operation.ticket.expires_at)
    if not (ticket_issued <= completed <= issued < expires <= ticket_expires):
        raise ImportCompletionError(
            "completion event window is outside the signed ticket window"
        )
    if instant < issued or instant >= expires:
        raise ImportCompletionError("completion event is not currently valid")

    if signed_ticket.ticket != operation.ticket:
        raise ImportCompletionError("signed ticket does not match the operation")
    try:
        verify_import_ticket(
            signed_ticket,
            verifier=ticket_verifier,
            at=envelope.completion.completed_at,
            expected_audience=operation.ticket.audience,
            expected_executor_principal=operation.ticket.executor_principal,
            expected_environment_fingerprint=operation.control_snapshot.environment_fingerprint,
        )
    except Exception as exc:
        raise ImportCompletionError("signed ticket verification failed") from exc

    completion = envelope.completion
    if (
        completion.operation_id != operation.operation_id
        or completion.ticket_digest != operation.ticket.ticket_digest
        or completion.environment_fingerprint
        != operation.control_snapshot.environment_fingerprint
        or completion.executor_principal != operation.ticket.executor_principal
        or completion.fencing_token != operation.fencing_token
    ):
        raise ImportCompletionError(
            "completion is not bound to the current operation fence"
        )
    _validate_manifest_descriptor(
        completion.source_manifest, operation=operation, layout=layout
    )
    _validate_payload_inventory(
        completion,
        expected_payloads=tuple(expected_payloads),
        operation=operation,
        layout=layout,
    )
    return completion.to_record()

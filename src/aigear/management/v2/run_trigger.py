"""Idempotent Run triggering (spec section 10.1).

``run_idempotency_key`` is the stable key an automated trigger (e.g. Cloud
Scheduler) must attach to every retry so that a redelivered trigger message
can never create a second Run. Per spec 10.1/10.2, the actual bookkeeping is
just an :class:`~aigear.management.v2.records.operation.OperationRecord`
keyed by that digest: same key + same ``request_fingerprint`` returns the
already-reserved operation; same key + a different fingerprint is a hard
conflict.

:func:`begin_run_trigger` takes an ``OperationStore`` (a minimal
get/put Protocol, not a hard dependency on
:class:`~aigear.management.v2.fake_registry.FakeRegistryV2`) so this module
has no forward dependency on the registry's later Operation CRUD support;
``FakeRegistryV2`` is made to satisfy this Protocol structurally once it
grows ``get_operation``/``put_operation``.

Deliberate scope note: a ``run_trigger`` operation never stages or uploads
any file, so the generic ``reserved -> staging -> uploaded -> finalizing ->
succeeded`` phase chain (spec 10.2, written for the shared Operation type in
general) does not really describe it. Spec 10.1/10.2 do not carve out a
shorter path for this operation type, so this module makes an explicit
choice: a successfully reserved run-trigger operation is left in
``OperationPhase.RESERVED`` and that *is* its terminal, successful state for
this operation type -- it is not forced through the file-upload-oriented
phases that do not apply to it. Revisit this if a later phase gives run
triggering its own phase vocabulary.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.operation import OperationPhase, OperationRecord

__all__ = [
    "RunTriggerIdempotencyConflict",
    "OperationStore",
    "compute_run_idempotency_key",
    "begin_run_trigger",
]


class RunTriggerIdempotencyConflict(ValueError):
    """Raised when a ``run_idempotency_key`` is replayed with a different
    ``request_fingerprint`` (spec 10.1: "相同 key、不同 fingerprint：IdempotencyConflict")."""


class OperationStore(Protocol):
    def get_operation(self, idempotency_key_hash: str) -> Optional[OperationRecord]: ...

    def put_operation(self, record: OperationRecord) -> OperationRecord: ...


def compute_run_idempotency_key(
    pipeline_identity: str,
    scheduled_for: str,
    trigger_source: str,
    trigger_payload_digest: TypedId,
) -> TypedId:
    """``run_idempotency_key`` per spec 10.1's exact JCS payload."""
    payload = [
        "aigear.run-trigger.v2",
        pipeline_identity,
        scheduled_for,
        trigger_source,
        trigger_payload_digest.typed,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def begin_run_trigger(
    store: OperationStore,
    *,
    idempotency_key: TypedId,
    request_fingerprint: str,
    owner_principal: str,
    create_run: Callable[[], str],
) -> OperationRecord:
    """Reserve (or replay) an idempotent run trigger.

    ``create_run`` is called at most once per distinct ``idempotency_key`` --
    only when no operation is already reserved for it -- and must create the
    Run and return its ``run_id``. Replays with a matching
    ``request_fingerprint`` short-circuit before ``create_run`` is called, so
    a redelivered trigger message never creates a second Run.
    """
    idempotency_key_hash = idempotency_key.bare
    existing = store.get_operation(idempotency_key_hash)
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise RunTriggerIdempotencyConflict(
                f"run_idempotency_key {idempotency_key.typed!r} is already reserved with a "
                "different request_fingerprint; refusing to trigger a conflicting run"
            )
        return existing

    run_id = create_run()
    if not isinstance(run_id, str) or not run_id:
        raise TypeError(f"create_run() must return a non-empty str run_id, got {run_id!r}")

    operation = OperationRecord(
        idempotency_key_hash=idempotency_key_hash,
        request_fingerprint=request_fingerprint,
        operation_type="run_trigger",
        owner_principal=owner_principal,
        write_epoch=1,
        fencing_token=0,
        phase=OperationPhase.RESERVED,
        revision=1,
        run_id=run_id,
    )
    return store.put_operation(operation)

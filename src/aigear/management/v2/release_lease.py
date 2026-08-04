"""Server-timed lease and fencing for one service release publisher."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.release import (
    ReleaseOperationRecord,
    ReleasePhase,
    ServiceReleaseState,
)

__all__ = [
    "DEFAULT_RELEASE_HEARTBEAT_SECONDS",
    "DEFAULT_RELEASE_LEASE_TTL_SECONDS",
    "ReleaseExternalState",
    "ReleaseLeaseError",
    "ReleaseLeaseBusy",
    "ReleaseLeaseConflict",
    "ReleaseLeaseFenced",
    "compute_release_idempotency_key_hash",
    "compute_release_request_fingerprint",
    "acquire_release_lease",
    "takeover_release_lease",
    "heartbeat_release_lease",
    "require_release_lease",
]


DEFAULT_RELEASE_LEASE_TTL_SECONDS = 120
DEFAULT_RELEASE_HEARTBEAT_SECONDS = 30
_MAX_RELEASE_LEASE_TTL_SECONDS = 10 * 60


class ReleaseLeaseError(ValueError):
    pass


class ReleaseLeaseBusy(ReleaseLeaseError):
    pass


class ReleaseLeaseConflict(ReleaseLeaseError):
    pass


class ReleaseLeaseFenced(ReleaseLeaseError):
    pass


@dataclass(frozen=True)
class ReleaseExternalState:
    """Exact Kubernetes identities observed before taking over an expired lease."""

    deployment_uid: Optional[str] = None
    deployment_resource_version: Optional[str] = None
    service_resource_version: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in (
            "deployment_uid",
            "deployment_resource_version",
            "service_resource_version",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ReleaseLeaseError(f"{field_name} must be a non-empty str")
        deployment = (self.deployment_uid, self.deployment_resource_version)
        if any(value is None for value in deployment) and any(
            value is not None for value in deployment
        ):
            raise ReleaseLeaseError(
                "deployment UID and resourceVersion must be observed together"
            )


def _server_time(registry) -> datetime:
    value = registry.get_server_read_time()
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ReleaseLeaseError("Registry server read time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _lease_expiry(operation: ReleaseOperationRecord) -> datetime:
    if operation.lease_expires_at is None:
        raise ReleaseLeaseFenced("release operation has no active lease")
    return datetime.fromisoformat(operation.lease_expires_at).astimezone(timezone.utc)


def _validate_ttl(lease_ttl_seconds: int) -> None:
    if (
        isinstance(lease_ttl_seconds, bool)
        or not isinstance(lease_ttl_seconds, int)
        or not DEFAULT_RELEASE_HEARTBEAT_SECONDS < lease_ttl_seconds
        <= _MAX_RELEASE_LEASE_TTL_SECONDS
    ):
        raise ReleaseLeaseError(
            "lease_ttl_seconds must exceed the heartbeat interval and be at most 600"
        )


def compute_release_idempotency_key_hash(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ReleaseLeaseError("idempotency_key must be a non-empty str")
    return digest_sha256_of_jcs(["aigear.release-idempotency.v2", idempotency_key])


def compute_release_request_fingerprint(
    *,
    environment_fingerprint: TypedId,
    service_name: str,
    target_release_id: TypedId,
    owner_principal: str,
) -> TypedId:
    if not isinstance(environment_fingerprint, TypedId):
        raise ReleaseLeaseError("environment_fingerprint must be a TypedId")
    service_name = validate_segment(service_name, field_name="service_name")
    if not isinstance(target_release_id, TypedId):
        raise ReleaseLeaseError("target_release_id must be a TypedId")
    if not isinstance(owner_principal, str) or not owner_principal:
        raise ReleaseLeaseError("owner_principal must be a non-empty str")
    return TypedId.from_bare(
        digest_sha256_of_jcs(
            {
                "domain": "aigear.release-request.v2",
                "environment_fingerprint": environment_fingerprint.typed,
                "service_name": service_name,
                "target_release_id": target_release_id.typed,
                "owner_principal": owner_principal,
            }
        )
    )


def _operation_id(environment_fingerprint: TypedId, key_hash: str) -> str:
    digest = digest_sha256_of_jcs(
        ["aigear.release-operation.v2", environment_fingerprint.typed, key_hash]
    )
    return f"release-{digest}"


def _takeover_observation(
    registry,
    *,
    service_name: str,
    operation_id: str,
    read_external_state: Optional[Callable[[str], ReleaseExternalState]],
) -> tuple[Optional[ReleaseExternalState], Optional[tuple[int, str, int]]]:
    state = registry.get_service_release_state(service_name)
    if state is None or state.active_operation_id is None:
        return None, None
    active = registry.get_release_operation(state.active_operation_id)
    if active is None:
        raise ReleaseLeaseConflict("active release operation is missing")
    if active.finished_at is not None and active.operation_id == operation_id:
        return None, None
    if active.finished_at is not None:
        if read_external_state is None:
            raise ReleaseLeaseConflict(
                "successor release requires an external state read"
            )
        observation = read_external_state(service_name)
        if not isinstance(observation, ReleaseExternalState):
            raise ReleaseLeaseError(
                "external state reader must return ReleaseExternalState"
            )
        return observation, (state.revision, active.operation_id, active.fencing_token)
    if _lease_expiry(active) > _server_time(registry):
        if active.operation_id != operation_id:
            raise ReleaseLeaseBusy("service already has a live release lease")
        return None, None
    if read_external_state is None:
        raise ReleaseLeaseConflict(
            "expired lease takeover requires an external state read"
        )
    observation = read_external_state(service_name)
    if not isinstance(observation, ReleaseExternalState):
        raise ReleaseLeaseError(
            "external state reader must return ReleaseExternalState"
        )
    return observation, (state.revision, active.operation_id, active.fencing_token)


def acquire_release_lease(
    registry,
    *,
    environment_fingerprint: TypedId,
    service_name: str,
    target_release_id: TypedId,
    idempotency_key: str,
    owner_principal: str,
    read_external_state: Optional[Callable[[str], ReleaseExternalState]] = None,
    lease_ttl_seconds: int = DEFAULT_RELEASE_LEASE_TTL_SECONDS,
) -> ReleaseOperationRecord:
    """Acquire a service lease; expired takeover observes Kubernetes before CAS."""

    _validate_ttl(lease_ttl_seconds)
    key_hash = compute_release_idempotency_key_hash(idempotency_key)
    fingerprint = compute_release_request_fingerprint(
        environment_fingerprint=environment_fingerprint,
        service_name=service_name,
        target_release_id=target_release_id,
        owner_principal=owner_principal,
    )
    operation_id = _operation_id(environment_fingerprint, key_hash)
    observation, takeover_identity = _takeover_observation(
        registry,
        service_name=service_name,
        operation_id=operation_id,
        read_external_state=read_external_state,
    )
    runner = getattr(registry, "run_atomic", None)
    if not callable(runner):
        raise ReleaseLeaseError("Registry lacks the required atomic transaction boundary")

    def acquire(tx):
        server_time = _server_time(tx)
        state = tx.get_service_release_state(service_name)
        existing = tx.get_release_operation(operation_id)
        if existing is not None and existing.request_fingerprint != fingerprint:
            raise ReleaseLeaseConflict(
                "idempotency key is bound to another release request"
            )
        if existing is not None and existing.finished_at is not None:
            return existing
        active = None
        if state is not None and state.active_operation_id is not None:
            active = tx.get_release_operation(state.active_operation_id)
            if active is None:
                raise ReleaseLeaseConflict("active release operation is missing")
            if active.finished_at is None and _lease_expiry(active) > server_time:
                if active.operation_id == operation_id:
                    return active
                raise ReleaseLeaseBusy("service already has a live release lease")
            identity = (state.revision, active.operation_id, active.fencing_token)
            if observation is None or identity != takeover_identity:
                raise ReleaseLeaseConflict(
                    "service changed after external takeover observation"
                )

        fencing_token = 1 if state is None else state.fencing_token + 1
        state_revision = 1 if state is None else state.revision + 1
        expires_at = (server_time + timedelta(seconds=lease_ttl_seconds)).isoformat()
        if (
            active is not None
            and active.operation_id != operation_id
            and active.finished_at is None
        ):
            tx.put_release_operation(
                replace(
                    active,
                    phase=ReleasePhase.RECONCILING,
                    revision=active.revision + 1,
                    lease_expires_at=None,
                    updated_at=server_time.isoformat(),
                    error_class="LeaseTakenOver",
                    error_summary="expired service release lease was fenced",
                )
            )

        if existing is None:
            operation = ReleaseOperationRecord(
                schema_version="2.0",
                environment_fingerprint=environment_fingerprint,
                operation_id=operation_id,
                idempotency_key_hash=key_hash,
                request_fingerprint=fingerprint,
                service_name=service_name,
                target_release_id=target_release_id,
                phase=ReleasePhase.RESERVED,
                owner_principal=owner_principal,
                fencing_token=fencing_token,
                revision=1,
                expected_service_revision=state_revision,
                lease_expires_at=expires_at,
                expected_deployment_uid=(
                    None if observation is None else observation.deployment_uid
                ),
                expected_deployment_resource_version=(
                    None
                    if observation is None
                    else observation.deployment_resource_version
                ),
                expected_service_resource_version=(
                    None
                    if observation is None
                    else observation.service_resource_version
                ),
                created_at=server_time.isoformat(),
                updated_at=server_time.isoformat(),
            )
        else:
            operation = replace(
                existing,
                owner_principal=owner_principal,
                fencing_token=fencing_token,
                revision=existing.revision + 1,
                expected_service_revision=state_revision,
                lease_expires_at=expires_at,
                expected_deployment_uid=(
                    None if observation is None else observation.deployment_uid
                ),
                expected_deployment_resource_version=(
                    None
                    if observation is None
                    else observation.deployment_resource_version
                ),
                expected_service_resource_version=(
                    None
                    if observation is None
                    else observation.service_resource_version
                ),
                updated_at=server_time.isoformat(),
            )
        new_state = ServiceReleaseState(
            schema_version="2.0",
            environment_fingerprint=environment_fingerprint,
            service_name=service_name,
            revision=state_revision,
            display_version_counter=(
                0 if state is None else state.display_version_counter
            ),
            desired_release_id=None if state is None else state.desired_release_id,
            desired_revision=0 if state is None else state.desired_revision,
            observed_release_id=None if state is None else state.observed_release_id,
            observed_evidence_revision=(
                0 if state is None else state.observed_evidence_revision
            ),
            traffic_release_id=None if state is None else state.traffic_release_id,
            traffic_k8s_resource_version=(
                None if state is None else state.traffic_k8s_resource_version
            ),
            champion_release_id=None if state is None else state.champion_release_id,
            previous_release_id=None if state is None else state.previous_release_id,
            active_operation_id=operation_id,
            active_operation_phase=operation.phase,
            fencing_token=fencing_token,
            security_watermark=0 if state is None else state.security_watermark,
            updated_at=server_time.isoformat(),
        )
        tx.put_release_operation(operation)
        tx.put_service_release_state(new_state)
        return operation

    return runner(acquire)


def require_release_lease(
    registry,
    *,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
) -> ReleaseOperationRecord:
    operation = registry.get_release_operation(operation_id)
    if operation is None:
        raise ReleaseLeaseFenced("release operation does not exist")
    state = registry.get_service_release_state(operation.service_name)
    if (
        state is None
        or state.active_operation_id != operation_id
        or state.fencing_token != fencing_token
        or operation.owner_principal != owner_principal
        or operation.fencing_token != fencing_token
        or operation.finished_at is not None
        or _lease_expiry(operation) <= _server_time(registry)
    ):
        raise ReleaseLeaseFenced("release lease is stale or no longer owned")
    return operation


def takeover_release_lease(
    registry,
    *,
    operation_id: str,
    owner_principal: str,
    read_external_state: Callable[[str], ReleaseExternalState],
    lease_ttl_seconds: int = DEFAULT_RELEASE_LEASE_TTL_SECONDS,
) -> ReleaseOperationRecord:
    """Fence and take over one expired non-terminal release operation."""

    _validate_ttl(lease_ttl_seconds)
    operation = registry.get_release_operation(operation_id)
    if operation is None:
        raise ReleaseLeaseConflict("release operation does not exist")
    state = registry.get_service_release_state(operation.service_name)
    if (
        state is None
        or state.active_operation_id != operation.operation_id
        or state.revision != operation.expected_service_revision
        or state.fencing_token != operation.fencing_token
    ):
        raise ReleaseLeaseConflict("release operation and service state disagree")
    if operation.finished_at is not None:
        return operation
    now = _server_time(registry)
    if _lease_expiry(operation) > now:
        if (
            operation.owner_principal == owner_principal
            and operation.phase is ReleasePhase.RECONCILING
        ):
            return operation
        raise ReleaseLeaseBusy("release operation still has a live lease")
    observation = read_external_state(operation.service_name)
    if not isinstance(observation, ReleaseExternalState):
        raise ReleaseLeaseError(
            "external state reader must return ReleaseExternalState"
        )
    expected_identity = (
        operation.revision,
        state.revision,
        operation.fencing_token,
    )

    def takeover(tx):
        current = tx.get_release_operation(operation_id)
        current_state = tx.get_service_release_state(operation.service_name)
        if current is None or current_state is None:
            raise ReleaseLeaseConflict("release operation disappeared during takeover")
        identity = (
            current.revision,
            current_state.revision,
            current.fencing_token,
        )
        if (
            identity != expected_identity
            or current_state.active_operation_id != current.operation_id
            or current_state.fencing_token != current.fencing_token
            or current.finished_at is not None
            or _lease_expiry(current) > _server_time(tx)
        ):
            raise ReleaseLeaseConflict(
                "release operation changed after external takeover observation"
            )
        server_time = _server_time(tx)
        fencing_token = current_state.fencing_token + 1
        new_state = replace(
            current_state,
            revision=current_state.revision + 1,
            active_operation_phase=ReleasePhase.RECONCILING,
            fencing_token=fencing_token,
            updated_at=server_time.isoformat(),
        )
        taken_over = replace(
            current,
            phase=ReleasePhase.RECONCILING,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
            revision=current.revision + 1,
            expected_service_revision=new_state.revision,
            lease_expires_at=(
                server_time + timedelta(seconds=lease_ttl_seconds)
            ).isoformat(),
            expected_deployment_uid=observation.deployment_uid,
            expected_deployment_resource_version=(
                observation.deployment_resource_version
            ),
            expected_service_resource_version=(
                observation.service_resource_version
            ),
            error_class="ReleaseLeaseTakenOver",
            error_summary="expired release operation was fenced for reconciliation",
            updated_at=server_time.isoformat(),
        )
        tx.put_release_operation(taken_over)
        tx.put_service_release_state(new_state)
        return taken_over

    return registry.run_atomic(takeover)


def heartbeat_release_lease(
    registry,
    *,
    operation_id: str,
    owner_principal: str,
    fencing_token: int,
    lease_ttl_seconds: int = DEFAULT_RELEASE_LEASE_TTL_SECONDS,
) -> ReleaseOperationRecord:
    _validate_ttl(lease_ttl_seconds)
    runner = getattr(registry, "run_atomic", None)
    if not callable(runner):
        raise ReleaseLeaseError("Registry lacks the required atomic transaction boundary")

    def heartbeat(tx):
        operation = require_release_lease(
            tx,
            operation_id=operation_id,
            owner_principal=owner_principal,
            fencing_token=fencing_token,
        )
        server_time = _server_time(tx)
        renewed = replace(
            operation,
            revision=operation.revision + 1,
            lease_expires_at=(
                server_time + timedelta(seconds=lease_ttl_seconds)
            ).isoformat(),
            updated_at=server_time.isoformat(),
        )
        return tx.put_release_operation(renewed)

    return runner(heartbeat)

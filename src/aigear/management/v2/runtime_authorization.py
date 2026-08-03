"""Bounded runtime authorization issuance and renewal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from aigear.management.v2.attestation import AttestationVerifier
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.records.runtime_evidence import (
    RuntimeAuthorizationLease,
    compute_runtime_authorization_lease_id,
)
from aigear.management.v2.release_manifest import (
    RuntimeContract,
    SignedReleaseManifest,
    verify_release_manifest,
)
from aigear.management.v2.resolver import UsageContext
from aigear.management.v2.runtime_binding import (
    RuntimeBindingConflict,
    VerifiedJournalWatermark,
    compute_runtime_binding_digest,
    resolve_runtime_assets,
    revalidate_current_runtime_bindings,
)

__all__ = [
    "DEFAULT_RUNTIME_AUTHORIZATION_TTL_SECONDS",
    "DEFAULT_READINESS_RENEWAL_MARGIN_SECONDS",
    "RuntimeAuthorizationError",
    "RuntimeAuthorizationConflict",
    "RuntimeAuthorizationExpired",
    "VerifiedJournalWatermark",
    "compute_runtime_binding_digest",
    "issue_runtime_authorization",
    "renew_runtime_authorization",
    "require_runtime_readiness",
]


DEFAULT_RUNTIME_AUTHORIZATION_TTL_SECONDS = 300
DEFAULT_READINESS_RENEWAL_MARGIN_SECONDS = 30
_MAX_RUNTIME_AUTHORIZATION_TTL_SECONDS = 15 * 60


class RuntimeAuthorizationError(ValueError):
    pass


class RuntimeAuthorizationConflict(RuntimeAuthorizationError):
    pass


class RuntimeAuthorizationExpired(RuntimeAuthorizationError):
    pass


def _aware_utc(field_name: str, value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise RuntimeAuthorizationError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _authorize(
    registry,
    *,
    control: ControlDocument,
    layout: GcsLayoutV2,
    manifest: SignedReleaseManifest,
    service_name: str,
    deployment_target_id: str,
    pod_uid: str,
    expected_runtime_contract: RuntimeContract,
    asset_attestation_verifier: AttestationVerifier,
    release_attestation_verifier: AttestationVerifier,
    release_key_versions: Sequence[str],
    non_release_key_versions: Sequence[str],
    journal: VerifiedJournalWatermark,
    issuer_principal: str,
    max_ttl_seconds: int,
    previous_lease: Optional[RuntimeAuthorizationLease],
) -> RuntimeAuthorizationLease:
    if not isinstance(control, ControlDocument) or control.authority != "v2":
        raise RuntimeAuthorizationError("an authoritative V2 control document is required")
    if not isinstance(pod_uid, str) or not pod_uid:
        raise RuntimeAuthorizationError("pod_uid must be a non-empty str")
    if not isinstance(issuer_principal, str) or not issuer_principal:
        raise RuntimeAuthorizationError("issuer_principal must be a non-empty str")
    if (
        isinstance(max_ttl_seconds, bool)
        or not isinstance(max_ttl_seconds, int)
        or not 1 <= max_ttl_seconds <= _MAX_RUNTIME_AUTHORIZATION_TTL_SECONDS
    ):
        raise RuntimeAuthorizationError("max_ttl_seconds must be between 1 and 900")
    release = registry.get_release(manifest.release_id)
    if (
        release is None
        or release.environment_fingerprint != control.environment_fingerprint
        or release.service_name != service_name
        or manifest.core.service_name != service_name
        or manifest.core.deployment_target_id != deployment_target_id
        or release.manifest_digest != manifest.release_id
        or release.signature_attestation_id != manifest.attestation.attestation_id
    ):
        raise RuntimeAuthorizationConflict("release Registry identity does not match manifest")
    observed_at = _aware_utc(
        "Registry server read time", registry.get_server_read_time()
    )
    if journal.verified_at > observed_at or journal.fresh_until <= observed_at:
        raise RuntimeAuthorizationExpired("security journal watermark is not fresh")

    try:
        handles = resolve_runtime_assets(
            registry,
            control=control,
            layout=layout,
            manifest=manifest,
            at=observed_at,
            verifier=asset_attestation_verifier,
        )
        verify_release_manifest(
            manifest,
            verifier=release_attestation_verifier,
            release_key_versions=release_key_versions,
            non_release_key_versions=non_release_key_versions,
            expected_environment_fingerprint=control.environment_fingerprint,
            expected_service_name=service_name,
            expected_deployment_target_id=deployment_target_id,
            expected_runtime_contract=expected_runtime_contract,
            resolved_assets=handles,
            now=observed_at,
            expected_asset_usage_context=UsageContext.SERVICE_RUNTIME,
        )
    except ValueError as exc:
        raise RuntimeAuthorizationError("runtime release verification failed") from exc

    binding_digest = compute_runtime_binding_digest(manifest, handles, journal)
    policy_ids = tuple(
        sorted(
            {handle.policy_decision_head_ref for handle in handles.values()},
            key=lambda value: value.typed,
        )
    )
    policy_valid_until = min(
        datetime.fromisoformat(handle.policy_valid_until)
        for handle in handles.values()
    )
    runner = getattr(registry, "run_atomic", None)
    if not callable(runner):
        raise RuntimeAuthorizationError(
            "Registry lacks the required atomic transaction boundary"
        )

    def commit(tx):
        current_release = tx.get_release(manifest.release_id)
        state = tx.get_service_release_state(service_name)
        current_control = tx.get_control_document()
        issued_at = _aware_utc(
            "Registry server read time", tx.get_server_read_time()
        )
        if (
            current_release != release
            or current_control != control
            or state is None
            or state.desired_release_id != manifest.release_id
            or state.security_watermark != journal.security_watermark
        ):
            raise RuntimeAuthorizationConflict(
                "release, control, desired state, or watermark changed"
            )
        try:
            revalidate_current_runtime_bindings(
                tx, handles=handles, at=issued_at
            )
        except RuntimeBindingConflict as exc:
            raise RuntimeAuthorizationConflict(
                "runtime binding changed before lease commit"
            ) from exc
        expires_at = min(
            policy_valid_until,
            journal.fresh_until,
            issued_at + timedelta(seconds=max_ttl_seconds),
        )
        if expires_at <= issued_at:
            raise RuntimeAuthorizationExpired(
                "no positive runtime authorization window remains"
            )
        if previous_lease is not None:
            stored_previous = tx.get_runtime_authorization_lease(
                service_name, previous_lease.lease_id
            )
            if stored_previous != previous_lease:
                raise RuntimeAuthorizationConflict(
                    "previous runtime lease is not the authoritative record"
                )
            previous_expiry = datetime.fromisoformat(previous_lease.expires_at)
            if previous_expiry <= issued_at:
                raise RuntimeAuthorizationExpired("runtime lease already expired")
            if (
                previous_lease.environment_fingerprint
                != control.environment_fingerprint
                or previous_lease.release_id != manifest.release_id
                or previous_lease.pod_uid != pod_uid
                or previous_lease.binding_digest != binding_digest
                or previous_lease.policy_attestation_ids != policy_ids
                or previous_lease.security_watermark != journal.security_watermark
            ):
                raise RuntimeAuthorizationConflict(
                    "runtime lease renewal identity changed"
                )
        issued_at_text = issued_at.isoformat()
        lease = RuntimeAuthorizationLease(
            schema_version="2.0",
            environment_fingerprint=control.environment_fingerprint,
            lease_id=compute_runtime_authorization_lease_id(
                release_id=manifest.release_id,
                pod_uid=pod_uid,
                binding_digest=binding_digest,
                policy_attestation_ids=policy_ids,
                security_watermark=journal.security_watermark,
                issued_at=issued_at_text,
            ),
            release_id=manifest.release_id,
            pod_uid=pod_uid,
            binding_digest=binding_digest,
            policy_attestation_ids=policy_ids,
            policy_valid_until=policy_valid_until.isoformat(),
            security_watermark=journal.security_watermark,
            journal_fresh_until=journal.fresh_until.isoformat(),
            max_ttl_seconds=max_ttl_seconds,
            issuer_principal=issuer_principal,
            issued_at=issued_at_text,
            expires_at=expires_at.isoformat(),
        )
        return tx.put_runtime_authorization_lease(service_name, lease)

    return runner(commit)


def issue_runtime_authorization(
    registry,
    *,
    max_ttl_seconds: int = DEFAULT_RUNTIME_AUTHORIZATION_TTL_SECONDS,
    **kwargs,
) -> RuntimeAuthorizationLease:
    return _authorize(
        registry,
        max_ttl_seconds=max_ttl_seconds,
        previous_lease=None,
        **kwargs,
    )


def renew_runtime_authorization(
    registry,
    *,
    previous_lease: RuntimeAuthorizationLease,
    max_ttl_seconds: int = DEFAULT_RUNTIME_AUTHORIZATION_TTL_SECONDS,
    **kwargs,
) -> RuntimeAuthorizationLease:
    if not isinstance(previous_lease, RuntimeAuthorizationLease):
        raise RuntimeAuthorizationError(
            "previous_lease must be RuntimeAuthorizationLease"
        )
    return _authorize(
        registry,
        max_ttl_seconds=max_ttl_seconds,
        previous_lease=previous_lease,
        **kwargs,
    )


def require_runtime_readiness(
    lease: RuntimeAuthorizationLease,
    *,
    at: datetime,
    renewal_margin_seconds: int = DEFAULT_READINESS_RENEWAL_MARGIN_SECONDS,
) -> RuntimeAuthorizationLease:
    if not isinstance(lease, RuntimeAuthorizationLease):
        raise RuntimeAuthorizationError("lease must be RuntimeAuthorizationLease")
    at = _aware_utc("at", at)
    if (
        isinstance(renewal_margin_seconds, bool)
        or not isinstance(renewal_margin_seconds, int)
        or renewal_margin_seconds < 0
    ):
        raise RuntimeAuthorizationError(
            "renewal_margin_seconds must be a non-negative int"
        )
    fail_closed_at = datetime.fromisoformat(lease.expires_at) - timedelta(
        seconds=renewal_margin_seconds
    )
    if at >= fail_closed_at:
        raise RuntimeAuthorizationExpired(
            "runtime readiness must fail closed before lease expiry"
        )
    return lease

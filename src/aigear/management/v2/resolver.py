"""Resolver protocol (spec section 6.4).

Implements the branches of spec 6.4's resolver protocol that production
automation actually needs (see ``docs/pipeline-v2-phase-b-tasks.md`` section
3 for the full scope boundary this repeats): resolving a closed ``Selector``
(asset label, run output, exact Occurrence, or raw AssetVersion) into an
immutable, short-TTL :class:`ResolvedHandle`, validating the control
document, AssetVersion lifecycle/trust, and each component Blob's location
along the way.

Deliberately out of scope:

- Raw projection-URI selectors (spec 6.4 bullet 1) and projection-freshness
  repair (bullet 6): both need a real GCS projection read, which is T26's
  job, not this module's.
- ``export``/``promotion``/``alias``/``release``/``service_runtime`` usage
  contexts (see the phase-b task doc's scope boundary).
- Firestore read-time snapshots: this module takes ``now`` as a plain
  parameter and uses it as the handle's ``resolved_at``, since
  :class:`~aigear.management.v2.fake_registry.FakeRegistryV2` has no
  transaction/snapshot concept to read a real ``read_time`` from.
- A real policy-decision engine: no ``PolicyDecision`` record type exists yet
  anywhere in this package, so "验证当前 approved policy head/epoch
  binding/attestation" is approximated by checking
  ``AssetVersionRecord.policy_decision_head_ref is not None`` alongside
  ``trust_state=approved``; tighten this once that record type exists.

``same_run_direct_upstream``'s relaxed trust bar needs proof that the
*consumer* actually declared this exact Occurrence as one of its own inputs.
That proof already exists before the consumer's own Attempt runs: spec 9.2's
resolved-input-binding step seals it into every one of the consumer's own
provisional Occurrences (``resolved_input_bindings``, see
``step_lease.acquire_step_lease``) well before the consumer's own finalize
(and thus its own outgoing ``LineageEdge``\\ s) exist. So this usage context
takes ``consumer_occurrence_id``/``consumer_binding_name`` and checks the
*consumer's* sealed ``resolved_input_bindings`` for a matching entry, not the
producer's outgoing lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.asset_version import AssetVersionRecord, LifecycleState, TrustState
from aigear.management.v2.records.blob import AvailabilityState
from aigear.management.v2.records.label import compute_label_id
from aigear.management.v2.records.occurrence import OccurrenceStatus, compute_committed_output_key

__all__ = [
    "ResolverError",
    "UsageContext",
    "SelectorKind",
    "Selector",
    "DEFAULT_RESOLVE_TTL",
    "ResolvedBlobHandle",
    "ResolvedHandle",
    "resolve",
]


class ResolverError(ValueError):
    """Raised when a selector cannot be resolved to a usable handle."""


class UsageContext(str, Enum):
    """The subset of spec 6.4's closed ``usage_context`` values this module
    implements (export/promotion/alias/release/service_runtime are out of
    scope, see module docstring)."""

    SAME_RUN_DIRECT_UPSTREAM = "same_run_direct_upstream"
    NEW_RUN_SEED = "new_run_seed"
    CROSS_RUN = "cross_run"
    MANUAL_DOWNLOAD = "manual_download"


class SelectorKind(str, Enum):
    ASSET_LABEL = "asset_label"
    RUN_OUTPUT = "run_output"
    OCCURRENCE = "occurrence"
    ASSET_VERSION = "asset_version"


# Spec 6.4 gives no concrete number for the resolved handle's short TTL;
# 5 minutes is a pragmatic default (same reasoning as step_lease.py's
# DEFAULT_LEASE_TTL), long enough for an immediate download, short enough
# that a stale handle cannot outlive a revoke for long.
DEFAULT_RESOLVE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class Selector:
    """One closed selector branch (spec 6.4 bullet 3); exactly one of the four
    ``by_*`` constructors below should be used to build one."""

    kind: SelectorKind
    asset_type: Optional[str] = None
    asset_name: Optional[str] = None
    display_version: Optional[str] = None
    run_id: Optional[str] = None
    step_name: Optional[str] = None
    output_name: Optional[str] = None
    occurrence_id: Optional[TypedId] = None
    asset_version_id: Optional[TypedId] = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SelectorKind):
            raise ResolverError(f"kind must be a SelectorKind, got {self.kind!r}")

        required_by_kind = {
            SelectorKind.ASSET_LABEL: ("asset_type", "asset_name", "display_version"),
            SelectorKind.RUN_OUTPUT: ("run_id", "step_name", "output_name"),
            SelectorKind.OCCURRENCE: ("occurrence_id",),
            SelectorKind.ASSET_VERSION: ("asset_version_id",),
        }
        required = required_by_kind[self.kind]
        all_fields = ("asset_type", "asset_name", "display_version", "run_id", "step_name",
                       "output_name", "occurrence_id", "asset_version_id")
        for field_name in all_fields:
            value = getattr(self, field_name)
            if field_name in required and value is None:
                raise ResolverError(f"selector kind {self.kind.value!r} requires {field_name!r}")
            if field_name not in required and value is not None:
                raise ResolverError(
                    f"selector kind {self.kind.value!r} must not set {field_name!r}"
                )

    @classmethod
    def by_asset_label(cls, asset_type: str, asset_name: str, display_version: str) -> "Selector":
        return cls(
            kind=SelectorKind.ASSET_LABEL,
            asset_type=asset_type,
            asset_name=asset_name,
            display_version=display_version,
        )

    @classmethod
    def by_run_output(cls, run_id: str, step_name: str, output_name: str) -> "Selector":
        return cls(kind=SelectorKind.RUN_OUTPUT, run_id=run_id, step_name=step_name, output_name=output_name)

    @classmethod
    def by_occurrence(cls, occurrence_id: TypedId) -> "Selector":
        return cls(kind=SelectorKind.OCCURRENCE, occurrence_id=occurrence_id)

    @classmethod
    def by_asset_version(cls, asset_version_id: TypedId) -> "Selector":
        return cls(kind=SelectorKind.ASSET_VERSION, asset_version_id=asset_version_id)


@dataclass(frozen=True)
class ResolvedBlobHandle:
    """One component's resolved, exact-generation Blob location (spec 6.4 bullet 7)."""

    role: str
    logical_name: str
    blob_id: TypedId
    bucket: str
    object_name: str
    generation: str
    sha256: str
    size_bytes: int
    location_revision: int
    location_attestation_ref: TypedId


@dataclass(frozen=True)
class ResolvedHandle:
    """An immutable, short-TTL resolved handle (spec 6.4 bullet 7).

    ``occurrence_id``/``occurrence_reference_epoch`` and
    ``label_id``/``label_display_version`` are only populated when the
    selector branch that produced this handle actually has one (raw
    ``asset_version`` selectors have neither).
    """

    usage_context: UsageContext
    resolved_at: datetime
    expires_at: datetime
    environment_fingerprint: TypedId
    registry_binding_id: str
    write_epoch: int
    asset_version_id: TypedId
    asset_record_revision: int
    policy_decision_head_ref: Optional[TypedId]
    blobs: Tuple[ResolvedBlobHandle, ...]
    occurrence_id: Optional[TypedId] = None
    occurrence_reference_epoch: Optional[int] = None
    label_id: Optional[TypedId] = None
    label_display_version: Optional[str] = None


def _resolve_asset_version_by_label(
    registry: FakeRegistryV2, selector: Selector
) -> Tuple[AssetVersionRecord, Optional[TypedId], Optional[int], Optional[TypedId], Optional[str]]:
    label_id = compute_label_id(selector.asset_type, selector.asset_name, selector.display_version)
    label = registry.get_label(label_id)
    if label is None:
        raise ResolverError(f"no Label found for label_id {label_id.typed!r}")
    asset_version = registry.get_asset_version(label.asset_version_id)
    if asset_version is None:
        raise ResolverError(
            f"Label {label_id.typed!r} points to missing AssetVersion "
            f"{label.asset_version_id.typed!r}"
        )
    return asset_version, None, None, label.label_id, label.display_version


def _resolve_asset_version_by_run_output(
    registry: FakeRegistryV2, selector: Selector
) -> Tuple[AssetVersionRecord, Optional[TypedId], Optional[int], Optional[TypedId], Optional[str]]:
    committed_output_key = compute_committed_output_key(
        selector.run_id, selector.step_name, selector.output_name
    )
    occurrence = registry.get_committed_occurrence_by_output_key(committed_output_key)
    if occurrence is None or occurrence.status != OccurrenceStatus.COMMITTED:
        raise ResolverError(
            f"no committed output found for (run_id={selector.run_id!r}, "
            f"step_name={selector.step_name!r}, output_name={selector.output_name!r})"
        )
    asset_version = registry.get_asset_version(occurrence.asset_version_id)
    if asset_version is None:
        raise ResolverError(
            f"Occurrence {occurrence.occurrence_id.typed!r} points to missing AssetVersion "
            f"{occurrence.asset_version_id.typed!r}"
        )
    # Spec 6.4 bullet 3: use the Occurrence's own sealed label_id/display_version,
    # never re-derive or guess a label independently.
    return (
        asset_version,
        occurrence.occurrence_id,
        occurrence.reference_epoch,
        occurrence.label_id,
        occurrence.display_version,
    )


def _resolve_asset_version_by_occurrence(
    registry: FakeRegistryV2, selector: Selector
) -> Tuple[AssetVersionRecord, Optional[TypedId], Optional[int], Optional[TypedId], Optional[str]]:
    occurrence = registry.get_occurrence(selector.occurrence_id)
    if occurrence is None or occurrence.status != OccurrenceStatus.COMMITTED:
        raise ResolverError(f"Occurrence {selector.occurrence_id.typed!r} is not a committed Occurrence")
    winner = registry.get_committed_occurrence_by_output_key(occurrence.committed_output_key)
    if winner is None or winner.occurrence_id != occurrence.occurrence_id:
        raise ResolverError(
            f"Occurrence {occurrence.occurrence_id.typed!r} is no longer the committed-output "
            "binding's winner"
        )
    asset_version = registry.get_asset_version(occurrence.asset_version_id)
    if asset_version is None:
        raise ResolverError(
            f"Occurrence {occurrence.occurrence_id.typed!r} points to missing AssetVersion "
            f"{occurrence.asset_version_id.typed!r}"
        )
    return (
        asset_version,
        occurrence.occurrence_id,
        occurrence.reference_epoch,
        occurrence.label_id,
        occurrence.display_version,
    )


def _resolve_asset_version_raw(
    registry: FakeRegistryV2, selector: Selector
) -> Tuple[AssetVersionRecord, Optional[TypedId], Optional[int], Optional[TypedId], Optional[str]]:
    asset_version = registry.get_asset_version(selector.asset_version_id)
    if asset_version is None:
        raise ResolverError(f"no AssetVersion found for {selector.asset_version_id.typed!r}")
    return asset_version, None, None, None, None


_RESOLVERS_BY_KIND = {
    SelectorKind.ASSET_LABEL: _resolve_asset_version_by_label,
    SelectorKind.RUN_OUTPUT: _resolve_asset_version_by_run_output,
    SelectorKind.OCCURRENCE: _resolve_asset_version_by_occurrence,
    SelectorKind.ASSET_VERSION: _resolve_asset_version_raw,
}


def _check_same_run_direct_upstream(
    registry: FakeRegistryV2,
    *,
    producer_occurrence_id: Optional[TypedId],
    consumer_occurrence_id: Optional[TypedId],
    consumer_binding_name: Optional[str],
) -> None:
    if producer_occurrence_id is None:
        raise ResolverError(
            "same_run_direct_upstream requires a selector that resolves to an Occurrence "
            "(run_output or occurrence)"
        )
    if consumer_occurrence_id is None or not consumer_binding_name:
        raise ResolverError(
            "same_run_direct_upstream requires consumer_occurrence_id and "
            "consumer_binding_name"
        )
    normalized_binding_name = validate_segment(consumer_binding_name, field_name="consumer_binding_name")

    consumer_occurrence = registry.get_occurrence(consumer_occurrence_id)
    if consumer_occurrence is None:
        raise ResolverError(f"no Occurrence found for consumer_occurrence_id {consumer_occurrence_id.typed!r}")

    declared = any(
        binding.binding_name == normalized_binding_name and binding.occurrence_id == producer_occurrence_id
        for binding in consumer_occurrence.resolved_input_bindings
    )
    if not declared:
        raise ResolverError(
            f"consumer Occurrence {consumer_occurrence_id.typed!r} has no resolved input binding "
            f"named {normalized_binding_name!r} pointing at producer Occurrence "
            f"{producer_occurrence_id.typed!r}"
        )


def _check_trust_and_lifecycle(asset_version: AssetVersionRecord, usage_context: UsageContext) -> None:
    if asset_version.lifecycle_state != LifecycleState.ACTIVE:
        raise ResolverError(
            f"AssetVersion {asset_version.asset_version_id.typed!r} is not usable: "
            f"lifecycle_state is {asset_version.lifecycle_state.value!r}, not active"
        )
    if asset_version.trust_state == TrustState.REVOKED:
        raise ResolverError(
            f"AssetVersion {asset_version.asset_version_id.typed!r} is not usable: trust_state is revoked"
        )

    if usage_context == UsageContext.SAME_RUN_DIRECT_UPSTREAM:
        if asset_version.trust_state not in (TrustState.VERIFIED, TrustState.APPROVED):
            raise ResolverError(
                f"AssetVersion {asset_version.asset_version_id.typed!r} is not usable for "
                f"same_run_direct_upstream: trust_state is {asset_version.trust_state.value!r}, "
                "must be at least verified"
            )
        return

    if asset_version.trust_state != TrustState.APPROVED or asset_version.policy_decision_head_ref is None:
        raise ResolverError(
            f"AssetVersion {asset_version.asset_version_id.typed!r} is not usable for "
            f"{usage_context.value!r}: requires trust_state=approved with a policy decision "
            "head, got trust_state="
            f"{asset_version.trust_state.value!r}, policy_decision_head_ref="
            f"{asset_version.policy_decision_head_ref!r}"
        )


def _resolve_blob_handles(
    registry: FakeRegistryV2, layout: GcsLayoutV2, asset_version: AssetVersionRecord
) -> Tuple[ResolvedBlobHandle, ...]:
    handles = []
    for component in asset_version.components:
        blob = registry.get_blob(component.blob_id)
        if blob is None:
            raise ResolverError(f"no Blob found for blob_id {component.blob_id.typed!r}")
        if blob.availability_state != AvailabilityState.READY:
            raise ResolverError(
                f"Blob {component.blob_id.typed!r} is not usable: availability_state is "
                f"{blob.availability_state.value!r}, not ready"
            )
        expected_object_name = layout.canonical_blob(component.blob_id)
        if blob.bucket != layout.bucket_name or blob.object_name != expected_object_name:
            raise ResolverError(
                f"Blob {component.blob_id.typed!r} location does not match its server-derived "
                f"canonical path: expected bucket={layout.bucket_name!r} object_name="
                f"{expected_object_name!r}, got bucket={blob.bucket!r} object_name="
                f"{blob.object_name!r}"
            )
        handles.append(
            ResolvedBlobHandle(
                role=component.role,
                logical_name=component.logical_name,
                blob_id=component.blob_id,
                bucket=blob.bucket,
                object_name=blob.object_name,
                generation=blob.generation,
                sha256=blob.sha256,
                size_bytes=blob.size_bytes,
                location_revision=blob.current_location_revision,
                location_attestation_ref=blob.current_location_attestation_ref,
            )
        )
    return tuple(handles)


def resolve(
    registry: FakeRegistryV2,
    control_document: ControlDocument,
    layout: GcsLayoutV2,
    selector: Selector,
    usage_context: UsageContext,
    now: datetime,
    *,
    ttl: timedelta = DEFAULT_RESOLVE_TTL,
    consumer_occurrence_id: Optional[TypedId] = None,
    consumer_binding_name: Optional[str] = None,
) -> ResolvedHandle:
    """Resolve one closed selector branch into an immutable, short-TTL handle (spec 6.4)."""
    if control_document.authority != "v2":
        raise ResolverError(
            f"control document authority is {control_document.authority!r}, not 'v2'; refusing to resolve"
        )

    resolver_fn = _RESOLVERS_BY_KIND[selector.kind]
    asset_version, occurrence_id, occurrence_reference_epoch, label_id, display_version = resolver_fn(
        registry, selector
    )

    if asset_version.environment_fingerprint != control_document.environment_fingerprint:
        raise ResolverError(
            f"AssetVersion {asset_version.asset_version_id.typed!r} environment_fingerprint does not "
            "match the control document's"
        )

    if usage_context == UsageContext.SAME_RUN_DIRECT_UPSTREAM:
        _check_same_run_direct_upstream(
            registry,
            producer_occurrence_id=occurrence_id,
            consumer_occurrence_id=consumer_occurrence_id,
            consumer_binding_name=consumer_binding_name,
        )

    _check_trust_and_lifecycle(asset_version, usage_context)

    blobs = _resolve_blob_handles(registry, layout, asset_version)

    return ResolvedHandle(
        usage_context=usage_context,
        resolved_at=now,
        expires_at=now + ttl,
        environment_fingerprint=control_document.environment_fingerprint,
        registry_binding_id=control_document.registry_binding.registry_binding_id,
        write_epoch=control_document.write_epoch,
        asset_version_id=asset_version.asset_version_id,
        asset_record_revision=asset_version.record_revision,
        policy_decision_head_ref=asset_version.policy_decision_head_ref,
        blobs=blobs,
        occurrence_id=occurrence_id,
        occurrence_reference_epoch=occurrence_reference_epoch,
        label_id=label_id,
        label_display_version=display_version,
    )

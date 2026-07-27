"""Finalize transaction orchestrator (spec section 10.4, read-validate-write).

Implements spec 10.4/6.5's finalize protocol against the structural Registry
and generation-aware GCS interfaces. Tests use fakes; production uses the
Firestore/GCS adapters and pinned KMS asymmetric signers.

1. Read: Run/Step/Attempt, the provisional Occurrence and any existing
   committed-output binding, an existing BlobRecord (reuse candidate).
2. Validate: Run running, Attempt still the Step's current one (fencing),
   Attempt in an eligible pre-terminal status, the target output slot not
   already committed by a different Occurrence, a reused AssetVersion is
   ``lifecycle_state=active`` and not ``trust_state=revoked``.
3. Write: create-or-validate Blob + first location revision, AssetVersion,
   Label, ComponentEdge/LineageEdge, commit the winning Occurrence
   (incrementing the AssetVersion's ``reference_epoch``), advance
   Attempt/Step to ``succeeded``, and decrement the Run's
   ``remaining_required_steps``.

Pub/Sub OIDC and environment-binding validation occur at the
``PipelineAssetManagement`` boundary before this domain transaction runs.

Two gaps in the surrounding Phase B types that this module's design works
around, both documented where they are actually addressed:

- RunSpec's ``OutputSlotSpec`` (T14) has no asset-naming field, only a
  component's ``role``/``logical_name``; it now defaults ``asset_type``/
  ``asset_name`` to them (see that module). ``display_version`` has no
  source at all, so this module uses ``run_id`` -- every Run that produces
  a given (asset_type, asset_name) stamps one Label version, which is
  deterministic, unique per producing Run, and trivially idempotent on
  replay.
- ``schema_contract_digest``/``runtime_contract_digest``/``policy_version``
  are legitimately external inputs (a schema registry / policy engine's
  job, per spec 8.2), not something a finalizer can compute; callers must
  supply them via :class:`FinalizeContext` instead of this module guessing.

Only a single-payload-per-output shape is supported (spec 8.4/5.3's
multi-component bundles are not): each output's ``AssetVersion`` gets
exactly one ``AssetComponent``, named after the ``OutputSlotSpec``'s own
``role``/``logical_name``. Extending to multi-file bundles is future work,
not required by any Phase B task.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Optional, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.attestation import DigestSigner, HmacTestSigner, create_attestation
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.fake_gcs import GenerationPreconditionError
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    AssetVersionRecord,
    InputBinding,
    LifecycleState,
    ProducerSpec,
    TrustState,
    compute_asset_version_id,
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
from aigear.management.v2.records.label import (
    LabelRecord,
    ReadableManifestProjection,
    compute_label_id,
)
from aigear.management.v2.records.lineage import (
    ComponentEdge,
    LineageEdge,
    compute_component_edge_id,
    compute_lineage_edge_id,
)
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    compute_occurrence_id,
)
from aigear.management.v2.records.operation import OperationPhase, OperationRecord
from aigear.management.v2.records.run import (
    AttemptRecord,
    AttemptStatus,
    RunRecord,
    RunStatus,
    StepRecord,
    StepStatus,
)
from aigear.management.v2.records.run_spec import OutputSlotSpec, RunSpec
from aigear.management.v2.staging_upload import (
    StagingOutputDescriptor,
    StepCompletionMessage,
    validate_step_completion_message,
)
from aigear.management.v2.step_lease import compute_attempt_finalize_operation_id

__all__ = [
    "FinalizeError",
    "FinalizeContext",
    "FinalizedOutput",
    "FinalizeOutcome",
    "reserve_finalize_blob_claims",
    "finalize_step_outputs",
]


class FinalizeError(ValueError):
    """Raised when a completion message fails finalize's read/validate phase."""


_ATTEMPT_ELIGIBLE_STATUSES = frozenset(
    {AttemptStatus.LEASED, AttemptStatus.RUNNING, AttemptStatus.COMMITTING}
)

# Neither the Step nor Attempt state machine (spec 9.1) has a direct
# leased/running -> succeeded edge; finalize is the only caller that ever
# drives a completed Attempt, so it walks every intermediate edge itself.
_ATTEMPT_LADDER = (
    AttemptStatus.LEASED,
    AttemptStatus.RUNNING,
    AttemptStatus.COMMITTING,
    AttemptStatus.SUCCEEDED,
)
_STEP_LADDER = (StepStatus.LEASED, StepStatus.RUNNING, StepStatus.COMMITTING, StepStatus.SUCCEEDED)


@dataclass(frozen=True)
class FinalizeContext:
    """Cross-cutting inputs finalize needs that no Phase B message carries
    (see module docstring for why each is a caller input, not a computed value)."""

    environment_id: str
    environment_fingerprint: TypedId
    schema_version: str
    schema_contract_digest: TypedId
    runtime_contract_digest: TypedId
    policy_version: str
    now: datetime
    manifest_integrity_signer: DigestSigner = field(default_factory=HmacTestSigner)
    blob_location_signer: DigestSigner = field(default_factory=HmacTestSigner)
    occurrence_finalization_signer: DigestSigner = field(default_factory=HmacTestSigner)
    write_epoch: int = 1
    firestore_database_id: Optional[str] = None
    firestore_database_resource: Optional[str] = None
    registry_binding_id: Optional[str] = None
    registry_binding_epoch: Optional[int] = None


@dataclass(frozen=True)
class FinalizedOutput:
    """One output slot's finalize result."""

    output_name: str
    occurrence: OccurrenceRecord
    asset_version: AssetVersionRecord
    label: LabelRecord
    blob: BlobRecord


@dataclass(frozen=True)
class FinalizeOutcome:
    run: RunRecord
    step: StepRecord
    attempt: AttemptRecord
    outputs: Tuple[FinalizedOutput, ...]


def reserve_finalize_blob_claims(
    registry: FakeRegistryV2,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    completion_message: StepCompletionMessage,
    run_spec: RunSpec,
    *,
    now: datetime,
    claim_ttl: timedelta = timedelta(minutes=15),
) -> None:
    """Durably reserve every missing Blob before canonical GCS side effects.

    This is intentionally a separate short Registry transaction.  If the
    process crashes after the GCS copy but before the finalize transaction,
    the adoption claim remains visible to orphan GC and reconcile.
    """
    validate_step_completion_message(completion_message, run_spec)
    run = registry.get_run(completion_message.run_id)
    step = registry.get_step(completion_message.run_id, completion_message.step_name)
    attempt = registry.get_attempt(
        completion_message.run_id, completion_message.step_name, completion_message.attempt_no
    )
    if run is None or run.status != RunStatus.RUNNING or step is None or attempt is None:
        raise FinalizeError("Run/Step/Attempt is missing or no longer eligible for finalize reservation")
    if step.current_attempt_no != attempt.attempt_no:
        raise FinalizeError("Attempt is no longer current")
    if completion_message.fencing_token is not None and completion_message.fencing_token != attempt.fencing_token:
        raise FinalizeError("completion fencing_token mismatch")

    for descriptor in completion_message.outputs:
        snapshot = gcs.get_object(descriptor.staging_object, generation=descriptor.generation)
        if snapshot.sha256 != descriptor.digest.bare or snapshot.size_bytes != descriptor.size:
            raise FinalizeError("staging object digest/size mismatch during claim reservation")
        if registry.get_blob(descriptor.digest) is not None:
            continue
        canonical_name = layout.canonical_blob(descriptor.digest)
        request_digest = TypedId.from_bare(
            digest_sha256_of_jcs(
                ["aigear.blob-claim-request.v2", completion_message.operation_id, descriptor.digest.typed]
            )
        )
        existing = registry.get_blob_claim(descriptor.digest)
        if existing is not None:
            if (
                existing.state != ClaimState.ADOPTING
                or existing.operation_id != completion_message.operation_id
                or existing.request_digest != request_digest
                or existing.fencing_token != attempt.fencing_token
            ):
                raise FinalizeError(f"Blob {descriptor.digest.typed!r} is claimed by another operation")
            continue
        registry.put_blob_claim(
            BlobClaim(
                blob_id=descriptor.digest,
                claim_epoch=1,
                fencing_token=attempt.fencing_token,
                operation_id=completion_message.operation_id,
                expected_object_name=canonical_name,
                request_digest=request_digest,
                state=ClaimState.ADOPTING,
                lease_expires_at=(now + claim_ttl).isoformat(),
            )
        )


def _request_fingerprint(message: StepCompletionMessage) -> str:
    payload = [
        "aigear.finalize-request.v2",
        message.run_id,
        message.step_name,
        message.attempt_no,
        message.operation_id,
        [
            {
                "output_name": output.output_name,
                "staging_object": output.staging_object,
                "generation": output.generation,
                "digest": output.digest.typed,
                "size": output.size,
                "media_type": output.media_type,
            }
            for output in message.outputs
        ],
    ]
    return digest_sha256_of_jcs(payload)


def _advance_attempt_to_succeeded(
    registry: FakeRegistryV2, run_id: str, step_name: str, attempt_no: int, current: AttemptStatus
) -> None:
    for target in _ATTEMPT_LADDER[_ATTEMPT_LADDER.index(current) + 1 :]:
        registry.update_attempt_status(run_id, step_name, attempt_no, target)


def _advance_step_to_succeeded(
    registry: FakeRegistryV2, run_id: str, step_name: str, current: StepStatus
) -> None:
    for target in _STEP_LADDER[_STEP_LADDER.index(current) + 1 :]:
        registry.update_step_status(run_id, step_name, target)


def _step_is_succeeded(registry: FakeRegistryV2, run_id: str, step_name: str) -> bool:
    step = registry.get_step(run_id, step_name)
    return step is not None and step.status == StepStatus.SUCCEEDED


def _advance_run(registry: FakeRegistryV2, run: RunRecord, run_spec: RunSpec) -> RunRecord:
    remaining = run.remaining_required_steps
    if remaining is None:
        remaining = len(run_spec.steps)
    remaining -= 1

    if remaining <= 0 and all(
        _step_is_succeeded(registry, run.run_id, step.step_name) for step in run_spec.steps
    ):
        return registry.update_run_status(
            run.run_id, RunStatus.SUCCEEDED, remaining_required_steps=0
        )
    return registry.update_run_status(run.run_id, run.status, remaining_required_steps=remaining)


def _rebuild_outcome(
    registry: FakeRegistryV2, run: RunRecord, step: StepRecord, attempt: AttemptRecord,
    completion_message: StepCompletionMessage,
) -> FinalizeOutcome:
    outputs = []
    for descriptor in completion_message.outputs:
        occurrence_id = compute_occurrence_id(
            run.run_id, step.step_name, attempt.attempt_no, descriptor.output_name
        )
        occurrence = registry.get_occurrence(occurrence_id)
        if occurrence is None or occurrence.status != OccurrenceStatus.COMMITTED:
            raise FinalizeError(
                "operation is recorded as succeeded but Occurrence "
                f"{occurrence_id.typed!r} is missing or not committed; registry state "
                "is inconsistent"
            )
        asset_version = registry.get_asset_version(occurrence.asset_version_id)
        label = registry.get_label(occurrence.label_id)
        blob = registry.get_blob(asset_version.components[0].blob_id)
        outputs.append(FinalizedOutput(descriptor.output_name, occurrence, asset_version, label, blob))

    return FinalizeOutcome(
        run=registry.get_run(run.run_id),
        step=registry.get_step(run.run_id, step.step_name),
        attempt=registry.get_attempt(run.run_id, step.step_name, attempt.attempt_no),
        outputs=tuple(outputs),
    )


def _finalize_one_output(
    *,
    registry: FakeRegistryV2,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    context: FinalizeContext,
    run_id: str,
    step_name: str,
    attempt: AttemptRecord,
    operation_id: str,
    slot: OutputSlotSpec,
    descriptor: StagingOutputDescriptor,
    run_spec: RunSpec,
) -> FinalizedOutput:
    occurrence_id = compute_occurrence_id(run_id, step_name, attempt.attempt_no, descriptor.output_name)
    provisional = registry.get_occurrence(occurrence_id)
    if provisional is None:
        raise FinalizeError(
            f"no provisional Occurrence found for (run_id={run_id!r}, step_name={step_name!r}, "
            f"attempt_no={attempt.attempt_no!r}, output_name={descriptor.output_name!r}); was "
            "the lease acquired?"
        )

    if provisional.status == OccurrenceStatus.COMMITTED:
        # Per-output idempotent replay (e.g. a prior finalize call committed
        # this output but crashed before the Operation record reached
        # `succeeded`): reuse what is already there instead of redoing work.
        asset_version = registry.get_asset_version(provisional.asset_version_id)
        label = registry.get_label(provisional.label_id)
        blob = registry.get_blob(asset_version.components[0].blob_id)
        return FinalizedOutput(descriptor.output_name, provisional, asset_version, label, blob)

    if provisional.status != OccurrenceStatus.PROVISIONAL:
        raise FinalizeError(
            f"Occurrence {occurrence_id.typed!r} is not eligible to finalize "
            f"(status={provisional.status.value!r})"
        )

    winner = registry.get_committed_occurrence_by_output_key(provisional.committed_output_key)
    if winner is not None and winner.occurrence_id != provisional.occurrence_id:
        raise FinalizeError(
            f"output (run_id={run_id!r}, step_name={step_name!r}, output_name="
            f"{descriptor.output_name!r}) is already committed by a different Occurrence "
            f"({winner.occurrence_id.typed!r})"
        )

    try:
        parsed_staging = layout.parse_and_validate(layout.to_uri(descriptor.staging_object))
    except ValueError as exc:
        raise FinalizeError(f"invalid staging object path: {descriptor.staging_object!r}") from exc
    expected_staging_identity = {
        "run_id": run_id,
        "step_name": step_name,
        "attempt_no": str(attempt.attempt_no),
        "operation_id": operation_id,
        "output_name": descriptor.output_name,
    }
    if parsed_staging.kind != "staging" or any(
        parsed_staging.fields.get(key) != value for key, value in expected_staging_identity.items()
    ):
        raise FinalizeError(
            "staging object path is not bound to the current run/step/attempt/operation/output"
        )

    staging_snapshot = gcs.get_object(descriptor.staging_object, generation=descriptor.generation)
    if staging_snapshot.size_bytes != descriptor.size or staging_snapshot.sha256 != descriptor.digest.bare:
        raise FinalizeError(
            f"staging object {descriptor.staging_object!r} at generation "
            f"{descriptor.generation!r} does not match the reported digest/size"
        )

    blob_id = descriptor.digest
    canonical_object_name = layout.canonical_blob(blob_id)
    existing_blob = registry.get_blob(blob_id)
    if existing_blob is not None:
        # Reuse candidate: only re-verify the recorded exact generation still
        # exists, never re-copy (spec 6.5 step 4).
        gcs.get_object(canonical_object_name, generation=existing_blob.generation)
        blob = existing_blob
    else:
        request_digest = TypedId.from_bare(
            digest_sha256_of_jcs(["aigear.blob-claim-request.v2", operation_id, blob_id.typed])
        )
        claim = registry.get_blob_claim(blob_id)
        if claim is None:
            # Direct unit-level use of finalize remains supported; the
            # production entry point always reserves in a prior transaction.
            claim = BlobClaim(
                blob_id=blob_id,
                claim_epoch=1,
                fencing_token=attempt.fencing_token,
                operation_id=operation_id,
                expected_object_name=canonical_object_name,
                request_digest=request_digest,
                state=ClaimState.ADOPTING,
            )
            registry.put_blob_claim(claim)
        elif (
            claim.state != ClaimState.ADOPTING
            or claim.operation_id != operation_id
            or claim.request_digest != request_digest
            or claim.fencing_token != attempt.fencing_token
        ):
            raise FinalizeError(f"Blob {blob_id.typed!r} does not have this operation's adoption claim")

        try:
            canonical_snapshot = gcs.copy_object(
                descriptor.staging_object,
                descriptor.generation,
                canonical_object_name,
                if_generation_match=0,
            )
        except GenerationPreconditionError:
            canonical_snapshot = gcs.get_live_object(canonical_object_name)
            if canonical_snapshot is None:
                raise
            if (
                canonical_snapshot.sha256 != blob_id.bare
                or canonical_snapshot.size_bytes != descriptor.size
            ):
                raise FinalizeError(
                    f"canonical path {canonical_object_name!r} already contains different bytes"
                )
        claim = replace(claim, expected_generation=canonical_snapshot.generation)
        registry.put_blob_claim(claim)
        previous_chain_head = compute_genesis_location_chain_head(
            context.environment_fingerprint, blob_id
        )
        location_attestation = create_attestation(
            schema_version=context.schema_version,
            attestation_kind="blob_location",
            environment_fingerprint=context.environment_fingerprint,
            subject={
                "blob_id": blob_id.typed,
                "location_revision": 1,
                "previous_location_attestation_ref": None,
                "previous_chain_head": previous_chain_head.typed,
                "bucket": layout.bucket_name,
                "object_name": canonical_object_name,
                "generation": canonical_snapshot.generation,
                "sha256": blob_id.bare,
                "crc32c": canonical_snapshot.crc32c,
                "size_bytes": descriptor.size,
                "location_operation_kind": LocationOperationKind.PIPELINE_FINALIZE.value,
                "location_operation_id": operation_id,
                "fencing_token": attempt.fencing_token,
            },
            signer=context.blob_location_signer,
        )
        registry.put_attestation(location_attestation)
        location_attestation_ref = location_attestation.attestation_id
        location_chain_head = compute_location_chain_head(
            previous_chain_head, location_attestation_ref
        )
        blob = BlobRecord(
            schema_version=context.schema_version,
            environment_fingerprint=context.environment_fingerprint,
            blob_id=blob_id,
            sha256=blob_id.bare,
            size_bytes=descriptor.size,
            crc32c=canonical_snapshot.crc32c,
            bucket=layout.bucket_name,
            object_name=canonical_object_name,
            generation=canonical_snapshot.generation,
            current_location_revision=1,
            current_location_attestation_ref=location_attestation_ref,
            location_chain_head=location_chain_head,
            availability_state=AvailabilityState.READY,
        )
        registry.put_blob(blob)
        registry.put_blob_location_revision(
            BlobLocationRevision(
                schema_version=context.schema_version,
                environment_fingerprint=context.environment_fingerprint,
                blob_id=blob_id,
                location_revision=1,
                bucket=layout.bucket_name,
                object_name=canonical_object_name,
                generation=canonical_snapshot.generation,
                sha256=blob_id.bare,
                crc32c=canonical_snapshot.crc32c,
                size_bytes=descriptor.size,
                location_operation_id=operation_id,
                location_operation_kind=LocationOperationKind.PIPELINE_FINALIZE,
                location_attestation_ref=location_attestation_ref,
                location_chain_head=location_chain_head,
                reason="pipeline finalize: first canonical location",
            )
        )
        registry.put_blob_claim(replace(claim, state=ClaimState.CONSUMED))

    component = AssetComponent(
        role=slot.role, blob_id=blob_id, logical_name=slot.logical_name, media_type=descriptor.media_type
    )
    input_bindings = tuple(
        InputBinding(binding_name=binding.binding_name, asset_version_id=binding.asset_version_id)
        for binding in provisional.resolved_input_bindings
    )
    producer_spec = ProducerSpec(
        source_commit=run_spec.code_digest.bare,
        image_digest=run_spec.producer_image_digest,
        code_digest=run_spec.code_digest,
        config_digest=run_spec.config_digest,
    )
    manifest = {
        "environment_id": context.environment_id,
        "environment_fingerprint": context.environment_fingerprint.typed,
        "asset_type": slot.asset_type,
        "name": slot.asset_name,
        "components": [component.to_manifest_dict()],
        "input_bindings": [binding.to_manifest_dict() for binding in input_bindings],
        "producer_spec": producer_spec.to_manifest_dict(),
        "schema_contract_digest": context.schema_contract_digest.typed,
        "runtime_contract_digest": context.runtime_contract_digest.typed,
        "policy_version": context.policy_version,
    }
    asset_version_id = compute_asset_version_id(manifest)
    existing_asset_version = registry.get_asset_version(asset_version_id)
    if existing_asset_version is not None:
        if existing_asset_version.lifecycle_state != LifecycleState.ACTIVE:
            raise FinalizeError(
                f"cannot reuse AssetVersion {asset_version_id.typed!r}: lifecycle_state is "
                f"{existing_asset_version.lifecycle_state.value!r}, not active"
            )
        if existing_asset_version.trust_state == TrustState.REVOKED:
            raise FinalizeError(
                f"cannot reuse AssetVersion {asset_version_id.typed!r}: trust_state is revoked"
            )
        manifest_integrity_attestation_ref = existing_asset_version.manifest_integrity_attestation_ref
        record_revision = existing_asset_version.record_revision + 1
        reference_epoch = existing_asset_version.reference_epoch + 1
        trust_state = existing_asset_version.trust_state
    else:
        manifest_attestation = create_attestation(
            schema_version=context.schema_version,
            attestation_kind="asset_manifest_integrity",
            environment_fingerprint=context.environment_fingerprint,
            subject={
                "asset_version_id": asset_version_id.typed,
                "manifest_digest": asset_version_id.typed,
                "manifest_schema_version": context.schema_version,
            },
            signer=context.manifest_integrity_signer,
        )
        registry.put_attestation(manifest_attestation)
        manifest_integrity_attestation_ref = manifest_attestation.attestation_id
        record_revision = 1
        reference_epoch = 1
        trust_state = TrustState.QUARANTINED

    asset_version = AssetVersionRecord(
        schema_version=context.schema_version,
        environment_id=context.environment_id,
        environment_fingerprint=context.environment_fingerprint,
        asset_version_id=asset_version_id,
        asset_type=slot.asset_type,
        name=slot.asset_name,
        manifest_digest=asset_version_id,
        record_revision=record_revision,
        components=(component,),
        input_bindings=input_bindings,
        producer_spec=producer_spec,
        schema_contract_digest=context.schema_contract_digest,
        runtime_contract_digest=context.runtime_contract_digest,
        lifecycle_state=LifecycleState.ACTIVE,
        trust_state=trust_state,
        policy_version=context.policy_version,
        manifest_integrity_attestation_ref=manifest_integrity_attestation_ref,
        reference_epoch=reference_epoch,
    )
    registry.put_asset_version(asset_version)

    registry.put_component_edge(
        ComponentEdge(
            schema_version=context.schema_version,
            environment_fingerprint=context.environment_fingerprint,
            component_edge_id=compute_component_edge_id(asset_version_id, slot.role, slot.logical_name, blob_id),
            asset_version_id=asset_version_id,
            blob_id=blob_id,
            role=slot.role,
            logical_name=slot.logical_name,
        )
    )

    for binding in provisional.resolved_input_bindings:
        if binding.occurrence_id is None:
            continue
        registry.put_lineage_edge(
            LineageEdge(
                schema_version=context.schema_version,
                environment_fingerprint=context.environment_fingerprint,
                edge_id=compute_lineage_edge_id(occurrence_id, binding.binding_name, binding.occurrence_id),
                output_occurrence_id=occurrence_id,
                input_occurrence_id=binding.occurrence_id,
                binding_name=binding.binding_name,
                run_id=run_id,
            )
        )

    label_id = compute_label_id(slot.asset_type, slot.asset_name, run_id)
    label = LabelRecord(
        schema_version=context.schema_version,
        environment_fingerprint=context.environment_fingerprint,
        label_id=label_id,
        asset_type=slot.asset_type,
        asset_name=slot.asset_name,
        display_version=run_id,
        asset_version_id=asset_version_id,
        projection_source_revision=1,
        readable_manifest=ReadableManifestProjection.initial(
            uri=f"gs://{layout.bucket_name}/{layout.asset_projection(slot.asset_type, slot.asset_name, run_id)}"
        ),
        created_by=attempt.owner_principal or "pipeline-finalizer",
    )
    registry.put_label(label)

    occurrence_attestation = create_attestation(
        schema_version=context.schema_version,
        attestation_kind="occurrence_finalization",
        environment_fingerprint=context.environment_fingerprint,
        subject={
            "producer_kind": "normal_pipeline",
            "occurrence_id": occurrence_id.typed,
            "run_id": run_id,
            "step_name": step_name,
            "attempt_no": attempt.attempt_no,
            "fencing_token": attempt.fencing_token,
            "committed_output_key": provisional.committed_output_key.typed,
            "asset_version_id": asset_version_id.typed,
            "label_id": label_id.typed,
            "resolved_inputs_digest": provisional.resolved_inputs_digest.typed,
            "operation_id": operation_id,
            "firestore_database_id": context.firestore_database_id,
            "firestore_database_resource": context.firestore_database_resource,
            "registry_binding_id": context.registry_binding_id,
            "registry_binding_epoch": context.registry_binding_epoch,
            "write_epoch": context.write_epoch,
            "components": [
                {
                    "component_key": component.component_key.typed,
                    "blob_id": blob_id.typed,
                    "location_revision": blob.current_location_revision,
                    "generation": blob.generation,
                }
            ],
        },
        signer=context.occurrence_finalization_signer,
    )
    registry.put_attestation(occurrence_attestation)

    committed_occurrence = replace(
        provisional,
        status=OccurrenceStatus.COMMITTED,
        asset_version_id=asset_version_id,
        asset_type=slot.asset_type,
        asset_name=slot.asset_name,
        label_id=label_id,
        display_version=run_id,
        finalization_attestation_ref=occurrence_attestation.attestation_id,
        committed_at=context.now.isoformat(),
    )
    registry.put_occurrence(committed_occurrence)

    return FinalizedOutput(descriptor.output_name, committed_occurrence, asset_version, label, blob)


def finalize_step_outputs(
    registry: FakeRegistryV2,
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    completion_message: StepCompletionMessage,
    run_spec: RunSpec,
    context: FinalizeContext,
) -> FinalizeOutcome:
    """Finalize every output in one worker completion message (spec 10.4)."""
    validate_step_completion_message(completion_message, run_spec)

    run_id = completion_message.run_id
    step_name = completion_message.step_name
    attempt_no = completion_message.attempt_no

    run = registry.get_run(run_id)
    if run is None:
        raise FinalizeError(f"no Run registered for run_id {run_id!r}")

    step = registry.get_step(run_id, step_name)
    if step is None:
        raise FinalizeError(f"no Step registered for (run_id={run_id!r}, step_name={step_name!r})")

    attempt = registry.get_attempt(run_id, step_name, attempt_no)
    if attempt is None:
        raise FinalizeError(
            f"no Attempt registered for (run_id={run_id!r}, step_name={step_name!r}, "
            f"attempt_no={attempt_no!r})"
        )
    if step.current_attempt_no != attempt_no:
        raise FinalizeError(
            f"Attempt {attempt_no} is no longer the current attempt for "
            f"(run_id={run_id!r}, step_name={step_name!r}); a later takeover has fenced it out"
        )
    expected_operation_id = compute_attempt_finalize_operation_id(
        run_id, step_name, attempt_no
    ).bare
    if completion_message.operation_id != expected_operation_id:
        raise FinalizeError(
            f"operation_id mismatch: expected {expected_operation_id!r}, "
            f"got {completion_message.operation_id!r}"
        )
    if (
        completion_message.fencing_token is not None
        and completion_message.fencing_token != attempt.fencing_token
    ):
        raise FinalizeError(
            f"completion fencing_token {completion_message.fencing_token!r} does not match "
            f"current Attempt token {attempt.fencing_token!r}"
        )

    step_spec = next((candidate for candidate in run_spec.steps if candidate.step_name == step_name), None)
    if step_spec is None:
        raise FinalizeError(f"RunSpec has no step named {step_name!r}")
    slots_by_output_name = {slot.output_name: slot for slot in step_spec.outputs}

    # Checked before "Run running"/"Attempt eligible" below: a genuinely
    # idempotent replay must still succeed even after the Run/Step/Attempt
    # have already moved on to their terminal `succeeded` status.
    request_fingerprint = _request_fingerprint(completion_message)
    operation_id = completion_message.operation_id
    existing_operation = registry.get_operation(operation_id)
    if (
        existing_operation is not None
        and existing_operation.request_fingerprint == request_fingerprint
        and existing_operation.phase == OperationPhase.SUCCEEDED
    ):
        return _rebuild_outcome(registry, run, step, attempt, completion_message)

    if run.status != RunStatus.RUNNING:
        raise FinalizeError(f"Run {run_id!r} is not running (status={run.status.value!r})")
    if attempt.status not in _ATTEMPT_ELIGIBLE_STATUSES:
        raise FinalizeError(
            f"Attempt (run_id={run_id!r}, step_name={step_name!r}, attempt_no={attempt_no!r}) "
            f"is not eligible to finalize (status={attempt.status.value!r})"
        )

    owner_principal = attempt.owner_principal or "unknown"
    registry.put_operation(
        OperationRecord(
            idempotency_key_hash=operation_id,
            request_fingerprint=request_fingerprint,
            operation_type="pipeline_finalize",
            owner_principal=owner_principal,
            write_epoch=context.write_epoch,
            fencing_token=attempt.fencing_token,
            phase=OperationPhase.FINALIZING,
            revision=1,
            run_id=run_id,
            step_name=step_name,
            attempt_no=attempt_no,
            firestore_database_id=context.firestore_database_id,
            firestore_database_resource=context.firestore_database_resource,
            registry_binding_id=context.registry_binding_id,
            registry_binding_epoch=context.registry_binding_epoch,
        )
    )

    finalized_outputs = tuple(
        _finalize_one_output(
            registry=registry,
            gcs=gcs,
            layout=layout,
            context=context,
            run_id=run_id,
            step_name=step_name,
            attempt=attempt,
            operation_id=operation_id,
            slot=slots_by_output_name[descriptor.output_name],
            descriptor=descriptor,
            run_spec=run_spec,
        )
        for descriptor in completion_message.outputs
    )

    _advance_attempt_to_succeeded(registry, run_id, step_name, attempt_no, attempt.status)
    _advance_step_to_succeeded(registry, run_id, step_name, step.status)
    _advance_run(registry, run, run_spec)

    registry.put_operation(
        OperationRecord(
            idempotency_key_hash=operation_id,
            request_fingerprint=request_fingerprint,
            operation_type="pipeline_finalize",
            owner_principal=owner_principal,
            write_epoch=context.write_epoch,
            fencing_token=attempt.fencing_token,
            phase=OperationPhase.SUCCEEDED,
            revision=2,
            run_id=run_id,
            step_name=step_name,
            attempt_no=attempt_no,
            firestore_database_id=context.firestore_database_id,
            firestore_database_resource=context.firestore_database_resource,
            registry_binding_id=context.registry_binding_id,
            registry_binding_epoch=context.registry_binding_epoch,
        )
    )

    return FinalizeOutcome(
        run=registry.get_run(run_id),
        step=registry.get_step(run_id, step_name),
        attempt=registry.get_attempt(run_id, step_name, attempt_no),
        outputs=finalized_outputs,
    )

"""``PipelineAssetManagement`` — the minimal Pipeline V2 migration entry point.

This is the new entry point spec section 24.1 recommends as the eventual
replacement for :class:`~aigear.management.versioned_asset.VersionedAssetManagement`.
The constructor wires up an ``EnvironmentIdentity``/``environment_fingerprint``
(spec 2.1), an in-memory :class:`~aigear.management.v2.fake_registry.FakeRegistryV2`
and :class:`~aigear.management.v2.fake_gcs.FakeGcsClient` (no real Firestore/GCS
access), and a :class:`~aigear.management.v2.gcs_layout.GcsLayoutV2` derived
from that same identity (its ``asset_bucket_name``/``project_name``/
``pipeline_version`` are exactly ``GcsLayoutV2``'s three fields, so there is
nothing left to ask the caller for separately).

T28 wires every Phase B execution-lifecycle module (T15/T20/T22/T23/T24/T25,
plus this task's own T28 ``resolve_inputs``/``fail_attempt`` modules) behind
spec 24.1's ``begin_run``/``resolve_inputs``/``begin_attempt``/
``finalize_step_outputs``/``fail_attempt``/``cancel_run``/``download_exact``.
Spec 24.1 only gives each method's business-level parameters
(``run_spec``/``idempotency_key``, ``run_id``/``step_name``, ...); it never
says how a caller supplies cross-cutting inputs like the current time,
"who is calling", or (for ``finalize_step_outputs``/``download_exact``) a
schema/runtime/policy pin or a ControlDocument. Rather than guess a default
for values that must reflect the caller's real request, this class takes them
as explicit keyword-only parameters on the relevant methods (``now``,
``owner_principal``, ...) or as optional constructor inputs that a method
raises :class:`PipelineAssetManagementError` for if actually needed but never
supplied (``schema_contract_digest``/``runtime_contract_digest``/
``policy_version`` for ``finalize_step_outputs``; ``control_document`` for
``download_exact``) -- this keeps the zero-config constructor working for
every caller that never touches those two methods.

``begin_run`` is the only place a :class:`~aigear.management.v2.records.
run_spec.RunSpec` is ever supplied; every other method above only takes a
``run_id``, so this class caches the accepted ``RunSpec`` in memory
(``self._run_specs``) and looks it up by ``run_id`` for them. This mirrors
spec 9.2's own assumption that a Run's RunSpec is immutable once accepted.

Out of scope for T28 (spec 24.1's ``upload_asset``/``upload_bundle``/
``import_external``): manual asset ingestion is a distinct workflow (spec
2200: "人工 upload/import CLI 同样创建受审计 operation，由 finalizer 提交"), not
part of the Run/Step/Attempt execution lifecycle this task wires up, and no
Phase B task builds its underlying operation-creation helper. These three
still fail loudly with :class:`NotImplementedError`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from aigear.management.v2 import attempt_fail, resolve_inputs as resolve_inputs_module, run_cancel
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.download import download_exact as _download_blob
from aigear.management.v2.environment import EnvironmentIdentity, compute_environment_fingerprint
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.finalizer import FinalizeContext
from aigear.management.v2.finalizer import finalize_step_outputs as _finalize_step_outputs
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import SHA256_TYPED_PREFIX, TypedId
from aigear.management.v2.records.asset_version import AssetVersionRecord
from aigear.management.v2.records.occurrence import OccurrenceRecord
from aigear.management.v2.records.run import AttemptRecord, RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.records.run_spec import RunSpec, compute_run_spec_digest
from aigear.management.v2.resolver import Selector, UsageContext
from aigear.management.v2.resolver import resolve as _resolve_selector
from aigear.management.v2.run_trigger import begin_run_trigger
from aigear.management.v2.staging_upload import StepCompletionMessage
from aigear.management.v2.step_lease import acquire_step_lease

__all__ = ["PipelineAssetManagementError", "PipelineAssetManagement"]

_NOT_IMPLEMENTED_HINT = (
    "requires Phase B/C execution machinery not yet implemented; see "
    "docs/pipeline-v2-migration-guide.md for the current migration status."
)

# Every RunSpec accepted by begin_run uses this schema_version end to end
# (RunRecord/StepRecord/AttemptRecord/OccurrenceRecord all key off it); no
# Phase B module makes it configurable yet, so it is a module constant here
# too rather than a per-instance guess.
_SCHEMA_VERSION = "2.0"


class PipelineAssetManagementError(ValueError):
    """Raised when a ``PipelineAssetManagement`` call is missing a required,
    caller-supplied cross-cutting input (see module docstring), or names a
    ``run_id``/``step_name`` this instance has no record of."""


def _coerce_typed_id(value: "TypedId | str") -> TypedId:
    if isinstance(value, TypedId):
        return value
    if isinstance(value, str) and value.startswith(SHA256_TYPED_PREFIX):
        return TypedId.from_typed(value)
    return TypedId.from_bare(value)


class PipelineAssetManagement:
    """The V2 migration entry point (spec 24.1).

    Parameters
    ----------
    environment_identity:
        The stable per-environment identity (spec 2.1). Its
        ``environment_fingerprint`` is derived here, once, rather than
        accepted directly, so this class and its registry can never disagree
        about which environment they belong to. Its ``asset_bucket_name``/
        ``project_name``/``pipeline_version`` also fully determine this
        instance's ``GcsLayoutV2``.
    registry, gcs:
        Injected backends; each defaults to a fresh in-memory fake so this
        class is usable standalone (e.g. in tests) without any GCP
        dependency. Real Firestore/GCS backends are out of scope for this
        phase and will replace these defaults in a later phase.
    schema_contract_digest, runtime_contract_digest, policy_version:
        Deployment-level pins ``finalize_step_outputs`` needs (spec 8.2);
        left optional here because most callers of this class never call
        that one method. Supplying only some of the three is treated the
        same as supplying none: ``finalize_step_outputs`` raises
        :class:`PipelineAssetManagementError` unless all three are present.
    control_document:
        The currently bound :class:`~aigear.management.v2.control_document.
        ControlDocument`, needed only by ``download_exact`` (via the
        resolver, T24). Same "all or nothing" reasoning as above: optional
        here, required at call time.
    """

    def __init__(
        self,
        environment_identity: EnvironmentIdentity,
        *,
        registry: Optional[FakeRegistryV2] = None,
        gcs: Optional[FakeGcsClient] = None,
        schema_contract_digest: Optional[TypedId] = None,
        runtime_contract_digest: Optional[TypedId] = None,
        policy_version: Optional[str] = None,
        control_document: Optional[ControlDocument] = None,
    ) -> None:
        self.environment_identity = environment_identity
        self.environment_fingerprint = compute_environment_fingerprint(environment_identity)
        self.registry = registry if registry is not None else FakeRegistryV2()
        self.gcs = gcs if gcs is not None else FakeGcsClient()
        self.layout = GcsLayoutV2(
            bucket_name=environment_identity.asset_bucket_name,
            project_name=environment_identity.project_name,
            pipeline_version=environment_identity.pipeline_version,
        )
        self.schema_contract_digest = schema_contract_digest
        self.runtime_contract_digest = runtime_contract_digest
        self.policy_version = policy_version
        self.control_document = control_document
        self._run_specs: Dict[str, RunSpec] = {}

    # ── internal lookups ─────────────────────────────────────────────────

    def _require_run_spec(self, run_id: str) -> RunSpec:
        run_spec = self._run_specs.get(run_id)
        if run_spec is None:
            raise PipelineAssetManagementError(
                f"no RunSpec cached for run_id {run_id!r}; it must have been accepted by "
                "begin_run on this same PipelineAssetManagement instance first"
            )
        return run_spec

    def _require_finalize_context(self, now: datetime) -> FinalizeContext:
        missing = [
            name
            for name, value in (
                ("schema_contract_digest", self.schema_contract_digest),
                ("runtime_contract_digest", self.runtime_contract_digest),
                ("policy_version", self.policy_version),
            )
            if value is None
        ]
        if missing:
            raise PipelineAssetManagementError(
                "finalize_step_outputs requires "
                f"{', '.join(missing)} to be set on this PipelineAssetManagement instance"
            )
        return FinalizeContext(
            environment_id=self.environment_identity.environment_id,
            environment_fingerprint=self.environment_fingerprint,
            schema_version=_SCHEMA_VERSION,
            schema_contract_digest=self.schema_contract_digest,
            runtime_contract_digest=self.runtime_contract_digest,
            policy_version=self.policy_version,
            now=now,
        )

    def _require_control_document(self) -> ControlDocument:
        if self.control_document is None:
            raise PipelineAssetManagementError(
                "download_exact requires control_document to be set on this "
                "PipelineAssetManagement instance"
            )
        return self.control_document

    # ── read-only lookups (implemented) ─────────────────────────────────

    def get_asset(self, asset_version_id: "TypedId | str") -> Optional[AssetVersionRecord]:
        """Look up an AssetVersion by its exact ID (spec 24.1: ``PipelineAssetRegistryV2.get_asset``)."""
        return self.registry.get_asset_version(_coerce_typed_id(asset_version_id))

    def get_occurrence(self, occurrence_id: "TypedId | str") -> Optional[OccurrenceRecord]:
        """Look up an Occurrence by its exact ID (spec 24.1: ``PipelineAssetRegistryV2.get_occurrence``)."""
        return self.registry.get_occurrence(_coerce_typed_id(occurrence_id))

    # ── execution lifecycle ──────────────────────────────────────────────

    def begin_run(self, run_spec: RunSpec, idempotency_key: "TypedId | str", *, owner_principal: str) -> RunRecord:
        """Idempotently trigger a new Run for ``run_spec`` (spec 10.1, T15).

        ``request_fingerprint`` is ``run_spec``'s own digest (T28's
        ``compute_run_spec_digest``): a redelivered ``idempotency_key`` with
        the *same* RunSpec content replays the already-created Run; the
        *same* key with different RunSpec content is a hard
        ``RunTriggerIdempotencyConflict``, exactly spec 10.1's rule.
        """
        run_spec_digest = compute_run_spec_digest(run_spec)

        def _create_run() -> str:
            run_id = f"run-{uuid.uuid4().hex}"
            self.registry.create_run(
                RunRecord(
                    run_id=run_id,
                    status=RunStatus.PENDING,
                    run_spec_digest=run_spec_digest,
                    remaining_required_steps=len(run_spec.steps),
                )
            )
            for step_spec in run_spec.steps:
                initial_status = StepStatus.BLOCKED if step_spec.dependencies else StepStatus.READY
                self.registry.create_step(
                    StepRecord(run_id=run_id, step_name=step_spec.step_name, status=initial_status)
                )
            self.registry.update_run_status(run_id, RunStatus.RUNNING)
            return run_id

        operation = begin_run_trigger(
            self.registry,
            idempotency_key=_coerce_typed_id(idempotency_key),
            request_fingerprint=run_spec_digest.typed,
            owner_principal=owner_principal,
            create_run=_create_run,
        )
        self._run_specs[operation.run_id] = run_spec
        return self.registry.get_run(operation.run_id)

    def resolve_inputs(self, run_id: str, step_name: str, *, now: datetime) -> StepRecord:
        """Resolve ``step_name``'s declared dependencies into exact upstream
        Occurrences (spec 9.2, T28's ``resolve_inputs`` module)."""
        run_spec = self._require_run_spec(run_id)
        return resolve_inputs_module.resolve_step_inputs(
            self.registry, run_spec, run_id=run_id, step_name=step_name, now=now
        )

    def begin_attempt(
        self, run_id: str, step_name: str, *, owner_principal: str, now: datetime
    ) -> AttemptRecord:
        """Acquire (or take over) ``step_name``'s lease and start a new Attempt
        (spec 10.3, T20)."""
        run_spec = self._require_run_spec(run_id)
        step_specs_by_name = {spec.step_name: spec for spec in run_spec.steps}
        step_spec = step_specs_by_name.get(step_name)
        if step_spec is None:
            raise PipelineAssetManagementError(f"RunSpec for run_id {run_id!r} has no Step named {step_name!r}")
        step = self.registry.get_step(run_id, step_name)
        if step is None:
            raise PipelineAssetManagementError(
                f"no Step registered for (run_id={run_id!r}, step_name={step_name!r})"
            )
        return acquire_step_lease(
            self.registry,
            run_id=run_id,
            step_name=step_name,
            output_names=[output.output_name for output in step_spec.outputs],
            resolved_input_bindings=step.resolved_inputs,
            environment_fingerprint=self.environment_fingerprint,
            schema_version=_SCHEMA_VERSION,
            owner_principal=owner_principal,
            now=now,
        )

    def finalize_step_outputs(self, step_completion: StepCompletionMessage, *, now: datetime):
        """Finalize every output in one worker completion message (spec 10.4, T22)."""
        run_spec = self._require_run_spec(step_completion.run_id)
        context = self._require_finalize_context(now)
        return _finalize_step_outputs(self.registry, self.gcs, self.layout, step_completion, run_spec, context)

    def fail_attempt(
        self,
        run_id: str,
        step_name: str,
        attempt_no: int,
        *,
        fencing_token: int,
        retryable: bool,
        reason: str,
    ) -> StepRecord:
        """Report that an Attempt failed (spec 24.1, T28's ``attempt_fail`` module)."""
        return attempt_fail.fail_attempt(
            self.registry,
            run_id=run_id,
            step_name=step_name,
            attempt_no=attempt_no,
            fencing_token=fencing_token,
            retryable=retryable,
            reason=reason,
        )

    def cancel_run(self, run_id: str, reason: str) -> RunRecord:
        """Cancel every non-terminal Step of ``run_id`` (spec 10.5, T23)."""
        run_spec = self._require_run_spec(run_id)
        step_names = [step_spec.step_name for step_spec in run_spec.steps]
        return run_cancel.cancel_run(self.registry, run_id=run_id, step_names=step_names, reason=reason)

    # ── asset ingestion (not yet implemented) ───────────────────────────

    def upload_asset(self, *args, **kwargs):
        raise NotImplementedError(f"PipelineAssetManagement.upload_asset {_NOT_IMPLEMENTED_HINT}")

    def upload_bundle(self, *args, **kwargs):
        raise NotImplementedError(
            f"PipelineAssetManagement.upload_bundle {_NOT_IMPLEMENTED_HINT}"
        )

    def import_external(self, *args, **kwargs):
        raise NotImplementedError(
            f"PipelineAssetManagement.import_external {_NOT_IMPLEMENTED_HINT}"
        )

    def download_exact(
        self,
        asset_version_id: "TypedId | str | None" = None,
        occurrence_id: "TypedId | str | None" = None,
        *,
        target_path: Path,
        now: datetime,
        usage_context: UsageContext = UsageContext.MANUAL_DOWNLOAD,
        max_size_bytes: Optional[int] = None,
    ) -> Path:
        """Resolve then download a single Blob to ``target_path`` (spec 6.4/6.6, T24/T25).

        Exactly one of ``asset_version_id``/``occurrence_id`` must be given:
        ``occurrence_id`` resolves the exact committed Occurrence (its
        as-of-then sealed AssetVersion/Label); ``asset_version_id`` resolves
        the raw AssetVersion directly, with no Occurrence/Label involved.
        These are the resolver's two "exact identity" selector branches
        (``Selector.by_occurrence``/``by_asset_version``); the other two
        (asset-label, run-output) need extra name parameters this method's
        two-positional-argument spec signature has no room for.
        """
        if (asset_version_id is None) == (occurrence_id is None):
            raise PipelineAssetManagementError(
                "download_exact requires exactly one of asset_version_id/occurrence_id"
            )
        control_document = self._require_control_document()
        if occurrence_id is not None:
            selector = Selector.by_occurrence(_coerce_typed_id(occurrence_id))
        else:
            selector = Selector.by_asset_version(_coerce_typed_id(asset_version_id))

        handle = _resolve_selector(self.registry, control_document, self.layout, selector, usage_context, now)
        return _download_blob(handle, self.gcs, target_path, now=now, max_size_bytes=max_size_bytes)

"""Symbolic dependency resolution for a Step's declared inputs (spec section 9.2).

Implements the part of spec 9.2 that reads: "下游 Step 变为 ready 时，在
Firestore 事务中把符号依赖解析为成功上游 Step 的精确 Occurrence，并保存：
resolved_inputs / resolved_inputs_digest / resolved_at / source_step_revision".
For each of a Step's declared ``dependencies`` (upstream step names, from its
own ``StepSpec``), this resolves every one of that upstream Step's declared
output slots to its exact committed Occurrence, seals the resulting
``ResolvedInputBinding`` tuple onto the Step, and moves it ``blocked -> ready``.

``binding_name`` for one of these auto-resolved edges is
``f"{dependency_step_name}.{output_name}"``: ``StepSpec.dependencies`` (T14)
only names upstream *Steps*, not a binding name per output (unlike
``SeedInputBinding``, which is caller-named), so this module has to invent
one -- and that format is guaranteed unique across every dependency's
outputs, since ``output_name`` is already unique within its own Step and
``step_name`` is unique within the RunSpec (both enforced by T14).

Deliberately out of scope: cycle detection and "does a declared dependency
actually name a Step in this RunSpec" validation -- T14's docstring already
scopes both out of ``RunSpec`` itself, so a :class:`ResolveInputsError` is
raised here instead if a dependency cannot be resolved. A Step whose
dependency has not committed its output yet is simply not eligible; like
``acquire_step_lease`` (T20), deciding when to retry is the caller's
(a controller loop's) job, not this function's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from aigear.management.v2.attestation import AttestationVerifier
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    ResolvedInputBinding,
    compute_committed_output_key,
    compute_resolved_inputs_digest,
)
from aigear.management.v2.records.run import StepRecord, StepStatus
from aigear.management.v2.records.run_spec import RunSpec, seed_inputs_for_step
from aigear.management.v2.resolver import ResolverError, validate_same_run_upstream

__all__ = ["ResolveInputsError", "ResolveInputsStore", "resolve_step_inputs"]


class ResolveInputsError(ValueError):
    """Raised when a Step's declared dependencies cannot be resolved as requested."""


class ResolveInputsStore(Protocol):
    def get_step(self, run_id: str, step_name: str) -> Optional[StepRecord]: ...

    def update_step_status(
        self, run_id: str, step_name: str, target_status: StepStatus, **field_updates
    ) -> StepRecord: ...

    def get_committed_occurrence_by_output_key(
        self, committed_output_key: TypedId
    ) -> Optional[OccurrenceRecord]: ...


def resolve_step_inputs(
    store: ResolveInputsStore,
    run_spec: RunSpec,
    *,
    run_id: str,
    step_name: str,
    now: datetime,
    layout: Optional[GcsLayoutV2] = None,
    attestation_verifier: Optional[AttestationVerifier] = None,
) -> StepRecord:
    """Resolve ``step_name``'s declared dependencies into exact upstream
    Occurrences and move it ``blocked -> ready``.

    Idempotent: a Step that is already ``ready`` with ``resolved_inputs``
    already sealed is returned unchanged (spec 9.1: "同一 Step 的 retry 必须
    复用相同 resolved_inputs_digest").
    """
    step = store.get_step(run_id, step_name)
    if step is None:
        raise ResolveInputsError(
            f"no Step registered for (run_id={run_id!r}, step_name={step_name!r})"
        )
    if step.status == StepStatus.READY and step.resolved_inputs_digest is not None:
        return step
    if step.status != StepStatus.BLOCKED:
        raise ResolveInputsError(
            f"Step (run_id={run_id!r}, step_name={step_name!r}) is not eligible for input "
            f"resolution (status={step.status.value!r})"
        )

    step_specs_by_name = {spec.step_name: spec for spec in run_spec.steps}
    this_step_spec = step_specs_by_name.get(step_name)
    if this_step_spec is None:
        raise ResolveInputsError(f"RunSpec has no StepSpec named {step_name!r}")

    bindings = [
        ResolvedInputBinding(
            binding_name=seed.binding_name,
            asset_version_id=seed.asset_version_id,
            occurrence_id=seed.occurrence_id,
            source_label_id=seed.source_label_id,
        )
        for seed in seed_inputs_for_step(run_spec, this_step_spec)
    ]
    for dependency_step_name in this_step_spec.dependencies:
        dependency_spec = step_specs_by_name.get(dependency_step_name)
        if dependency_spec is None:
            raise ResolveInputsError(
                f"Step {step_name!r} declares dependency {dependency_step_name!r}, which has no "
                "matching StepSpec in this RunSpec"
            )
        for output in dependency_spec.outputs:
            committed_output_key = compute_committed_output_key(
                run_id, dependency_step_name, output.output_name
            )
            occurrence = store.get_committed_occurrence_by_output_key(committed_output_key)
            if occurrence is None:
                raise ResolveInputsError(
                    f"dependency {dependency_step_name!r} output {output.output_name!r} has no "
                    f"committed Occurrence yet; Step {step_name!r} is not ready to resolve"
                )
            try:
                validate_same_run_upstream(
                    store,
                    occurrence,
                    layout=layout,
                    attestation_verifier=attestation_verifier,
                )
            except ResolverError as exc:
                raise ResolveInputsError(
                    f"dependency {dependency_step_name!r} output {output.output_name!r} "
                    f"is not eligible: {exc}"
                ) from exc
            bindings.append(
                ResolvedInputBinding(
                    binding_name=f"{dependency_step_name}.{output.output_name}",
                    asset_version_id=occurrence.asset_version_id,
                    occurrence_id=occurrence.occurrence_id,
                    source_label_id=occurrence.label_id,
                )
            )

    sealed_bindings = tuple(sorted(bindings, key=lambda binding: binding.binding_name))
    return store.update_step_status(
        run_id,
        step_name,
        StepStatus.READY,
        resolved_inputs=sealed_bindings,
        resolved_inputs_digest=compute_resolved_inputs_digest(sealed_bindings),
        resolved_at=now.isoformat(),
        source_step_revision=(step.source_step_revision or 0) + 1,
    )

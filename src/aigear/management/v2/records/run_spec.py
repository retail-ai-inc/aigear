"""``RunSpec`` and its fail-fast validation (spec section 9.2).

Everything a Run must fix at creation time -- trigger identity, code/config/
graph/producer digests, seed inputs and the declared Step/output graph -- is
modeled here as plain, self-validating dataclasses. Per spec 9.2, ``begin_run``
must reject a malformed graph *before* creating the Run: duplicate
``step_name``, duplicate ``output_name`` within a Step, duplicate
``(role, logical_name)`` within a Step, or a local-bundle-layout path
collision among a Step's outputs (spec 6.7: two outputs sharing a
``role``/``logical_name`` pair that only differ by case/trailing dot). All of
that is enforced eagerly in ``__post_init__``, matching every other Phase A
record type in this package.

The dependency graph is validated eagerly.  Unknown dependencies,
self-dependencies and cycles are deployment errors and must fail before a Run
or worker resource is created.  ``resolved_inputs`` remains a runtime concern,
but the symbolic graph it resolves is immutable and must already be sound.

``retry_policy`` and ``cancel_policy`` remain JSON objects for wire
compatibility, but their currently supported keys are closed and validated.
Unknown keys fail closed instead of being silently ignored by different
controller versions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import ensure_no_collisions, validate_segment
from aigear.management.v2.records.asset_version import compute_component_key

__all__ = [
    "InvalidRunSpecError",
    "SeedInputBinding",
    "ComponentSlotSpec",
    "AttachmentSlotSpec",
    "OutputSlotSpec",
    "StepSpec",
    "RunSpec",
    "compute_run_spec_digest",
    "estimate_step_finalize_writes",
    "seed_inputs_for_step",
]

DEFAULT_MAX_FINALIZE_WRITES = 400
FIRESTORE_TRANSACTION_WRITE_LIMIT = 500
MAX_COMPONENTS_PER_OUTPUT = 32
MAX_ATTACHMENTS_PER_OUTPUT = 32


class InvalidRunSpecError(ValueError):
    """Raised for a malformed RunSpec field or a fail-fast uniqueness/collision violation (spec 9.2)."""


_RETRY_POLICY_KEYS = frozenset(
    {
        "max_attempts",
        "initial_backoff_seconds",
        "max_backoff_seconds",
        "backoff_multiplier",
        "retryable_error_classes",
    }
)
_CANCEL_POLICY_KEYS = frozenset({"grace_period_seconds", "force_after_seconds"})


def _require_number(field_name: str, value: object, *, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise InvalidRunSpecError(f"{field_name} must be a number >= {minimum}, got {value!r}")
    return float(value)


def _validate_retry_policy(policy: Dict) -> None:
    unknown = set(policy) - _RETRY_POLICY_KEYS
    if unknown:
        raise InvalidRunSpecError(f"retry_policy contains unsupported keys: {sorted(unknown)!r}")
    if "max_attempts" in policy:
        value = policy["max_attempts"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InvalidRunSpecError(f"retry_policy.max_attempts must be a positive int, got {value!r}")
    initial = _require_number(
        "retry_policy.initial_backoff_seconds",
        policy.get("initial_backoff_seconds", 1),
        minimum=0,
    )
    maximum = _require_number(
        "retry_policy.max_backoff_seconds",
        policy.get("max_backoff_seconds", max(initial, 1)),
        minimum=0,
    )
    if maximum < initial:
        raise InvalidRunSpecError(
            "retry_policy.max_backoff_seconds must be >= initial_backoff_seconds"
        )
    _require_number(
        "retry_policy.backoff_multiplier",
        policy.get("backoff_multiplier", 2),
        minimum=1,
    )
    retryable = policy.get("retryable_error_classes", ())
    if not isinstance(retryable, (list, tuple)) or not all(
        isinstance(item, str) and item for item in retryable
    ):
        raise InvalidRunSpecError(
            "retry_policy.retryable_error_classes must be a sequence of non-empty strings"
        )


def _validate_cancel_policy(policy: Dict) -> None:
    unknown = set(policy) - _CANCEL_POLICY_KEYS
    if unknown:
        raise InvalidRunSpecError(f"cancel_policy contains unsupported keys: {sorted(unknown)!r}")
    grace = _require_number(
        "cancel_policy.grace_period_seconds",
        policy.get("grace_period_seconds", 30),
        minimum=0,
    )
    force_after = _require_number(
        "cancel_policy.force_after_seconds",
        policy.get("force_after_seconds", max(grace, 30)),
        minimum=0,
    )
    if force_after < grace:
        raise InvalidRunSpecError(
            "cancel_policy.force_after_seconds must be >= grace_period_seconds"
        )


def _validate_dependency_graph(steps: Tuple["StepSpec", ...]) -> None:
    names = {step.step_name for step in steps}
    graph = {step.step_name: tuple(step.dependencies) for step in steps}
    for step_name, dependencies in graph.items():
        for dependency in dependencies:
            if dependency == step_name:
                raise InvalidRunSpecError(f"step {step_name!r} must not depend on itself")
            if dependency not in names:
                raise InvalidRunSpecError(
                    f"step {step_name!r} depends on unknown step {dependency!r}"
                )

    visiting = set()
    visited = set()

    def visit(step_name: str, path: Tuple[str, ...]) -> None:
        if step_name in visited:
            return
        if step_name in visiting:
            cycle_start = path.index(step_name)
            cycle = path[cycle_start:] + (step_name,)
            raise InvalidRunSpecError(f"RunSpec dependency cycle detected: {' -> '.join(cycle)}")
        visiting.add(step_name)
        for dependency in graph[step_name]:
            visit(dependency, path + (step_name,))
        visiting.remove(step_name)
        visited.add(step_name)

    for step_name in sorted(names):
        visit(step_name, ())


def _require_non_empty_str(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidRunSpecError(f"{field_name} must be a non-empty str, got {value!r}")


def _require_typed_id(field_name: str, value: object) -> None:
    if not isinstance(value, TypedId):
        raise InvalidRunSpecError(f"{field_name} must be a TypedId, got {type(value)!r}")


def _ensure_unique(values, *, field_name: str) -> None:
    """Reject exact duplicates, then reject near-duplicates that would collide
    once normalized (``ensure_no_collisions`` alone permits exact repeats)."""
    values = list(values)
    if len(set(values)) != len(values):
        raise InvalidRunSpecError(f"{field_name} values must be unique, got {values!r}")
    ensure_no_collisions(values, field_name=field_name)


@dataclass(frozen=True)
class SeedInputBinding:
    """A Run's fixed seed input: an exact AssetVersion, not a query (spec 9.2/9.3)."""

    binding_name: str
    asset_version_id: TypedId
    occurrence_id: Optional[TypedId] = None
    source_label_id: Optional[TypedId] = None
    consumer_steps: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "binding_name", validate_segment(self.binding_name, field_name="binding_name")
        )
        _require_typed_id("asset_version_id", self.asset_version_id)
        if self.occurrence_id is not None:
            _require_typed_id("occurrence_id", self.occurrence_id)
        if self.source_label_id is not None:
            _require_typed_id("source_label_id", self.source_label_id)
        if isinstance(self.consumer_steps, list):
            object.__setattr__(self, "consumer_steps", tuple(self.consumer_steps))
        object.__setattr__(
            self,
            "consumer_steps",
            tuple(
                validate_segment(value, field_name="seed consumer_step")
                for value in self.consumer_steps
            ),
        )
        _ensure_unique(self.consumer_steps, field_name="seed consumer_steps")

    def to_digest_dict(self) -> dict:
        result = {
            "binding_name": self.binding_name,
            "asset_version_id": self.asset_version_id.typed,
            "occurrence_id": self.occurrence_id.typed if self.occurrence_id is not None else None,
            "source_label_id": self.source_label_id.typed if self.source_label_id is not None else None,
        }
        if self.consumer_steps:
            result["consumer_steps"] = list(self.consumer_steps)
        return result


@dataclass(frozen=True)
class ComponentSlotSpec:
    """One additional identity component declared by an output bundle."""

    role: str
    logical_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", validate_segment(self.role, field_name="role"))
        object.__setattr__(
            self,
            "logical_name",
            validate_segment(self.logical_name, field_name="logical_name"),
        )

    @property
    def component_key(self) -> TypedId:
        return compute_component_key(self.role, self.logical_name)

    def to_digest_dict(self) -> dict:
        return {"role": self.role, "logical_name": self.logical_name}


@dataclass(frozen=True)
class AttachmentSlotSpec:
    """One non-identity attachment declared by an output occurrence."""

    attachment_kind: str
    logical_name: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attachment_kind",
            validate_segment(self.attachment_kind, field_name="attachment_kind"),
        )
        object.__setattr__(
            self,
            "logical_name",
            validate_segment(self.logical_name, field_name="logical_name"),
        )

    def to_digest_dict(self) -> dict:
        return {
            "attachment_kind": self.attachment_kind,
            "logical_name": self.logical_name,
        }


@dataclass(frozen=True)
class OutputSlotSpec:
    """One declared, required output of a Step. All V2 outputs are required (spec 9.2).

    ``asset_type``/``asset_name`` name the AssetVersion/Label this output's
    winning Occurrence is finalized into (spec 10.4/8.2/8.3). Spec 9.2's
    RunSpec field list has no dedicated naming field for this -- only
    ``role``/``logical_name`` (a *component's* identity within a bundle, spec
    5.3) -- so they default to ``role``/``logical_name`` when not given
    explicitly, which is exactly right for the common case of a single-file
    output whose one component *is* the whole asset.
    """

    output_name: str
    role: str
    logical_name: str
    asset_type: Optional[str] = None
    asset_name: Optional[str] = None
    additional_components: Tuple[ComponentSlotSpec, ...] = ()
    attachments: Tuple[AttachmentSlotSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "output_name", validate_segment(self.output_name, field_name="output_name")
        )
        object.__setattr__(self, "role", validate_segment(self.role, field_name="role"))
        object.__setattr__(
            self, "logical_name", validate_segment(self.logical_name, field_name="logical_name")
        )
        object.__setattr__(
            self,
            "asset_type",
            validate_segment(self.asset_type or self.role, field_name="asset_type"),
        )
        object.__setattr__(
            self,
            "asset_name",
            validate_segment(self.asset_name or self.logical_name, field_name="asset_name"),
        )
        if isinstance(self.additional_components, list):
            object.__setattr__(self, "additional_components", tuple(self.additional_components))
        if isinstance(self.attachments, list):
            object.__setattr__(self, "attachments", tuple(self.attachments))
        if not all(isinstance(item, ComponentSlotSpec) for item in self.additional_components):
            raise InvalidRunSpecError(
                "additional_components must contain only ComponentSlotSpec values"
            )
        if not all(isinstance(item, AttachmentSlotSpec) for item in self.attachments):
            raise InvalidRunSpecError("attachments must contain only AttachmentSlotSpec values")
        if 1 + len(self.additional_components) > MAX_COMPONENTS_PER_OUTPUT:
            raise InvalidRunSpecError(
                f"an output may declare at most {MAX_COMPONENTS_PER_OUTPUT} components"
            )
        if len(self.attachments) > MAX_ATTACHMENTS_PER_OUTPUT:
            raise InvalidRunSpecError(
                f"an output may declare at most {MAX_ATTACHMENTS_PER_OUTPUT} attachments"
            )
        component_keys = [(self.role, self.logical_name)] + [
            (item.role, item.logical_name) for item in self.additional_components
        ]
        if len(set(component_keys)) != len(component_keys):
            raise InvalidRunSpecError(
                "output components must be unique by (role, logical_name)"
            )
        roles = [role for role, _logical_name in component_keys]
        ensure_no_collisions(roles, field_name=f"output {self.output_name!r} component role")
        for role in set(roles):
            ensure_no_collisions(
                [logical_name for item_role, logical_name in component_keys if item_role == role],
                field_name=(
                    f"output {self.output_name!r} role {role!r} component logical_name"
                ),
            )
        attachment_keys = [
            (item.attachment_kind, item.logical_name) for item in self.attachments
        ]
        if len(set(attachment_keys)) != len(attachment_keys):
            raise InvalidRunSpecError(
                "output attachments must be unique by (attachment_kind, logical_name)"
            )

    @property
    def component_key(self) -> TypedId:
        """Deterministic component key derived from ``(role, logical_name)`` (spec 5.3)."""
        return compute_component_key(self.role, self.logical_name)

    def to_digest_dict(self) -> dict:
        result = {
            "output_name": self.output_name,
            "role": self.role,
            "logical_name": self.logical_name,
            "asset_type": self.asset_type,
            "asset_name": self.asset_name,
        }
        # Preserve the digest of pre-bundle RunSpecs when both collections
        # are empty; only the new capability adds new canonical fields.
        if self.additional_components:
            result["additional_components"] = [
                item.to_digest_dict() for item in self.additional_components
            ]
        if self.attachments:
            result["attachments"] = [item.to_digest_dict() for item in self.attachments]
        return result

    @property
    def declared_components(self) -> Tuple[ComponentSlotSpec, ...]:
        return (ComponentSlotSpec(self.role, self.logical_name),) + self.additional_components


@dataclass(frozen=True)
class StepSpec:
    """One Step's declared dependencies and required outputs."""

    step_name: str
    dependencies: Tuple[str, ...] = ()
    outputs: Tuple[OutputSlotSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "step_name", validate_segment(self.step_name, field_name="step_name")
        )
        if isinstance(self.dependencies, list):
            object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if isinstance(self.outputs, list):
            object.__setattr__(self, "outputs", tuple(self.outputs))

        object.__setattr__(
            self,
            "dependencies",
            tuple(
                validate_segment(dep, field_name=f"step {self.step_name!r} dependency")
                for dep in self.dependencies
            ),
        )

        if not self.outputs or not all(
            isinstance(output, OutputSlotSpec) for output in self.outputs
        ):
            raise InvalidRunSpecError(
                f"step {self.step_name!r} must declare at least one OutputSlotSpec output"
            )
        _ensure_unique(
            (output.output_name for output in self.outputs),
            field_name=f"step {self.step_name!r} output_name",
        )
        # Same-role logical_name collisions must be rejected up front (spec 6.7's
        # local bundle layout is `<target>/<role>/<logical_name>`); different
        # roles may reuse the same logical_name safely, so collisions are only
        # checked within each role group. Uniqueness of (role, logical_name)
        # also guarantees uniqueness of the derived component_key, since it is
        # a pure function of that pair.
        by_role: Dict[str, list] = {}
        for output in self.outputs:
            by_role.setdefault(output.role, []).append(output.logical_name)
        for role, logical_names in by_role.items():
            _ensure_unique(
                logical_names,
                field_name=f"step {self.step_name!r} role {role!r} logical_name",
            )

    def to_digest_dict(self) -> dict:
        return {
            "step_name": self.step_name,
            "dependencies": list(self.dependencies),
            "outputs": [output.to_digest_dict() for output in self.outputs],
        }


@dataclass(frozen=True)
class RunSpec:
    """Everything a Run must fix at creation time (spec 9.2)."""

    trigger_principal: str
    trigger_source: str
    graph_digest: TypedId
    code_digest: TypedId
    config_digest: TypedId
    producer_image_digest: TypedId
    steps: Tuple[StepSpec, ...]
    seed_inputs: Tuple[SeedInputBinding, ...] = ()
    scheduled_for: Optional[str] = None
    retry_policy: Dict = field(default_factory=dict)
    cancel_policy: Dict = field(default_factory=dict)
    max_finalize_writes: int = DEFAULT_MAX_FINALIZE_WRITES

    def __post_init__(self) -> None:
        if isinstance(self.steps, list):
            object.__setattr__(self, "steps", tuple(self.steps))
        if isinstance(self.seed_inputs, list):
            object.__setattr__(self, "seed_inputs", tuple(self.seed_inputs))

        _require_non_empty_str("trigger_principal", self.trigger_principal)
        _require_non_empty_str("trigger_source", self.trigger_source)
        _require_typed_id("graph_digest", self.graph_digest)
        _require_typed_id("code_digest", self.code_digest)
        _require_typed_id("config_digest", self.config_digest)
        _require_typed_id("producer_image_digest", self.producer_image_digest)

        if not self.steps or not all(isinstance(step, StepSpec) for step in self.steps):
            raise InvalidRunSpecError("steps must be a non-empty sequence of StepSpec")
        _ensure_unique((step.step_name for step in self.steps), field_name="step_name")
        _validate_dependency_graph(self.steps)

        if not all(isinstance(binding, SeedInputBinding) for binding in self.seed_inputs):
            raise InvalidRunSpecError("seed_inputs must be a sequence of SeedInputBinding")
        _ensure_unique(
            (binding.binding_name for binding in self.seed_inputs),
            field_name="seed_inputs binding_name",
        )
        step_names = {step.step_name for step in self.steps}
        for binding in self.seed_inputs:
            unknown_consumers = set(binding.consumer_steps) - step_names
            if unknown_consumers:
                raise InvalidRunSpecError(
                    f"seed input {binding.binding_name!r} targets unknown steps: "
                    f"{sorted(unknown_consumers)!r}"
                )

        if self.scheduled_for is not None:
            _require_non_empty_str("scheduled_for", self.scheduled_for)
        if not isinstance(self.retry_policy, dict):
            raise InvalidRunSpecError(f"retry_policy must be a dict, got {type(self.retry_policy)!r}")
        if not isinstance(self.cancel_policy, dict):
            raise InvalidRunSpecError(
                f"cancel_policy must be a dict, got {type(self.cancel_policy)!r}"
            )
        _validate_retry_policy(self.retry_policy)
        _validate_cancel_policy(self.cancel_policy)
        if (
            isinstance(self.max_finalize_writes, bool)
            or not isinstance(self.max_finalize_writes, int)
            or not (1 <= self.max_finalize_writes < FIRESTORE_TRANSACTION_WRITE_LIMIT)
        ):
            raise InvalidRunSpecError(
                "max_finalize_writes must be an int in [1, 499]"
            )
        for step in self.steps:
            estimated = estimate_step_finalize_writes(step, self)
            if estimated > self.max_finalize_writes:
                raise InvalidRunSpecError(
                    f"step {step.step_name!r} worst-case finalize requires {estimated} writes, "
                    f"exceeding max_finalize_writes={self.max_finalize_writes}"
                )

    def to_digest_dict(self) -> dict:
        result = {
            "trigger_principal": self.trigger_principal,
            "trigger_source": self.trigger_source,
            "graph_digest": self.graph_digest.typed,
            "code_digest": self.code_digest.typed,
            "config_digest": self.config_digest.typed,
            "producer_image_digest": self.producer_image_digest.typed,
            "steps": [step.to_digest_dict() for step in self.steps],
            "seed_inputs": [binding.to_digest_dict() for binding in self.seed_inputs],
            "scheduled_for": self.scheduled_for,
            "retry_policy": self.retry_policy,
            "cancel_policy": self.cancel_policy,
        }
        if self.max_finalize_writes != DEFAULT_MAX_FINALIZE_WRITES:
            result["max_finalize_writes"] = self.max_finalize_writes
        return result


def seed_inputs_for_step(run_spec: RunSpec, step: StepSpec) -> Tuple[SeedInputBinding, ...]:
    """Return explicit seed consumers; legacy empty consumer lists target roots."""
    return tuple(
        binding
        for binding in run_spec.seed_inputs
        if step.step_name in binding.consumer_steps
        or (not binding.consumer_steps and not step.dependencies)
    )


def estimate_step_finalize_writes(
    step: StepSpec, run_spec: Optional[RunSpec] = None
) -> int:
    """Conservative worst-case Firestore writes for one finalize transaction.

    Each new payload can create Blob, location revision and location
    attestation, consume its adoption claim, and create one reverse edge.
    Each output additionally creates manifest/finalization attestations,
    AssetVersion, Label, Occurrence/binding and two projection outbox events.
    Four shared writes cover Operation, Run, Step and Attempt.
    """
    shared_writes = 4
    lineage_writes = 0
    if run_spec is not None:
        by_name = {candidate.step_name: candidate for candidate in run_spec.steps}
        lineage_writes = len(seed_inputs_for_step(run_spec, step)) + sum(
            len(by_name[dependency].outputs) for dependency in step.dependencies
        )
    output_writes = 0
    for output in step.outputs:
        component_count = 1 + len(output.additional_components)
        attachment_count = len(output.attachments)
        payload_and_edge_writes = 5 * (component_count + attachment_count)
        fixed_output_writes = 10
        output_writes += payload_and_edge_writes + fixed_output_writes
    return shared_writes + output_writes + lineage_writes * len(step.outputs)


def compute_run_spec_digest(run_spec: RunSpec) -> TypedId:
    """A stable digest of every field a Run fixes at creation time (spec 9.2's
    field list), for ``RunRecord.run_spec_digest`` and as the ``begin_run``
    idempotency ``request_fingerprint`` (T28): a replayed ``idempotency_key``
    with a different ``RunSpec`` must be a conflict, not a silent no-op.
    Requires ``retry_policy``/``cancel_policy`` to only contain values
    :func:`~aigear.management.v2.canonical.canonicalize_json` supports."""
    return TypedId.from_bare(digest_sha256_of_jcs(["aigear.run-spec.v2", run_spec.to_digest_dict()]))

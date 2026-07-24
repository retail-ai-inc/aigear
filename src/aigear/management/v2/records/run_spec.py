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

Deliberately out of scope here: dependency-graph validation (whether a
Step's declared ``dependencies`` actually reference other Steps in the same
RunSpec, cycle detection) and ``resolved_inputs`` resolution -- the spec's
explicit fail-fast list for ``begin_run`` does not mention either, and
resolving symbolic dependencies into concrete Occurrences is a later,
Firestore-transaction-backed step (T20), not a property of the RunSpec value
itself. ``retry_policy``/``cancel_policy`` are left as opaque dicts because
spec 9.2 requires that a RunSpec carry them but never defines a closed schema
for their contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.naming import ensure_no_collisions, validate_segment
from aigear.management.v2.records.asset_version import compute_component_key

__all__ = [
    "InvalidRunSpecError",
    "SeedInputBinding",
    "OutputSlotSpec",
    "StepSpec",
    "RunSpec",
]


class InvalidRunSpecError(ValueError):
    """Raised for a malformed RunSpec field or a fail-fast uniqueness/collision violation (spec 9.2)."""


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

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "binding_name", validate_segment(self.binding_name, field_name="binding_name")
        )
        _require_typed_id("asset_version_id", self.asset_version_id)
        if self.occurrence_id is not None:
            _require_typed_id("occurrence_id", self.occurrence_id)
        if self.source_label_id is not None:
            _require_typed_id("source_label_id", self.source_label_id)


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

    @property
    def component_key(self) -> TypedId:
        """Deterministic component key derived from ``(role, logical_name)`` (spec 5.3)."""
        return compute_component_key(self.role, self.logical_name)


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

        if not all(isinstance(binding, SeedInputBinding) for binding in self.seed_inputs):
            raise InvalidRunSpecError("seed_inputs must be a sequence of SeedInputBinding")
        _ensure_unique(
            (binding.binding_name for binding in self.seed_inputs),
            field_name="seed_inputs binding_name",
        )

        if self.scheduled_for is not None:
            _require_non_empty_str("scheduled_for", self.scheduled_for)
        if not isinstance(self.retry_policy, dict):
            raise InvalidRunSpecError(f"retry_policy must be a dict, got {type(self.retry_policy)!r}")
        if not isinstance(self.cancel_policy, dict):
            raise InvalidRunSpecError(
                f"cancel_policy must be a dict, got {type(self.cancel_policy)!r}"
            )

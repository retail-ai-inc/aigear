"""``PipelineAssetManagement`` — the minimal Pipeline V2 migration entry point.

This is the new entry point spec section 24.1 recommends as the eventual
replacement for :class:`~aigear.management.versioned_asset.VersionedAssetManagement`.
It is deliberately minimal for Phase A: the constructor wires up an
``EnvironmentIdentity``/``environment_fingerprint`` (spec 2.1) and an
in-memory :class:`~aigear.management.v2.fake_registry.FakeRegistryV2` (no
real Firestore/GCS access), and only the two read-only lookups that are
fully implementable against that registry are actually usable.

Every execution/write method named in spec 24.1
(``begin_run``, ``resolve_inputs``, ``begin_attempt``, ``finalize_step_outputs``,
``fail_attempt``, ``cancel_run``, ``upload_asset``, ``upload_bundle``,
``import_external``, ``download_exact``) requires machinery Phase A does not
build yet (attempt lease/fencing, worker staging upload, the finalize
transaction, real GCS I/O -- Phase B/C). Per the Phase A plan, those methods
must fail loudly with :class:`NotImplementedError` rather than silently
falling back to V1 behavior or returning a fake success. See
``docs/pipeline-v2-migration-guide.md`` for what this means in practice and
how to migrate incrementally.
"""

from __future__ import annotations

from typing import Optional

from aigear.management.v2.environment import EnvironmentIdentity, compute_environment_fingerprint
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import SHA256_TYPED_PREFIX, TypedId
from aigear.management.v2.records.asset_version import AssetVersionRecord
from aigear.management.v2.records.occurrence import OccurrenceRecord

__all__ = ["PipelineAssetManagement"]

_NOT_IMPLEMENTED_HINT = (
    "requires Phase B/C execution machinery not yet implemented; see "
    "docs/pipeline-v2-migration-guide.md for the current migration status."
)


def _coerce_typed_id(value: "TypedId | str") -> TypedId:
    if isinstance(value, TypedId):
        return value
    if isinstance(value, str) and value.startswith(SHA256_TYPED_PREFIX):
        return TypedId.from_typed(value)
    return TypedId.from_bare(value)


class PipelineAssetManagement:
    """The Phase A skeleton of the V2 migration entry point (spec 24.1).

    Parameters
    ----------
    environment_identity:
        The stable per-environment identity (spec 2.1). Its
        ``environment_fingerprint`` is derived here, once, rather than
        accepted directly, so this class and its registry can never disagree
        about which environment they belong to.
    registry:
        Injected registry backend; defaults to a fresh
        :class:`~aigear.management.v2.fake_registry.FakeRegistryV2` so this
        class is usable standalone (e.g. in tests) without any GCP
        dependency. A real Firestore-backed V2 registry is out of scope for
        Phase A and will replace this default in a later phase.
    """

    def __init__(
        self,
        environment_identity: EnvironmentIdentity,
        *,
        registry: Optional[FakeRegistryV2] = None,
    ) -> None:
        self.environment_identity = environment_identity
        self.environment_fingerprint = compute_environment_fingerprint(environment_identity)
        self.registry = registry if registry is not None else FakeRegistryV2()

    # ── read-only lookups (implemented) ─────────────────────────────────

    def get_asset(self, asset_version_id: "TypedId | str") -> Optional[AssetVersionRecord]:
        """Look up an AssetVersion by its exact ID (spec 24.1: ``PipelineAssetRegistryV2.get_asset``)."""
        return self.registry.get_asset_version(_coerce_typed_id(asset_version_id))

    def get_occurrence(self, occurrence_id: "TypedId | str") -> Optional[OccurrenceRecord]:
        """Look up an Occurrence by its exact ID (spec 24.1: ``PipelineAssetRegistryV2.get_occurrence``)."""
        return self.registry.get_occurrence(_coerce_typed_id(occurrence_id))

    # ── execution lifecycle (not yet implemented) ───────────────────────

    def begin_run(self, run_spec, idempotency_key: str):
        raise NotImplementedError(f"PipelineAssetManagement.begin_run {_NOT_IMPLEMENTED_HINT}")

    def resolve_inputs(self, run_id: str, step_name: str):
        raise NotImplementedError(
            f"PipelineAssetManagement.resolve_inputs {_NOT_IMPLEMENTED_HINT}"
        )

    def begin_attempt(self, run_id: str, step_name: str):
        raise NotImplementedError(
            f"PipelineAssetManagement.begin_attempt {_NOT_IMPLEMENTED_HINT}"
        )

    def finalize_step_outputs(self, step_completion):
        raise NotImplementedError(
            f"PipelineAssetManagement.finalize_step_outputs {_NOT_IMPLEMENTED_HINT}"
        )

    def fail_attempt(self, *args, **kwargs):
        raise NotImplementedError(
            f"PipelineAssetManagement.fail_attempt {_NOT_IMPLEMENTED_HINT}"
        )

    def cancel_run(self, run_id: str, reason: str):
        raise NotImplementedError(f"PipelineAssetManagement.cancel_run {_NOT_IMPLEMENTED_HINT}")

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

    def download_exact(self, asset_version_id: "TypedId | str", occurrence_id: "TypedId | str | None" = None):
        raise NotImplementedError(
            f"PipelineAssetManagement.download_exact {_NOT_IMPLEMENTED_HINT}"
        )

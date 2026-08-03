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

``begin_run`` persists the immutable RunSpec in Registry V2. Later calls
resolve it from Registry, so controller restarts and horizontal replicas do
not depend on process memory. ``self._run_specs`` remains only as a backwards-
compatible fallback for third-party test doubles written before RunSpec CRUD.

Manual ``upload_asset``/``upload_bundle`` and external ``import_external``
requests use the same Phase C import operation. Local bytes first enter an
operation-scoped, create-only quarantine; external requests pin one exact GCS
generation. All three return the authoritative operation (and immutable result
when already committed) without bypassing asynchronous inspection/finalization.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple, TypeVar
from urllib.parse import urlsplit

from aigear.management.v2 import attempt_fail, resolve_inputs as resolve_inputs_module, run_cancel
from aigear.management.v2.attempt_heartbeat import heartbeat_attempt as _heartbeat_attempt
from aigear.management.v2.attestation import (
    AttestationVerifier,
    CloudKmsAsymmetricSigner,
    CloudKmsAttestationVerifier,
    DigestSigner,
    HmacTestSigner,
)
from aigear.management.v2.control_document import ControlDocument
from aigear.management.v2.download import (
    download_bundle_exact as _download_bundle,
    download_exact as _download_blob,
)
from aigear.management.v2.environment import EnvironmentIdentity, compute_environment_fingerprint
from aigear.management.v2.fake_gcs import FakeGcsClient
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.firestore_registry import FirestoreRegistryV2
from aigear.management.v2.finalizer import FinalizeContext
from aigear.management.v2.finalizer import finalize_step_outputs as _finalize_step_outputs
from aigear.management.v2.finalizer import (
    prepare_finalize_external,
    reserve_finalize_blob_claims,
    try_rebuild_finalize_outcome,
    validate_finalize_staging,
)
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.gcs_client import GcsClientV2, GoogleGcsClientV2
from aigear.management.v2.identifiers import SHA256_TYPED_PREFIX, TypedId
from aigear.management.v2.external_source import (
    ExternalSourcePolicy,
    parse_exact_external_source,
)
from aigear.management.v2.import_api import (
    DEFAULT_MAX_LOCAL_IMPORT_BYTES,
    DEFAULT_MAX_LOCAL_IMPORT_PAYLOADS,
    ExternalObjectMetadata,
    ExternalSourceInspector,
    GoogleExternalSourceInspector,
    ImportSubmission,
    LocalImportPayload,
    compute_import_operation_id,
    stage_local_import_bundle,
)
from aigear.management.v2.import_recovery import reconcile_import_once
from aigear.management.v2.import_reservation import (
    compute_import_idempotency_key_hash,
    reserve_import_operation,
)
from aigear.management.v2.records.asset_version import AssetVersionRecord, LifecycleState, TrustState
from aigear.management.v2.records.import_operation import (
    ExactImportSource,
    ImportOperationRecord,
    ImportPhase,
)
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    ResolvedInputBinding,
    compute_resolved_inputs_digest,
)
from aigear.management.v2.records.run import AttemptRecord, RunRecord, RunStatus, StepRecord, StepStatus
from aigear.management.v2.records.run_spec import (
    RunSpec,
    compute_run_spec_digest,
    seed_inputs_for_step,
)
from aigear.management.v2.pubsub_auth import verify_pubsub_oidc_token
from aigear.management.v2.records.release import (
    ReleaseOperationRecord,
    ServiceReleaseState,
)
from aigear.management.v2.records.runtime_evidence import RuntimeAuthorizationLease
from aigear.management.v2.release_kubernetes import KubernetesReleasePort
from aigear.management.v2.release_lease import (
    ReleaseExternalState,
    compute_release_idempotency_key_hash,
)
from aigear.management.v2.release_manifest import RuntimeContract, SignedReleaseManifest
from aigear.management.v2.release_prepare import prepare_release as _prepare_release
from aigear.management.v2.release_reconcile import reconcile_release_once
from aigear.management.v2.release_rollback import rollback_release as _rollback_release
from aigear.management.v2.runtime_authorization import (
    VerifiedJournalWatermark,
    issue_runtime_authorization,
)
from aigear.management.v2.resolver import ResolverError, Selector, UsageContext
from aigear.management.v2.resolver import resolve as _resolve_selector
from aigear.management.v2.run_trigger import begin_run_trigger
from aigear.management.v2.staging_upload import StepCompletionMessage
from aigear.management.v2.step_lease import acquire_step_lease

__all__ = [
    "PipelineAssetManagementError",
    "LocalImportPayload",
    "ImportSubmission",
    "ServiceReleaseStatus",
    "PipelineAssetManagement",
]

# Every RunSpec accepted by begin_run uses this schema_version end to end
# (RunRecord/StepRecord/AttemptRecord/OccurrenceRecord all key off it); no
# Phase B module makes it configurable yet, so it is a module constant here
# too rather than a per-instance guess.
_SCHEMA_VERSION = "2.0"
_IMPORT_CAPABILITY = "asset_import_v2"
_RELEASE_CAPABILITY = "service_release_v2"
_RUNTIME_AUTHORIZATION_CAPABILITY = "service_runtime_authorization_v2"
_T = TypeVar("_T")


class PipelineAssetManagementError(ValueError):
    """Raised when a ``PipelineAssetManagement`` call is missing a required,
    caller-supplied cross-cutting input (see module docstring), or names a
    ``run_id``/``step_name`` this instance has no record of."""


@dataclass(frozen=True)
class ServiceReleaseStatus:
    state: ServiceReleaseState
    operation: Optional[ReleaseOperationRecord]


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
        Injected backends; each defaults to a fresh in-memory fake for tests.
        Production mode rejects these defaults; use :meth:`for_gcp` or inject
        equivalent transaction/generation-aware production implementations.
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
        registry: Optional[object] = None,
        gcs: Optional[GcsClientV2] = None,
        schema_contract_digest: Optional[TypedId] = None,
        runtime_contract_digest: Optional[TypedId] = None,
        policy_version: Optional[str] = None,
        control_document: Optional[ControlDocument] = None,
        attestation_signer: Optional[DigestSigner] = None,
        manifest_integrity_signer: Optional[DigestSigner] = None,
        blob_location_signer: Optional[DigestSigner] = None,
        occurrence_finalization_signer: Optional[DigestSigner] = None,
        attestation_verifier: Optional[AttestationVerifier] = None,
        release_attestation_verifier: Optional[AttestationVerifier] = None,
        allowed_completion_publishers: Tuple[str, ...] = (),
        completion_oidc_audience: Optional[str] = None,
        external_source_policy: Optional[ExternalSourcePolicy] = None,
        external_source_inspector: Optional[ExternalSourceInspector] = None,
        max_local_import_payloads: int = DEFAULT_MAX_LOCAL_IMPORT_PAYLOADS,
        max_local_import_bytes: int = DEFAULT_MAX_LOCAL_IMPORT_BYTES,
        release_kubernetes: Optional[KubernetesReleasePort] = None,
        production: bool = False,
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
        self.attestation_signer = attestation_signer
        self.manifest_integrity_signer = manifest_integrity_signer or attestation_signer
        self.blob_location_signer = blob_location_signer or attestation_signer
        self.occurrence_finalization_signer = occurrence_finalization_signer or attestation_signer
        self.attestation_verifier = attestation_verifier
        self.release_attestation_verifier = (
            release_attestation_verifier or attestation_verifier
        )
        self.production = production
        self.allowed_completion_publishers = tuple(allowed_completion_publishers)
        self.completion_oidc_audience = completion_oidc_audience
        self.external_source_policy = external_source_policy
        self.external_source_inspector = external_source_inspector
        self.max_local_import_payloads = max_local_import_payloads
        self.max_local_import_bytes = max_local_import_bytes
        self.release_kubernetes = release_kubernetes
        self._run_specs: Dict[str, RunSpec] = {}
        if production:
            missing = [
                name
                for name, value in (
                    ("control_document", control_document),
                    ("schema_contract_digest", schema_contract_digest),
                    ("runtime_contract_digest", runtime_contract_digest),
                    ("policy_version", policy_version),
                    ("manifest_integrity_signer", self.manifest_integrity_signer),
                    ("blob_location_signer", self.blob_location_signer),
                    ("occurrence_finalization_signer", self.occurrence_finalization_signer),
                    ("attestation_verifier", self.attestation_verifier),
                    ("allowed_completion_publishers", self.allowed_completion_publishers or None),
                    ("completion_oidc_audience", completion_oidc_audience),
                )
                if value is None
            ]
            if missing:
                raise PipelineAssetManagementError(
                    f"production mode requires explicit {', '.join(missing)}"
                )
            if not control_document.firestore_database_resource:
                raise PipelineAssetManagementError(
                    "production mode requires control_document.firestore_database_resource"
                )
            if (
                control_document.environment_id != environment_identity.environment_id
                or control_document.environment_fingerprint != self.environment_fingerprint
            ):
                raise PipelineAssetManagementError(
                    "production control document does not belong to this environment identity"
                )
            if isinstance(self.registry, FakeRegistryV2) or isinstance(self.gcs, FakeGcsClient):
                raise PipelineAssetManagementError(
                    "production mode refuses FakeRegistryV2/FakeGcsClient backends"
                )
            if any(
                isinstance(signer, HmacTestSigner)
                for signer in (
                    self.manifest_integrity_signer,
                    self.blob_location_signer,
                    self.occurrence_finalization_signer,
                )
            ):
                raise PipelineAssetManagementError(
                    "production mode refuses HmacTestSigner; use a pinned asymmetric KMS signer"
                )
            key_versions = {
                signer.key_version
                for signer in (
                    self.manifest_integrity_signer,
                    self.blob_location_signer,
                    self.occurrence_finalization_signer,
                )
            }
            if len(key_versions) != 3:
                raise PipelineAssetManagementError(
                    "production mode requires distinct pinned keys for manifest, blob location, "
                    "and occurrence finalization attestations"
                )
            if not all(
                isinstance(signer, CloudKmsAsymmetricSigner)
                for signer in (
                    self.manifest_integrity_signer,
                    self.blob_location_signer,
                    self.occurrence_finalization_signer,
                )
            ):
                raise PipelineAssetManagementError(
                    "production mode requires CloudKmsAsymmetricSigner for all attestations"
                )
            if isinstance(self.attestation_verifier, CloudKmsAttestationVerifier):
                self.attestation_verifier.warm()

    @classmethod
    def for_gcp(
        cls,
        environment_identity: EnvironmentIdentity,
        *,
        control_document: ControlDocument,
        schema_contract_digest: TypedId,
        runtime_contract_digest: TypedId,
        policy_version: str,
        manifest_integrity_key_version: str,
        blob_location_key_version: str,
        occurrence_finalization_key_version: str,
        policy_decision_key_versions: Tuple[str, ...],
        allowed_completion_publishers: Tuple[str, ...],
        completion_oidc_audience: str,
        external_source_policy: Optional[ExternalSourcePolicy] = None,
        firestore_client: object = None,
        storage_client: object = None,
        kms_client: object = None,
    ) -> "PipelineAssetManagement":
        """Construct a fail-closed production manager for the current GCP binding."""
        if not policy_decision_key_versions:
            raise PipelineAssetManagementError(
                "for_gcp requires at least one pinned policy decision key version"
            )
        integrity_key_versions = {
            manifest_integrity_key_version,
            blob_location_key_version,
            occurrence_finalization_key_version,
        }
        if integrity_key_versions.intersection(policy_decision_key_versions):
            raise PipelineAssetManagementError(
                "policy decision verification keys must be independent from integrity keys"
            )
        registry = FirestoreRegistryV2(
            environment_identity.project_name,
            environment_identity.pipeline_version,
            database_id=control_document.registry_binding.firestore_database_id,
            client=firestore_client,
        )
        gcs = GoogleGcsClientV2(
            environment_identity.asset_bucket_name,
            client=storage_client,
        )
        return cls(
            environment_identity,
            registry=registry,
            gcs=gcs,
            schema_contract_digest=schema_contract_digest,
            runtime_contract_digest=runtime_contract_digest,
            policy_version=policy_version,
            control_document=control_document,
            manifest_integrity_signer=CloudKmsAsymmetricSigner(
                manifest_integrity_key_version, client=kms_client
            ),
            blob_location_signer=CloudKmsAsymmetricSigner(
                blob_location_key_version, client=kms_client
            ),
            occurrence_finalization_signer=CloudKmsAsymmetricSigner(
                occurrence_finalization_key_version, client=kms_client
            ),
            attestation_verifier=CloudKmsAttestationVerifier(
                (
                    manifest_integrity_key_version,
                    blob_location_key_version,
                    occurrence_finalization_key_version,
                    *policy_decision_key_versions,
                ),
                client=kms_client,
            ),
            allowed_completion_publishers=allowed_completion_publishers,
            completion_oidc_audience=completion_oidc_audience,
            external_source_policy=external_source_policy,
            external_source_inspector=(
                None
                if external_source_policy is None
                else GoogleExternalSourceInspector(client=storage_client)
            ),
            production=True,
        )

    # ── internal lookups ─────────────────────────────────────────────────

    def _require_run_spec(self, run_id: str) -> RunSpec:
        getter = getattr(self.registry, "get_run_spec", None)
        run_spec = getter(run_id) if getter is not None else None
        if run_spec is None:
            # Compatibility fallback for a third-party test backend written
            # against the earlier Phase-B protocol. Production backends must
            # implement get/put_run_spec; for the bundled backend the cache is
            # never authoritative.
            run_spec = self._run_specs.get(run_id)
        if run_spec is None:
            raise PipelineAssetManagementError(
                f"no RunSpec cached or persisted for run_id {run_id!r}"
            )
        return run_spec

    def _run_atomic(self, work: Callable[[object], _T]) -> _T:
        runner = getattr(self.registry, "run_atomic", None)
        if runner is None:
            raise PipelineAssetManagementError(
                "the configured Registry backend has no run_atomic transaction boundary; "
                "refusing a multi-record Pipeline V2 mutation"
            )
        def guarded(registry):
            if self.production:
                self._validate_current_control(registry)
            return work(registry)

        return runner(guarded)

    def _validate_current_control(self, registry) -> ControlDocument:
        getter = getattr(registry, "get_control_document", None)
        if getter is None:
            raise PipelineAssetManagementError(
                "production Registry backend cannot read the V2 control document"
            )
        current = getter()
        if current is None:
            raise PipelineAssetManagementError("V2 control document is missing")
        expected = self.control_document
        if expected is None:
            raise PipelineAssetManagementError("production manager has no expected control binding")
        if current.environment_fingerprint != self.environment_fingerprint:
            raise PipelineAssetManagementError("control environment_fingerprint mismatch")
        if current.authority != "v2" or current.phase not in {
            "v2_authoritative",
            "compatibility_window",
            "complete",
        }:
            raise PipelineAssetManagementError(
                f"V2 is not writable in control phase {current.phase!r}"
            )
        if (
            current.write_epoch != expected.write_epoch
            or current.registry_binding != expected.registry_binding
            or current.firestore_database_resource != expected.firestore_database_resource
        ):
            raise PipelineAssetManagementError(
                "control database/binding/write epoch changed; recreate the manager from current config"
            )
        backend_database_id = getattr(registry, "database_id", None)
        if backend_database_id != current.registry_binding.firestore_database_id:
            raise PipelineAssetManagementError(
                "Registry backend database_id does not match the current control binding"
            )
        if (
            current.applied_security_watermark < expected.applied_security_watermark
            or current.security_journal_head_sequence < expected.security_journal_head_sequence
        ):
            raise PipelineAssetManagementError(
                "control security watermark/journal head moved backwards; possible restore rollback"
            )
        return current

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
        manifest_signer = self.manifest_integrity_signer
        location_signer = self.blob_location_signer
        occurrence_signer = self.occurrence_finalization_signer
        if manifest_signer is None or location_signer is None or occurrence_signer is None:
            if self.production:
                raise PipelineAssetManagementError("production finalize requires all integrity signers")
            default_signer = HmacTestSigner()
            manifest_signer = manifest_signer or default_signer
            location_signer = location_signer or default_signer
            occurrence_signer = occurrence_signer or default_signer
        return FinalizeContext(
            environment_id=self.environment_identity.environment_id,
            environment_fingerprint=self.environment_fingerprint,
            schema_version=_SCHEMA_VERSION,
            schema_contract_digest=self.schema_contract_digest,
            runtime_contract_digest=self.runtime_contract_digest,
            policy_version=self.policy_version,
            now=now,
            manifest_integrity_signer=manifest_signer,
            blob_location_signer=location_signer,
            occurrence_finalization_signer=occurrence_signer,
            write_epoch=self.control_document.write_epoch if self.control_document else 1,
            firestore_database_id=(
                self.control_document.registry_binding.firestore_database_id
                if self.control_document
                else None
            ),
            firestore_database_resource=(
                self.control_document.firestore_database_resource
                if self.control_document
                else None
            ),
            registry_binding_id=(
                self.control_document.registry_binding.registry_binding_id
                if self.control_document
                else None
            ),
            registry_binding_epoch=(
                self.control_document.registry_binding.registry_binding_epoch
                if self.control_document
                else None
            ),
        )

    def _require_control_document(self) -> ControlDocument:
        if self.control_document is None:
            raise PipelineAssetManagementError(
                "this operation requires control_document to be set on this "
                "PipelineAssetManagement instance"
            )
        if self.production:
            return self._validate_current_control(self.registry)
        return self.control_document

    def _require_import_control(self) -> ControlDocument:
        control = self._require_control_document()
        if control.authority != "v2" or control.phase not in {
            "v2_authoritative",
            "compatibility_window",
            "complete",
        }:
            raise PipelineAssetManagementError(
                f"V2 import is not writable in control phase {control.phase!r}"
            )
        if (
            control.environment_id != self.environment_identity.environment_id
            or control.environment_fingerprint != self.environment_fingerprint
        ):
            raise PipelineAssetManagementError(
                "import control document does not belong to this environment"
            )
        if _IMPORT_CAPABILITY not in control.required_capabilities:
            raise PipelineAssetManagementError(
                f"import requires control capability {_IMPORT_CAPABILITY!r}"
            )
        missing_registry_methods = [
            name
            for name in ("get_control_document", "get_import_operation", "run_atomic")
            if not callable(getattr(self.registry, name, None))
        ]
        if missing_registry_methods:
            raise PipelineAssetManagementError(
                "configured Registry lacks import capabilities: "
                + ", ".join(missing_registry_methods)
            )
        current = self.registry.get_control_document()
        if current != control:
            raise PipelineAssetManagementError(
                "Registry control/binding changed before import submission"
            )
        return control

    def _require_service_control(self, capability: str) -> ControlDocument:
        control = self._require_control_document()
        if control.authority != "v2" or control.phase not in {
            "v2_only",
            "v2_authoritative",
            "compatibility_window",
            "complete",
        }:
            raise PipelineAssetManagementError(
                f"service release is not writable in control phase {control.phase!r}"
            )
        if (
            control.environment_id != self.environment_identity.environment_id
            or control.environment_fingerprint != self.environment_fingerprint
        ):
            raise PipelineAssetManagementError(
                "service release control does not belong to this environment"
            )
        if capability not in control.required_capabilities:
            raise PipelineAssetManagementError(
                f"service release requires control capability {capability!r}"
            )
        getter = getattr(self.registry, "get_control_document", None)
        if not callable(getter) or getter() != control:
            raise PipelineAssetManagementError(
                "Registry control/binding changed before service release call"
            )
        return control

    @staticmethod
    def _require_release_mutation_envelope(
        *,
        idempotency_key: str,
        actor: str,
        reason: str,
        expected_revision: int,
    ) -> None:
        for field_name, value in (
            ("idempotency_key", idempotency_key),
            ("actor", actor),
            ("reason", reason),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PipelineAssetManagementError(
                    f"{field_name} must be a non-empty str"
                )
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise PipelineAssetManagementError(
                "expected_revision must be a non-negative int"
            )

    def _release_replay(
        self, service_name: str, idempotency_key: str
    ) -> Optional[ReleaseOperationRecord]:
        state = self.registry.get_service_release_state(service_name)
        if state is None or state.active_operation_id is None:
            return None
        operation = self.registry.get_release_operation(state.active_operation_id)
        if (
            operation is not None
            and operation.idempotency_key_hash
            == compute_release_idempotency_key_hash(idempotency_key)
        ):
            return operation
        return None

    @staticmethod
    def _import_now(value: Optional[datetime]) -> str:
        instant = value if value is not None else datetime.now(timezone.utc)
        if (
            not isinstance(instant, datetime)
            or instant.tzinfo is None
            or instant.utcoffset() is None
        ):
            raise PipelineAssetManagementError("import now must be timezone-aware")
        return instant.isoformat()

    @staticmethod
    def _validate_import_request(
        *,
        idempotency_key: str,
        owner_principal: str,
        write_budget: int,
        lease_ttl_seconds: int,
    ) -> None:
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise PipelineAssetManagementError("idempotency_key must be a non-empty str")
        if not isinstance(owner_principal, str) or not owner_principal:
            raise PipelineAssetManagementError("owner_principal must be a non-empty str")
        if (
            isinstance(write_budget, bool)
            or not isinstance(write_budget, int)
            or write_budget < 1
            or write_budget >= 500
        ):
            raise PipelineAssetManagementError(
                "write_budget must be a positive int below 500"
            )
        if (
            isinstance(lease_ttl_seconds, bool)
            or not isinstance(lease_ttl_seconds, int)
            or lease_ttl_seconds < 1
        ):
            raise PipelineAssetManagementError("lease_ttl_seconds must be positive")

    def _reserve_import(
        self,
        *,
        control: ControlDocument,
        source: ExactImportSource,
        operation_id: str,
        idempotency_key: str,
        owner_principal: str,
        target_quarantine_prefix: str,
        write_budget: int,
        lease_ttl_seconds: int,
        now: str,
    ) -> ImportOperationRecord:
        return reserve_import_operation(
            self.registry,
            control=control,
            source=source,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            owner_principal=owner_principal,
            target_quarantine_prefix=target_quarantine_prefix,
            write_budget=write_budget,
            lease_ttl_seconds=lease_ttl_seconds,
            now=now,
        )

    def _import_submission(
        self, operation: ImportOperationRecord, *, now: str
    ) -> ImportSubmission:
        if operation.phase is not ImportPhase.SUCCEEDED:
            return ImportSubmission(operation)
        outcome = reconcile_import_once(
            self.registry,
            idempotency_key_hash=operation.idempotency_key_hash,
            expected_request_fingerprint=operation.request_fingerprint,
            expected_fencing_token=operation.fencing_token,
            now=now,
        )
        if outcome.result is None:
            raise PipelineAssetManagementError(
                "succeeded import has no reconstructable immutable result"
            )
        return ImportSubmission(outcome.operation, outcome.result)

    # ── read-only lookups (implemented) ─────────────────────────────────

    def get_asset(self, asset_version_id: "TypedId | str") -> Optional[AssetVersionRecord]:
        """Look up an AssetVersion by its exact ID (spec 24.1: ``PipelineAssetRegistryV2.get_asset``)."""
        return self.registry.get_asset_version(_coerce_typed_id(asset_version_id))

    def get_occurrence(self, occurrence_id: "TypedId | str") -> Optional[OccurrenceRecord]:
        """Look up an Occurrence by its exact ID (spec 24.1: ``PipelineAssetRegistryV2.get_occurrence``)."""
        return self.registry.get_occurrence(_coerce_typed_id(occurrence_id))

    @staticmethod
    def _validate_seed_input_state(
        registry, run_spec: RunSpec, environment_fingerprint: TypedId
    ) -> None:
        for seed in run_spec.seed_inputs:
            asset = registry.get_asset_version(seed.asset_version_id)
            if asset is None:
                raise PipelineAssetManagementError(
                    f"seed input {seed.binding_name!r} AssetVersion is missing"
                )
            if asset.environment_fingerprint != environment_fingerprint:
                raise PipelineAssetManagementError(
                    f"seed input {seed.binding_name!r} belongs to another environment"
                )
            if (
                asset.lifecycle_state != LifecycleState.ACTIVE
                or asset.trust_state != TrustState.APPROVED
                or asset.policy_decision_head_ref is None
            ):
                raise PipelineAssetManagementError(
                    f"seed input {seed.binding_name!r} requires active + approved "
                    "AssetVersion with a policy decision head"
                )
            if seed.occurrence_id is not None:
                occurrence = registry.get_occurrence(seed.occurrence_id)
                if (
                    occurrence is None
                    or occurrence.status != OccurrenceStatus.COMMITTED
                    or occurrence.asset_version_id != seed.asset_version_id
                ):
                    raise PipelineAssetManagementError(
                        f"seed input {seed.binding_name!r} Occurrence is missing, "
                        "uncommitted or points to another AssetVersion"
                    )
                winner = registry.get_committed_occurrence_by_output_key(
                    occurrence.committed_output_key
                )
                if winner is None or winner.occurrence_id != occurrence.occurrence_id:
                    raise PipelineAssetManagementError(
                        f"seed input {seed.binding_name!r} Occurrence is not the output winner"
                    )
            if seed.source_label_id is not None:
                label = registry.get_label(seed.source_label_id)
                if label is None or label.asset_version_id != seed.asset_version_id:
                    raise PipelineAssetManagementError(
                        f"seed input {seed.binding_name!r} source label is missing or rebound"
                    )

    def _verify_seed_input_attestations(
        self, run_spec: RunSpec, *, registry=None, now: Optional[datetime] = None
    ) -> None:
        if not run_spec.seed_inputs:
            return
        if self.attestation_verifier is None or not self.policy_version:
            raise PipelineAssetManagementError(
                "seed inputs require an attestation verifier and current policy version"
            )
        control = self._require_control_document()
        resolve_time = now or datetime.now(timezone.utc)
        active_registry = self.registry if registry is None else registry
        for seed in run_spec.seed_inputs:
            selector = (
                Selector.by_occurrence(seed.occurrence_id)
                if seed.occurrence_id is not None
                else Selector.by_asset_version(seed.asset_version_id)
            )
            try:
                handle = _resolve_selector(
                    active_registry,
                    control,
                    self.layout,
                    selector,
                    UsageContext.NEW_RUN_SEED,
                    resolve_time,
                    attestation_verifier=self.attestation_verifier,
                    required_policy_version=self.policy_version,
                )
            except ResolverError as exc:
                raise PipelineAssetManagementError(
                    f"seed input {seed.binding_name!r} policy resolution failed: {exc}"
                ) from exc
            if handle.asset_version_id != seed.asset_version_id:
                raise PipelineAssetManagementError(
                    f"seed input {seed.binding_name!r} resolved to another AssetVersion"
                )
            if seed.source_label_id is not None and handle.label_id not in (
                None,
                seed.source_label_id,
            ):
                raise PipelineAssetManagementError(
                    f"seed input {seed.binding_name!r} resolved to another Label"
                )

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
        authorization_time = datetime.now(timezone.utc)
        self._validate_seed_input_state(
            self.registry, run_spec, self.environment_fingerprint
        )
        self._verify_seed_input_attestations(run_spec, now=authorization_time)

        def _work(registry) -> object:
            self._validate_seed_input_state(
                registry, run_spec, self.environment_fingerprint
            )
            self._verify_seed_input_attestations(
                run_spec, registry=registry, now=authorization_time
            )

            def _create_run() -> str:
                run_id = f"run-{uuid.uuid4().hex}"
                registry.create_run(
                    RunRecord(
                        run_id=run_id,
                        status=RunStatus.PENDING,
                        run_spec_digest=run_spec_digest,
                        remaining_required_steps=len(run_spec.steps),
                    )
                )
                put_run_spec = getattr(registry, "put_run_spec", None)
                if put_run_spec is None:
                    raise PipelineAssetManagementError(
                        "the configured Registry backend cannot persist immutable RunSpec records"
                    )
                put_run_spec(run_id, run_spec)
                for step_spec in run_spec.steps:
                    initial_status = StepStatus.BLOCKED if step_spec.dependencies else StepStatus.READY
                    seeds = tuple(
                        ResolvedInputBinding(
                            binding_name=seed.binding_name,
                            asset_version_id=seed.asset_version_id,
                            occurrence_id=seed.occurrence_id,
                            source_label_id=seed.source_label_id,
                        )
                        for seed in seed_inputs_for_step(run_spec, step_spec)
                    )
                    registry.create_step(
                        StepRecord(
                            run_id=run_id,
                            step_name=step_spec.step_name,
                            status=initial_status,
                            resolved_inputs=seeds,
                            resolved_inputs_digest=(
                                compute_resolved_inputs_digest(seeds) if seeds else None
                            ),
                            source_step_revision=1 if seeds else None,
                        )
                    )
                registry.update_run_status(run_id, RunStatus.RUNNING)
                return run_id

            return begin_run_trigger(
                registry,
                idempotency_key=_coerce_typed_id(idempotency_key),
                request_fingerprint=run_spec_digest.typed,
                owner_principal=owner_principal,
                create_run=_create_run,
                write_epoch=self.control_document.write_epoch if self.control_document else 1,
                firestore_database_id=(
                    self.control_document.registry_binding.firestore_database_id
                    if self.control_document
                    else None
                ),
                firestore_database_resource=(
                    self.control_document.firestore_database_resource
                    if self.control_document
                    else None
                ),
                registry_binding_id=(
                    self.control_document.registry_binding.registry_binding_id
                    if self.control_document
                    else None
                ),
                registry_binding_epoch=(
                    self.control_document.registry_binding.registry_binding_epoch
                    if self.control_document
                    else None
                ),
            )

        operation = self._run_atomic(_work)
        self._run_specs[operation.run_id] = run_spec
        return self.registry.get_run(operation.run_id)

    def resolve_inputs(self, run_id: str, step_name: str, *, now: datetime) -> StepRecord:
        """Resolve ``step_name``'s declared dependencies into exact upstream
        Occurrences (spec 9.2, T28's ``resolve_inputs`` module)."""
        run_spec = self._require_run_spec(run_id)
        return self._run_atomic(
            lambda registry: resolve_inputs_module.resolve_step_inputs(
                registry,
                run_spec,
                run_id=run_id,
                step_name=step_name,
                now=now,
                layout=self.layout,
                attestation_verifier=self.attestation_verifier,
            )
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
        def _work(registry):
            step = registry.get_step(run_id, step_name)
            if step is None:
                raise PipelineAssetManagementError(
                    f"no Step registered for (run_id={run_id!r}, step_name={step_name!r})"
                )
            return acquire_step_lease(
                registry,
                run_id=run_id,
                step_name=step_name,
                output_names=[output.output_name for output in step_spec.outputs],
                resolved_input_bindings=step.resolved_inputs,
                environment_fingerprint=self.environment_fingerprint,
                schema_version=_SCHEMA_VERSION,
                owner_principal=owner_principal,
                now=now,
            )
        return self._run_atomic(_work)

    def finalize_step_outputs(
        self,
        step_completion: StepCompletionMessage,
        *,
        now: datetime,
        verified_publisher_principal: Optional[str] = None,
        publisher_oidc_token: Optional[str] = None,
    ):
        """Finalize every output in one worker completion message (spec 10.4, T22)."""
        run_spec = self._require_run_spec(step_completion.run_id)
        context = self._require_finalize_context(now)
        if self.production:
            if step_completion.fencing_token is None:
                raise PipelineAssetManagementError(
                    "production finalize requires the complete database/binding/write/fencing envelope"
                )
            if publisher_oidc_token is None:
                raise PipelineAssetManagementError(
                    "production finalize requires the Pub/Sub push OIDC token"
                )
            verified_publisher_principal = verify_pubsub_oidc_token(
                publisher_oidc_token,
                audience=self.completion_oidc_audience,
                allowed_service_accounts=self.allowed_completion_publishers,
            )
            if verified_publisher_principal != step_completion.publisher_principal:
                raise PipelineAssetManagementError(
                    "verified publisher principal does not match the completion envelope"
                )
            if verified_publisher_principal not in self.allowed_completion_publishers:
                raise PipelineAssetManagementError("completion publisher principal is not allowlisted")
            try:
                issued_at = datetime.fromisoformat(step_completion.issued_at)
                expires_at = datetime.fromisoformat(step_completion.expires_at)
            except (TypeError, ValueError) as exc:
                raise PipelineAssetManagementError(
                    "completion issued_at/expires_at must be valid RFC3339 timestamps"
                ) from exc
            if now.tzinfo is None or issued_at.tzinfo is None or expires_at.tzinfo is None:
                raise PipelineAssetManagementError("completion timestamps and now must be timezone-aware")
            if not (issued_at <= now < expires_at):
                raise PipelineAssetManagementError("completion message is not within its replay window")
            if (expires_at - issued_at).total_seconds() > 600:
                raise PipelineAssetManagementError("completion replay window exceeds 10 minutes")
            control = self._require_control_document()
            expected = (
                control.registry_binding.firestore_database_id,
                control.firestore_database_resource,
                control.registry_binding.registry_binding_id,
                control.registry_binding.registry_binding_epoch,
                control.write_epoch,
            )
            actual = (
                step_completion.firestore_database_id,
                step_completion.firestore_database_resource,
                step_completion.registry_binding_id,
                step_completion.registry_binding_epoch,
                step_completion.write_epoch,
            )
            if actual != expected:
                raise PipelineAssetManagementError(
                    "completion database/binding/write epoch does not match the current control document"
                )
        replay = try_rebuild_finalize_outcome(self.registry, step_completion)
        if replay is not None:
            return replay
        validate_finalize_staging(self.gcs, self.layout, step_completion, run_spec)
        self._run_atomic(
            lambda registry: reserve_finalize_blob_claims(
                registry,
                None,
                self.layout,
                step_completion,
                run_spec,
                now=now,
                staging_validated=True,
            )
        )
        prepared = prepare_finalize_external(
            self.registry,
            self.gcs,
            self.layout,
            step_completion,
            run_spec,
            context,
        )
        return self._run_atomic(
            lambda registry: _finalize_step_outputs(
                registry,
                self.gcs,
                self.layout,
                step_completion,
                run_spec,
                context,
                prepared,
            )
        )

    def heartbeat_attempt(
        self,
        run_id: str,
        step_name: str,
        attempt_no: int,
        *,
        fencing_token: int,
        owner_principal: str,
        now: datetime,
    ) -> AttemptRecord:
        """Extend the current Attempt lease after owner/fence validation."""
        return self._run_atomic(
            lambda registry: _heartbeat_attempt(
                registry,
                run_id=run_id,
                step_name=step_name,
                attempt_no=attempt_no,
                fencing_token=fencing_token,
                owner_principal=owner_principal,
                now=now,
            )
        )

    def fail_attempt(
        self,
        run_id: str,
        step_name: str,
        attempt_no: int,
        *,
        fencing_token: int,
        retryable: bool,
        reason: str,
        now: Optional[datetime] = None,
    ) -> StepRecord:
        """Report that an Attempt failed (spec 24.1, T28's ``attempt_fail`` module)."""
        run_spec = self._require_run_spec(run_id)
        step_spec = next(
            (candidate for candidate in run_spec.steps if candidate.step_name == step_name), None
        )
        if step_spec is None:
            raise PipelineAssetManagementError(
                f"RunSpec for run_id {run_id!r} has no Step named {step_name!r}"
            )
        retry_policy = run_spec.retry_policy
        max_attempts = int(retry_policy.get("max_attempts", 3))
        initial_backoff = float(retry_policy.get("initial_backoff_seconds", 1))
        max_backoff = float(retry_policy.get("max_backoff_seconds", max(initial_backoff, 1)))
        multiplier = float(retry_policy.get("backoff_multiplier", 2))
        retry_backoff = min(max_backoff, initial_backoff * (multiplier ** max(0, attempt_no - 1)))
        return self._run_atomic(
            lambda registry: attempt_fail.fail_attempt(
                registry,
                run_id=run_id,
                step_name=step_name,
                attempt_no=attempt_no,
                fencing_token=fencing_token,
                retryable=retryable,
                reason=reason,
                output_names=[output.output_name for output in step_spec.outputs],
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff,
                now=now,
            )
        )

    def cancel_run(self, run_id: str, reason: str) -> RunRecord:
        """Cancel every non-terminal Step of ``run_id`` (spec 10.5, T23)."""
        run_spec = self._require_run_spec(run_id)
        step_names = [step_spec.step_name for step_spec in run_spec.steps]
        output_names_by_step = {
            step_spec.step_name: [output.output_name for output in step_spec.outputs]
            for step_spec in run_spec.steps
        }
        return self._run_atomic(
            lambda registry: run_cancel.cancel_run(
                registry,
                run_id=run_id,
                step_names=step_names,
                reason=reason,
                output_names_by_step=output_names_by_step,
            )
        )

    # ── asset import submission ─────────────────────────────────────────

    def upload_asset(
        self,
        path: Path,
        *,
        declaration: Mapping[str, object],
        payload_key: str,
        media_type: str,
        idempotency_key: str,
        owner_principal: str,
        file_name: Optional[str] = None,
        write_budget: int = 100,
        lease_ttl_seconds: int = 300,
        now: Optional[datetime] = None,
    ) -> ImportSubmission:
        """Stage one component in quarantine and reserve its import operation."""

        source_path = Path(path)
        return self.upload_bundle(
            (
                LocalImportPayload(
                    path=source_path,
                    payload_kind="components",
                    payload_key=payload_key,
                    file_name=file_name or source_path.name,
                    media_type=media_type,
                ),
            ),
            declaration=declaration,
            idempotency_key=idempotency_key,
            owner_principal=owner_principal,
            write_budget=write_budget,
            lease_ttl_seconds=lease_ttl_seconds,
            now=now,
        )

    def upload_bundle(
        self,
        payloads: Sequence[LocalImportPayload],
        *,
        declaration: Mapping[str, object],
        idempotency_key: str,
        owner_principal: str,
        write_budget: int = 100,
        lease_ttl_seconds: int = 300,
        now: Optional[datetime] = None,
    ) -> ImportSubmission:
        """Stage a complete local bundle create-only, then reserve its operation."""

        self._validate_import_request(
            idempotency_key=idempotency_key,
            owner_principal=owner_principal,
            write_budget=write_budget,
            lease_ttl_seconds=lease_ttl_seconds,
        )
        control = self._require_import_control()
        missing_gcs_methods = [
            name
            for name in (
                "upload_file",
                "put_object",
                "get_object",
                "get_live_object",
            )
            if not callable(getattr(self.gcs, name, None))
        ]
        if missing_gcs_methods:
            raise PipelineAssetManagementError(
                "configured GCS backend lacks local import capabilities: "
                + ", ".join(missing_gcs_methods)
            )
        now_text = self._import_now(now)
        staged = stage_local_import_bundle(
            gcs=self.gcs,
            layout=self.layout,
            environment_identity=self.environment_identity,
            environment_fingerprint=self.environment_fingerprint.typed,
            declaration=declaration,
            payloads=payloads,
            idempotency_key=idempotency_key,
            max_payloads=self.max_local_import_payloads,
            max_total_size_bytes=self.max_local_import_bytes,
        )
        operation = self._reserve_import(
            control=control,
            source=staged.source,
            operation_id=staged.operation_id,
            idempotency_key=idempotency_key,
            owner_principal=owner_principal,
            target_quarantine_prefix=staged.target_quarantine_prefix,
            write_budget=write_budget,
            lease_ttl_seconds=lease_ttl_seconds,
            now=now_text,
        )
        return self._import_submission(operation, now=now_text)

    def import_external(
        self,
        source_uri: str,
        *,
        idempotency_key: str,
        owner_principal: str,
        write_budget: int = 100,
        lease_ttl_seconds: int = 300,
        now: Optional[datetime] = None,
    ) -> ImportSubmission:
        """Reserve an allowlisted, exact-generation external GCS import."""

        self._validate_import_request(
            idempotency_key=idempotency_key,
            owner_principal=owner_principal,
            write_budget=write_budget,
            lease_ttl_seconds=lease_ttl_seconds,
        )
        control = self._require_import_control()
        policy = self.external_source_policy
        inspector = self.external_source_inspector
        if policy is None or inspector is None:
            raise PipelineAssetManagementError(
                "import_external requires an external_source_policy and trusted "
                "external_source_inspector"
            )
        if policy.target_environment_id != self.environment_identity.environment_id:
            raise PipelineAssetManagementError(
                "external source policy belongs to another environment"
            )

        bucket = urlsplit(source_uri).netloc if isinstance(source_uri, str) else ""
        allowed = next(
            (value for value in policy.allowed_buckets if value.bucket == bucket),
            None,
        )
        if allowed is None:
            raise ExternalSourceError("external source bucket is not allowlisted")
        operation_id = compute_import_operation_id(
            self.environment_fingerprint.typed, idempotency_key
        )
        target_prefix = self.layout.quarantine_manifest(operation_id).removesuffix(
            "source-manifest.json"
        )
        now_text = self._import_now(now)
        existing = self.registry.get_import_operation(
            compute_import_idempotency_key_hash(idempotency_key)
        )
        if existing is not None:
            replay_source = parse_exact_external_source(
                source_uri,
                policy=policy,
                observed_region=existing.source.region,
                observed_size_bytes=existing.source.size_bytes,
            )
            if replay_source != existing.source:
                raise PipelineAssetManagementError(
                    "idempotency key is bound to a different exact external source"
                )
            operation = self._reserve_import(
                control=control,
                source=replay_source,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                owner_principal=owner_principal,
                target_quarantine_prefix=target_prefix,
                write_budget=write_budget,
                lease_ttl_seconds=lease_ttl_seconds,
                now=now_text,
            )
            return self._import_submission(operation, now=now_text)

        provisional = parse_exact_external_source(
            source_uri,
            policy=policy,
            observed_region=allowed.region,
            observed_size_bytes=0,
        )
        metadata = inspector.inspect(provisional)
        if not isinstance(metadata, ExternalObjectMetadata):
            raise PipelineAssetManagementError(
                "external source inspector returned invalid metadata"
            )
        source = parse_exact_external_source(
            source_uri,
            policy=policy,
            observed_region=metadata.region,
            observed_size_bytes=metadata.size_bytes,
        )
        operation = self._reserve_import(
            control=control,
            source=source,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            owner_principal=owner_principal,
            target_quarantine_prefix=target_prefix,
            write_budget=write_budget,
            lease_ttl_seconds=lease_ttl_seconds,
            now=now_text,
        )
        return self._import_submission(operation, now=now_text)

    def get_service_release_status(self, service_name: str) -> ServiceReleaseStatus:
        self._require_service_control(_RELEASE_CAPABILITY)
        state = self.registry.get_service_release_state(service_name)
        if state is None:
            raise PipelineAssetManagementError(
                f"service release state {service_name!r} does not exist"
            )
        operation = (
            None
            if state.active_operation_id is None
            else self.registry.get_release_operation(state.active_operation_id)
        )
        if state.active_operation_id is not None and operation is None:
            raise PipelineAssetManagementError(
                "active service release operation is missing"
            )
        return ServiceReleaseStatus(state, operation)

    def prepare_service_release(
        self,
        manifest: SignedReleaseManifest,
        *,
        expected_runtime_contract: RuntimeContract,
        deployment_target_id: str,
        release_key_versions: Sequence[str],
        non_release_key_versions: Sequence[str],
        idempotency_key: str,
        actor: str,
        reason: str,
        expected_revision: int,
        read_external_state: Optional[
            Callable[[str], ReleaseExternalState]
        ] = None,
        lease_ttl_seconds: int = 120,
    ) -> ReleaseOperationRecord:
        control = self._require_service_control(_RELEASE_CAPABILITY)
        self._require_release_mutation_envelope(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
        )
        replay = self._release_replay(
            manifest.core.service_name, idempotency_key
        )
        if replay is not None:
            if (
                replay.target_release_id != manifest.release_id
                or replay.owner_principal != actor
            ):
                raise PipelineAssetManagementError(
                    "idempotency key is bound to another release request"
                )
            return replay
        state = self.registry.get_service_release_state(manifest.core.service_name)
        revision = 0 if state is None else state.revision
        if revision != expected_revision:
            raise PipelineAssetManagementError(
                "service release state does not match expected revision"
            )
        if (
            self.attestation_verifier is None
            or self.release_attestation_verifier is None
        ):
            raise PipelineAssetManagementError(
                "service release prepare requires an attestation verifier"
            )
        return _prepare_release(
            self.registry,
            control=control,
            layout=self.layout,
            manifest=manifest,
            service_name=manifest.core.service_name,
            deployment_target_id=deployment_target_id,
            expected_runtime_contract=expected_runtime_contract,
            asset_attestation_verifier=self.attestation_verifier,
            release_attestation_verifier=self.release_attestation_verifier,
            release_key_versions=release_key_versions,
            non_release_key_versions=non_release_key_versions,
            idempotency_key=idempotency_key,
            owner_principal=actor,
            read_external_state=read_external_state,
            lease_ttl_seconds=lease_ttl_seconds,
        ).operation

    def rollback_service_release(
        self,
        manifest: SignedReleaseManifest,
        *,
        expected_runtime_contract: RuntimeContract,
        deployment_target_id: str,
        release_key_versions: Sequence[str],
        non_release_key_versions: Sequence[str],
        verify_current_image: Callable[[object, datetime], None],
        verify_current_config_reference: Callable[[object, datetime], None],
        idempotency_key: str,
        actor: str,
        reason: str,
        expected_revision: int,
        target_release_id: Optional[TypedId] = None,
        read_external_state: Optional[
            Callable[[str], ReleaseExternalState]
        ] = None,
        lease_ttl_seconds: int = 120,
    ) -> ReleaseOperationRecord:
        control = self._require_service_control(_RELEASE_CAPABILITY)
        self._require_release_mutation_envelope(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
        )
        replay = self._release_replay(
            manifest.core.service_name, idempotency_key
        )
        if replay is not None:
            if (
                replay.target_release_id != manifest.release_id
                or replay.owner_principal != actor
            ):
                raise PipelineAssetManagementError(
                    "idempotency key is bound to another rollback request"
                )
            return replay
        if (
            self.attestation_verifier is None
            or self.release_attestation_verifier is None
        ):
            raise PipelineAssetManagementError(
                "service rollback requires an attestation verifier"
            )
        return _rollback_release(
            self.registry,
            control=control,
            layout=self.layout,
            manifest=manifest,
            service_name=manifest.core.service_name,
            deployment_target_id=deployment_target_id,
            expected_service_revision=expected_revision,
            expected_runtime_contract=expected_runtime_contract,
            asset_attestation_verifier=self.attestation_verifier,
            release_attestation_verifier=self.release_attestation_verifier,
            release_key_versions=release_key_versions,
            non_release_key_versions=non_release_key_versions,
            verify_current_image=verify_current_image,
            verify_current_config_reference=verify_current_config_reference,
            idempotency_key=idempotency_key,
            owner_principal=actor,
            target_release_id=target_release_id,
            read_external_state=read_external_state,
            lease_ttl_seconds=lease_ttl_seconds,
        ).prepared.operation

    def reconcile_service_release(
        self,
        service_name: str,
        *,
        operation_id: str,
        idempotency_key: str,
        actor: str,
        reason: str,
        expected_revision: int,
        max_actions: int = 3,
        lease_ttl_seconds: int = 120,
    ) -> ReleaseOperationRecord:
        self._require_service_control(_RELEASE_CAPABILITY)
        self._require_release_mutation_envelope(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
        )
        state = self.registry.get_service_release_state(service_name)
        if state is None or state.revision != expected_revision:
            raise PipelineAssetManagementError(
                "service release state does not match expected revision"
            )
        if state.active_operation_id != operation_id:
            raise PipelineAssetManagementError(
                "operation is not active for the requested service"
            )
        if self.release_kubernetes is None:
            raise PipelineAssetManagementError(
                "service release reconcile requires a Kubernetes release port"
            )
        return reconcile_release_once(
            self.registry,
            self.release_kubernetes,
            operation_id=operation_id,
            owner_principal=actor,
            evidence_issuer_principal=actor,
            max_actions=max_actions,
            lease_ttl_seconds=lease_ttl_seconds,
        ).operation

    def issue_service_runtime_authorization(
        self,
        manifest: SignedReleaseManifest,
        *,
        deployment_target_id: str,
        pod_uid: str,
        expected_runtime_contract: RuntimeContract,
        release_key_versions: Sequence[str],
        non_release_key_versions: Sequence[str],
        journal: VerifiedJournalWatermark,
        idempotency_key: str,
        actor: str,
        reason: str,
        expected_revision: int,
        max_ttl_seconds: int = 300,
    ) -> RuntimeAuthorizationLease:
        control = self._require_service_control(
            _RUNTIME_AUTHORIZATION_CAPABILITY
        )
        self._require_release_mutation_envelope(
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            expected_revision=expected_revision,
        )
        state = self.registry.get_service_release_state(
            manifest.core.service_name
        )
        if state is None or state.revision != expected_revision:
            raise PipelineAssetManagementError(
                "service release state does not match expected revision"
            )
        if (
            self.attestation_verifier is None
            or self.release_attestation_verifier is None
        ):
            raise PipelineAssetManagementError(
                "runtime authorization requires an attestation verifier"
            )
        return issue_runtime_authorization(
            self.registry,
            control=control,
            layout=self.layout,
            manifest=manifest,
            service_name=manifest.core.service_name,
            deployment_target_id=deployment_target_id,
            pod_uid=pod_uid,
            expected_runtime_contract=expected_runtime_contract,
            asset_attestation_verifier=self.attestation_verifier,
            release_attestation_verifier=self.release_attestation_verifier,
            release_key_versions=release_key_versions,
            non_release_key_versions=non_release_key_versions,
            journal=journal,
            issuer_principal=actor,
            max_ttl_seconds=max_ttl_seconds,
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

        handle = _resolve_selector(
            self.registry,
            control_document,
            self.layout,
            selector,
            usage_context,
            now,
            attestation_verifier=self.attestation_verifier,
        )
        return _download_blob(handle, self.gcs, target_path, now=now, max_size_bytes=max_size_bytes)

    def download_bundle_exact(
        self,
        asset_version_id: "TypedId | str | None" = None,
        occurrence_id: "TypedId | str | None" = None,
        *,
        target_path: Path,
        now: datetime,
        usage_context: UsageContext = UsageContext.MANUAL_DOWNLOAD,
        max_components: int = 32,
        max_total_size_bytes: Optional[int] = None,
    ) -> Path:
        """Resolve and atomically materialize an exact multi-component bundle."""
        if (asset_version_id is None) == (occurrence_id is None):
            raise PipelineAssetManagementError(
                "download_bundle_exact requires exactly one of asset_version_id/occurrence_id"
            )
        control_document = self._require_control_document()
        selector = (
            Selector.by_occurrence(_coerce_typed_id(occurrence_id))
            if occurrence_id is not None
            else Selector.by_asset_version(_coerce_typed_id(asset_version_id))
        )
        handle = _resolve_selector(
            self.registry,
            control_document,
            self.layout,
            selector,
            usage_context,
            now,
            attestation_verifier=self.attestation_verifier,
        )
        return _download_bundle(
            handle,
            self.gcs,
            target_path,
            now=now,
            max_components=max_components,
            max_total_size_bytes=max_total_size_bytes,
        )

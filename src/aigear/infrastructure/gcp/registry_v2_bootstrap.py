"""Firestore/GCS V2 bootstrap infrastructure (spec sections 6-10; Phase A T13).

Provisions the three pieces of infrastructure Phase A's V2 record types and
GCS layout (``aigear.management.v2.firestore_paths``,
``aigear.management.v2.gcs_layout``) need but cannot create for themselves:

- Firestore composite indexes for the ``blobs``/``labels``/``occurrences``/
  ``runs`` query patterns Phase A's record types are built around.
- No TTL on ``blob_claims`` or active ``operations``.  Both are correctness
  fences and are released/compacted only by a state-aware reconciler; a
  wall-clock TTL delete can reopen an adoption/delete race or erase an
  unfinished Saga.
- A prefix-scoped IAM condition binding for the GCS ``registry/v2`` admin
  root (``GcsLayoutV2.object_prefix``), so a granted principal can only ever
  touch that subtree, never the whole bucket.

Like ``iam.py``/``bucket.py``, every mutation shells out to ``gcloud`` via
``run_sh`` so it can be exercised in tests without real GCP credentials. This
module is deliberately **not** wired into ``aigear.infrastructure.gcp.infra.
Infra`` or any ``[project.scripts]`` CLI entry point: Phase A only requires
that this infrastructure be provisionable, not that it run by default (spec
acceptance criterion: the V2 subpackage must stay opt-in only). Call it
explicitly from a one-off script or a future, deliberate CLI task instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from aigear.common import run_sh
from aigear.common.logger import Logging
from aigear.management.v2.gcs_layout import GcsLayoutV2

logger = Logging(log_name=__name__).console_logging()

__all__ = [
    "FirestoreCompositeIndex",
    "RegistryV2FirestoreIndexes",
    "RegistryV2TtlPolicies",
    "RegistryV2GcsIam",
]

_ASCENDING = "ASCENDING"
_DESCENDING = "DESCENDING"


@dataclass(frozen=True)
class FirestoreCompositeIndex:
    """One ``gcloud firestore indexes composite create`` definition.

    ``fields`` is an ordered sequence of ``(field_path, order)`` pairs, where
    ``order`` is ``"ASCENDING"`` or ``"DESCENDING"``, matching Firestore's own
    composite index field-config order.
    """

    collection_group: str
    fields: Tuple[Tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.collection_group:
            raise ValueError("collection_group must be non-empty")
        if not self.fields:
            raise ValueError("fields must be non-empty")
        for field_path, order in self.fields:
            if order not in (_ASCENDING, _DESCENDING):
                raise ValueError(
                    f"order must be {_ASCENDING!r} or {_DESCENDING!r}, got {order!r}"
                )
            if not field_path:
                raise ValueError("field_path must be non-empty")


class RegistryV2FirestoreIndexes:
    """Creates the Phase A composite indexes for the V2 registry namespace.

    These are the query patterns Phase A's record types (``BlobRecord``,
    ``LabelRecord``, ``OccurrenceRecord``, ``RunRecord``) are built around;
    Phase B adds more fields to ``RunRecord``/``StepRecord``/``AttemptRecord``
    (full ``RunSpec``, lease/fencing) and will need additional indexes then.
    """

    def __init__(self, project_id: str, database_id: str) -> None:
        self.project_id = project_id
        self.database_id = database_id

    @staticmethod
    def default_index_definitions() -> Tuple[FirestoreCompositeIndex, ...]:
        return (
            FirestoreCompositeIndex(
                collection_group="blobs",
                fields=(("availability_state", _ASCENDING), ("created_at", _DESCENDING)),
            ),
            FirestoreCompositeIndex(
                collection_group="labels",
                fields=(
                    ("asset_type", _ASCENDING),
                    ("asset_name", _ASCENDING),
                    ("display_version", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="asset_versions",
                fields=(
                    ("asset_type", _ASCENDING),
                    ("name", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("asset_version_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="asset_versions",
                fields=(
                    ("asset_type", _ASCENDING),
                    ("name", _ASCENDING),
                    ("lifecycle_state", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("asset_version_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="asset_versions",
                fields=(
                    ("asset_type", _ASCENDING),
                    ("name", _ASCENDING),
                    ("trust_state", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("asset_version_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="asset_versions",
                fields=(
                    ("asset_type", _ASCENDING),
                    ("name", _ASCENDING),
                    ("lifecycle_state", _ASCENDING),
                    ("trust_state", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("asset_version_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="occurrences",
                fields=(
                    ("run_id", _ASCENDING),
                    ("status", _ASCENDING),
                    ("committed_at", _ASCENDING),
                    ("occurrence_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="occurrences",
                fields=(
                    ("run_id", _ASCENDING),
                    ("status", _ASCENDING),
                    ("step_name", _ASCENDING),
                    ("committed_at", _ASCENDING),
                    ("occurrence_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="occurrences",
                fields=(("asset_version_id", _ASCENDING), ("status", _ASCENDING)),
            ),
            FirestoreCompositeIndex(
                collection_group="runs",
                fields=(("status", _ASCENDING), ("run_id", _ASCENDING)),
            ),
            FirestoreCompositeIndex(
                collection_group="releases",
                fields=(
                    ("service_name", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("release_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="release_operations",
                fields=(
                    ("service_name", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("operation_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="aliases",
                fields=(("updated_at", _ASCENDING), ("alias_name", _ASCENDING)),
            ),
            FirestoreCompositeIndex(
                collection_group="runtime_evidence",
                fields=(("issued_at", _ASCENDING), ("evidence_id", _ASCENDING)),
            ),
            FirestoreCompositeIndex(
                collection_group="runtime_authorization_leases",
                fields=(("issued_at", _ASCENDING), ("lease_id", _ASCENDING)),
            ),
            FirestoreCompositeIndex(
                collection_group="outbox",
                fields=(
                    ("status", _ASCENDING),
                    ("next_attempt_at", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("event_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="outbox",
                fields=(
                    ("status", _ASCENDING),
                    ("lease_expires_at", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("event_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="outbox",
                fields=(
                    ("status", _ASCENDING),
                    ("kind", _ASCENDING),
                    ("next_attempt_at", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("event_id", _ASCENDING),
                ),
            ),
            FirestoreCompositeIndex(
                collection_group="outbox",
                fields=(
                    ("status", _ASCENDING),
                    ("kind", _ASCENDING),
                    ("lease_expires_at", _ASCENDING),
                    ("created_at", _ASCENDING),
                    ("event_id", _ASCENDING),
                ),
            ),
        )

    def create(self, index: FirestoreCompositeIndex) -> None:
        command = [
            "gcloud",
            "firestore",
            "indexes",
            "composite",
            "create",
            f"--collection-group={index.collection_group}",
            f"--database={self.database_id}",
            f"--project={self.project_id}",
        ]
        for field_path, order in index.fields:
            command.append(f"--field-config=field-path={field_path},order={order}")
        run_sh(command, check=True)
        logger.info(
            f"Requested Firestore composite index on {index.collection_group!r} "
            f"({', '.join(f'{p} {o}' for p, o in index.fields)})."
        )

    def create_all(self, indexes: Optional[Sequence[FirestoreCompositeIndex]] = None) -> None:
        for index in indexes if indexes is not None else self.default_index_definitions():
            self.create(index)


class RegistryV2TtlPolicies:
    """Enable TTL only for explicitly reviewed, non-authoritative collections.

    Lease expiry is a takeover condition, not permission to delete the
    document. Claims, operations and tombstones therefore have no automatic
    TTL; reconcile verifies their external effects before compacting them.
    """

    DEFAULT_TTL_FIELDS: Tuple[Tuple[str, str], ...] = ()
    FORBIDDEN_TTL_COLLECTIONS = frozenset({"blob_claims", "operations", "tombstones"})

    def __init__(self, project_id: str, database_id: str) -> None:
        self.project_id = project_id
        self.database_id = database_id

    def enable(self, collection_group: str, ttl_field: str) -> None:
        if collection_group in self.FORBIDDEN_TTL_COLLECTIONS:
            raise ValueError(
                f"TTL is forbidden on correctness-fence collection {collection_group!r}; "
                "use the state-aware reconciler"
            )
        command = [
            "gcloud",
            "firestore",
            "fields",
            "ttls",
            "update",
            ttl_field,
            f"--collection-group={collection_group}",
            f"--database={self.database_id}",
            f"--project={self.project_id}",
            "--enable-ttl",
        ]
        run_sh(command, check=True)
        logger.info(f"Enabled TTL on {collection_group}.{ttl_field}.")

    def enable_all(self, ttl_fields: Optional[Sequence[Tuple[str, str]]] = None) -> None:
        for collection_group, ttl_field in (
            ttl_fields if ttl_fields is not None else self.DEFAULT_TTL_FIELDS
        ):
            self.enable(collection_group, ttl_field)


class RegistryV2GcsIam:
    """Grants prefix-scoped IAM bindings for one ``registry/v2`` admin root.

    Reuses :class:`~aigear.management.v2.gcs_layout.GcsLayoutV2` for the
    managed root's object prefix so this can never drift from the layout
    finalizer/resolver/etc. actually read and write.
    """

    def __init__(self, layout: GcsLayoutV2, project_id: str) -> None:
        self.layout = layout
        self.project_id = project_id

    @property
    def bucket_gs(self) -> str:
        return f"gs://{self.layout.bucket_name}"

    @property
    def condition_title(self) -> str:
        return f"registry-v2-{self.layout.project_name}-{self.layout.pipeline_version}"

    def condition_expression(self) -> str:
        return (
            'resource.name.startsWith('
            f'"projects/_/buckets/{self.layout.bucket_name}/objects/{self.layout.object_prefix}/")'
        )

    def add_prefix_scoped_binding(
        self, member: str, role: str = "roles/storage.objectAdmin"
    ) -> None:
        condition = (
            f"expression={self.condition_expression()},"
            f"title={self.condition_title},"
            f"description=Scoped to the {self.layout.object_prefix}/ V2 admin root only"
        )
        command = [
            "gcloud",
            "storage",
            "buckets",
            "add-iam-policy-binding",
            self.bucket_gs,
            f"--member={member}",
            f"--role={role}",
            f"--condition={condition}",
            f"--project={self.project_id}",
        ]
        run_sh(command, check=True)
        logger.info(
            f"Granted {role} to {member} scoped to {self.layout.object_prefix}/ in {self.bucket_gs}."
        )

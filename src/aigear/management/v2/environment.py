"""Environment identity and registry binding for Pipeline V2 (spec section 2.1).

Two distinct concepts must never be merged into a single fingerprint:

- :class:`EnvironmentIdentity` / :func:`compute_environment_fingerprint` — the
  *stable* identity of a deployment (one GCP Project per environment). It must
  stay identical across a Firestore backup/restore, so it deliberately never
  includes ``firestore_database_id``, a GCS generation or a KMS key version.
- :class:`RegistryBinding` — the *mutable* pointer from that stable
  environment to the currently bound Firestore database. It carries a
  per-bind/rebind random nonce and a monotonically increasing epoch so a
  restored or replayed binding can never be silently reused.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass

from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "InvalidEnvironmentIdentityError",
    "InvalidRegistryBindingError",
    "MIN_REGISTRY_BINDING_ID_ENTROPY_BYTES",
    "EnvironmentIdentity",
    "compute_environment_fingerprint",
    "RegistryBinding",
    "generate_registry_binding_id",
    "validate_registry_binding_epoch",
]

MIN_REGISTRY_BINDING_ID_ENTROPY_BYTES = 32  # 256 bits


class InvalidEnvironmentIdentityError(ValueError):
    """Raised when an :class:`EnvironmentIdentity` field is missing or malformed."""


class InvalidRegistryBindingError(ValueError):
    """Raised when a :class:`RegistryBinding` field is missing or malformed."""


@dataclass(frozen=True)
class EnvironmentIdentity:
    """The stable fields that make up ``environment_fingerprint``.

    Every field is required and must be a non-empty string; there is no safe
    default for any of them because a missing value would silently narrow the
    fingerprint's identity space.
    """

    environment_id: str
    gcp_project_number: str
    project_name: str
    pipeline_version: str
    asset_bucket_name: str
    asset_bucket_location: str
    kms_trust_domain: str

    def __post_init__(self) -> None:
        for field_name in (
            "environment_id",
            "gcp_project_number",
            "project_name",
            "pipeline_version",
            "asset_bucket_name",
            "asset_bucket_location",
            "kms_trust_domain",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise InvalidEnvironmentIdentityError(
                    f"EnvironmentIdentity.{field_name} must be a non-empty str, "
                    f"got {value!r}"
                )


def compute_environment_fingerprint(identity: EnvironmentIdentity) -> TypedId:
    """Compute ``environment_fingerprint = sha256(JCS([...]))`` per spec 2.1."""
    payload = [
        "aigear.environment.v2",
        identity.environment_id,
        identity.gcp_project_number,
        identity.project_name,
        identity.pipeline_version,
        identity.asset_bucket_name,
        identity.asset_bucket_location,
        identity.kms_trust_domain,
    ]
    return TypedId.from_bare(digest_sha256_of_jcs(payload))


def generate_registry_binding_id() -> str:
    """Generate a fresh, at-least-256-bit, non-reusable binding nonce."""
    return secrets.token_urlsafe(MIN_REGISTRY_BINDING_ID_ENTROPY_BYTES)


def _decoded_entropy_bytes(value: str) -> int:
    padded = value + "=" * (-len(value) % 4)
    try:
        return len(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError) as exc:
        raise InvalidRegistryBindingError(
            f"registry_binding_id must be base64url-encoded: {value!r}"
        ) from exc


def validate_registry_binding_epoch(
    candidate_epoch: int, ever_issued_watermark: int
) -> None:
    """Enforce that a new epoch exceeds the security journal's watermark.

    Per spec 2.1: "``registry_binding_epoch`` 必须大于 security journal 的
    ever-issued watermark，不能只对恢复 snapshot 中的旧值 +1". Callers must pass
    the durable, append-only watermark (the highest epoch ever issued across
    all binds/rebinds, including ones lost from a restored snapshot) rather
    than just the locally remembered previous epoch.
    """
    if candidate_epoch <= ever_issued_watermark:
        raise InvalidRegistryBindingError(
            "registry_binding_epoch must exceed the ever-issued watermark "
            f"({ever_issued_watermark}), got {candidate_epoch}"
        )


@dataclass(frozen=True)
class RegistryBinding:
    """The mutable binding from a stable environment to a Firestore database."""

    firestore_database_id: str
    registry_binding_id: str
    registry_binding_epoch: int
    bound_environment_fingerprint: TypedId

    def __post_init__(self) -> None:
        if not isinstance(self.firestore_database_id, str) or not self.firestore_database_id:
            raise InvalidRegistryBindingError(
                "firestore_database_id must be a non-empty str, "
                f"got {self.firestore_database_id!r}"
            )
        entropy_bytes = _decoded_entropy_bytes(self.registry_binding_id)
        if entropy_bytes < MIN_REGISTRY_BINDING_ID_ENTROPY_BYTES:
            raise InvalidRegistryBindingError(
                "registry_binding_id must decode to at least "
                f"{MIN_REGISTRY_BINDING_ID_ENTROPY_BYTES} bytes of entropy, "
                f"got {entropy_bytes} bytes"
            )
        if not isinstance(self.registry_binding_epoch, int) or isinstance(
            self.registry_binding_epoch, bool
        ) or self.registry_binding_epoch < 1:
            raise InvalidRegistryBindingError(
                "registry_binding_epoch must be a positive int, "
                f"got {self.registry_binding_epoch!r}"
            )
        if not isinstance(self.bound_environment_fingerprint, TypedId):
            raise InvalidRegistryBindingError(
                "bound_environment_fingerprint must be a TypedId, "
                f"got {type(self.bound_environment_fingerprint)!r}"
            )

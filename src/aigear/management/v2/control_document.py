"""The ``registries/v2`` control document (spec sections 7 and 7.1).

The control document is the single control-plane record that every V2
reader/writer must validate before touching business data: it carries the
schema version, migration ``authority``/``phase``, the monotonic
``write_epoch``, and the current Firestore database binding (see
:mod:`aigear.management.v2.environment`). This module models it as an
immutable, self-validating dataclass; persistence (Firestore get/CAS) is left
to a later task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from aigear.management.v2.environment import RegistryBinding
from aigear.management.v2.identifiers import TypedId

__all__ = [
    "InvalidControlDocumentError",
    "parse_schema_version",
    "ControlDocument",
]

_SCHEMA_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

# Spec sections 4.2/22.3 use "v1"/"v2" as the two authority values that
# exist today; a hard cutover only ever flips between them.
_VALID_AUTHORITIES = ("v1", "v2")


class InvalidControlDocumentError(ValueError):
    """Raised when a control document field is missing or malformed."""


def parse_schema_version(value: str) -> Tuple[int, int]:
    """Parse a canonical ``"<major>.<minor>"`` schema version string.

    Per spec 7.1, both parts are non-negative decimal integers with no
    leading zero; "2", "2.0.1" and "02.0" are all rejected even though a
    naive numeric or lexicographic comparison might accept them.
    """
    if not isinstance(value, str):
        raise InvalidControlDocumentError(f"schema_version must be a str, got {value!r}")
    match = _SCHEMA_VERSION_RE.fullmatch(value)
    if not match:
        raise InvalidControlDocumentError(
            f'schema_version must match "<major>.<minor>" with no leading zeros, '
            f"got {value!r}"
        )
    return int(match.group(1)), int(match.group(2))


@dataclass(frozen=True)
class ControlDocument:
    """Immutable snapshot of the ``registries/v2`` control document.

    ``phase`` is intentionally left as a validated non-empty string rather
    than a closed enum: the full V1->V2 migration phase state machine is
    defined in spec chapter 23, which is out of scope for Phase A. Tighten
    this once that state machine is implemented.
    """

    schema_version: str
    environment_id: str
    authority: str
    phase: str
    write_epoch: int
    min_reader_version: str
    min_writer_version: str
    required_capabilities: Tuple[str, ...]
    environment_fingerprint: TypedId
    registry_binding: RegistryBinding
    registry_bound_by: str
    registry_rebind_operation_id: Optional[str] = None
    v1_compat_projection_enabled_until: Optional[str] = None
    old_writer_fenced_at: Optional[str] = None
    firestore_database_resource: Optional[str] = None
    applied_security_watermark: int = 0
    security_journal_head_sequence: int = 0
    security_journal_head_digest: Optional[TypedId] = None
    security_journal_head_uri: Optional[str] = None
    security_head_last_verified_at: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.required_capabilities, list):
            object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))

        parse_schema_version(self.schema_version)
        parse_schema_version(self.min_reader_version)
        parse_schema_version(self.min_writer_version)

        if self.authority not in _VALID_AUTHORITIES:
            raise InvalidControlDocumentError(
                f"authority must be one of {_VALID_AUTHORITIES}, got {self.authority!r}"
            )
        if not isinstance(self.environment_id, str) or not self.environment_id:
            raise InvalidControlDocumentError("environment_id must be a non-empty str")
        if not isinstance(self.phase, str) or not self.phase:
            raise InvalidControlDocumentError("phase must be a non-empty str")
        if (
            isinstance(self.write_epoch, bool)
            or not isinstance(self.write_epoch, int)
            or self.write_epoch < 1
        ):
            raise InvalidControlDocumentError(
                f"write_epoch must be a positive int, got {self.write_epoch!r}"
            )
        if not self.required_capabilities or not all(
            isinstance(capability, str) and capability
            for capability in self.required_capabilities
        ):
            raise InvalidControlDocumentError(
                "required_capabilities must be a non-empty sequence of non-empty strings"
            )
        if not isinstance(self.environment_fingerprint, TypedId):
            raise InvalidControlDocumentError("environment_fingerprint must be a TypedId")
        if not isinstance(self.registry_binding, RegistryBinding):
            raise InvalidControlDocumentError("registry_binding must be a RegistryBinding")
        if self.registry_binding.bound_environment_fingerprint != self.environment_fingerprint:
            raise InvalidControlDocumentError(
                "registry_binding.bound_environment_fingerprint must match "
                "environment_fingerprint"
            )
        if not isinstance(self.registry_bound_by, str) or not self.registry_bound_by:
            raise InvalidControlDocumentError("registry_bound_by must be a non-empty str")
        for field_name in ("applied_security_watermark", "security_journal_head_sequence"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidControlDocumentError(
                    f"{field_name} must be a non-negative int, got {value!r}"
                )
        journal_fields = (
            self.security_journal_head_digest,
            self.security_journal_head_uri,
            self.security_head_last_verified_at,
        )
        if self.security_journal_head_sequence == 0:
            if any(value is not None for value in journal_fields):
                raise InvalidControlDocumentError(
                    "security journal head fields must be null at sequence 0"
                )
        elif any(value is None for value in journal_fields):
            raise InvalidControlDocumentError(
                "non-zero security_journal_head_sequence requires digest, URI and verified_at"
            )
        if self.firestore_database_resource is not None:
            parts = self.firestore_database_resource.split("/")
            if (
                len(parts) != 4
                or parts[0] != "projects"
                or not parts[1]
                or parts[2] != "databases"
                or not parts[3]
            ):
                raise InvalidControlDocumentError(
                    "firestore_database_resource must be a full projects/.../databases/... resource"
                )
            if parts[3] != self.registry_binding.firestore_database_id:
                raise InvalidControlDocumentError(
                    "firestore_database_resource database must match registry_binding database_id"
                )

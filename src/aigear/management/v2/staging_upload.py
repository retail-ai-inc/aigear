"""Worker staging-upload helpers (spec section 6.5, steps 1-5).

Covers only the parts of the worker-side staging protocol that do not
require a real GCS client call:

1. Local output file safety validation (step 1): reject symlink/junction,
   non-regular files, path escape out of the worker's own output directory,
   and platform-reserved file names.
2. The completion message a worker publishes after uploading every declared
   output to ``_staging`` (step 5): ``StagingOutputDescriptor`` (one per
   output slot: staging object, generation, digest, size) and
   ``StepCompletionMessage`` (the whole Step's descriptors).
3. ``validate_step_completion_message``: reject a message that does not
   cover a RunSpec Step's declared outputs exactly once each (missing,
   duplicate or unknown output_name).

Deliberately out of scope here: the actual GCS create-only upload call
(step 3), and building/verifying the ``_staging`` object name itself --
``GcsLayoutV2.staging(...)`` (T5) already does that and callers should use
it directly when uploading. Re-deriving and cross-checking the expected
staging object name from a completion message is the finalizer's job (T22),
not the worker's.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.canonical import digest_sha256_of_jcs
from aigear.management.v2.gcs_client import GcsClientV2
from aigear.management.v2.gcs_layout import GcsLayoutV2
from aigear.management.v2.naming import validate_segment
from aigear.management.v2.records.asset_version import compute_component_key
from aigear.management.v2.records.run_spec import RunSpec

__all__ = [
    "StagingUploadError",
    "validate_local_output_file",
    "stage_output_file",
    "stage_component_file",
    "stage_attachment_file",
    "StagingComponentDescriptor",
    "StagingAttachmentDescriptor",
    "StagingOutputDescriptor",
    "StepCompletionMessage",
    "validate_step_completion_message",
]


class StagingUploadError(ValueError):
    """Raised for an unsafe local output file or a malformed completion message."""


# Windows reserved device names (case-insensitive, regardless of extension).
# Spec 6.5 step 1 requires rejecting these because a name like ``CON.json``
# is unusable on that platform even though it looks like an ordinary file.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def validate_local_output_file(path: Path, *, base_dir: Path) -> Path:
    """Reject an output file that is unsafe to stage (spec 6.5 step 1).

    Rejects a symlink/junction at ``path`` itself, a resolved location
    outside ``base_dir`` (path escape, including via an intermediate
    symlinked directory), a non-regular file, and a platform-reserved name.
    Returns the resolved, validated path.
    """
    if path.is_symlink():
        raise StagingUploadError(f"output file must not be a symlink/junction: {path}")

    try:
        resolved_base = base_dir.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        # Some platform-reserved names (e.g. Windows ``NUL``) cannot even be
        # resolved as a real filesystem entry, which is itself a reason to
        # reject them alongside the explicit name check below.
        raise StagingUploadError(f"unable to resolve output file {path}: {exc}") from exc

    try:
        resolved_path.relative_to(resolved_base)
    except ValueError as exc:
        raise StagingUploadError(
            f"output file {path} escapes base_dir {base_dir}"
        ) from exc

    if not resolved_path.is_file():
        raise StagingUploadError(f"output file must be a regular file: {path}")

    name_without_suffix = resolved_path.name.split(".", 1)[0].upper()
    if name_without_suffix in _WINDOWS_RESERVED_NAMES:
        raise StagingUploadError(
            f"output file name is a platform-reserved name: {resolved_path.name!r}"
        )

    return resolved_path


def _require_non_empty_str(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise StagingUploadError(f"{field_name} must be a non-empty str, got {value!r}")


def _require_positive_int(field_name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StagingUploadError(f"{field_name} must be a positive int, got {value!r}")


@dataclass(frozen=True)
class StagingComponentDescriptor:
    role: str
    logical_name: str
    staging_object: str
    generation: str
    digest: TypedId
    size: int
    media_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", validate_segment(self.role, field_name="role"))
        object.__setattr__(
            self,
            "logical_name",
            validate_segment(self.logical_name, field_name="logical_name"),
        )
        _validate_staged_payload(self)


@dataclass(frozen=True)
class StagingAttachmentDescriptor:
    attachment_kind: str
    logical_name: str
    staging_object: str
    generation: str
    digest: TypedId
    size: int
    media_type: str

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
        _validate_staged_payload(self)


def _validate_staged_payload(value) -> None:
    _require_non_empty_str("staging_object", value.staging_object)
    _require_non_empty_str("generation", value.generation)
    if not isinstance(value.digest, TypedId):
        raise StagingUploadError(f"digest must be a TypedId, got {type(value.digest)!r}")
    if isinstance(value.size, bool) or not isinstance(value.size, int) or value.size < 0:
        raise StagingUploadError(f"size must be a non-negative int, got {value.size!r}")
    _require_non_empty_str("media_type", value.media_type)


@dataclass(frozen=True)
class StagingOutputDescriptor:
    """One declared output's staged-upload result (spec 6.5 step 5).

    ``digest``/``size`` must come from the same immutable local snapshot
    that was uploaded (spec 6.5 step 2), not a later re-read of a mutable
    path.
    """

    output_name: str
    staging_object: str
    generation: str
    digest: TypedId
    size: int
    media_type: str
    additional_components: Tuple[StagingComponentDescriptor, ...] = ()
    attachments: Tuple[StagingAttachmentDescriptor, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "output_name", validate_segment(self.output_name, field_name="output_name")
        )
        if isinstance(self.additional_components, list):
            object.__setattr__(self, "additional_components", tuple(self.additional_components))
        if isinstance(self.attachments, list):
            object.__setattr__(self, "attachments", tuple(self.attachments))
        _validate_staged_payload(self)
        if not all(
            isinstance(value, StagingComponentDescriptor)
            for value in self.additional_components
        ):
            raise StagingUploadError(
                "additional_components must contain StagingComponentDescriptor values"
            )
        if not all(
            isinstance(value, StagingAttachmentDescriptor) for value in self.attachments
        ):
            raise StagingUploadError(
                "attachments must contain StagingAttachmentDescriptor values"
            )
        component_keys = [
            (value.role, value.logical_name) for value in self.additional_components
        ]
        if len(set(component_keys)) != len(component_keys):
            raise StagingUploadError(
                "additional components must be unique by (role, logical_name)"
            )
        attachment_keys = [
            (value.attachment_kind, value.logical_name) for value in self.attachments
        ]
        if len(set(attachment_keys)) != len(attachment_keys):
            raise StagingUploadError(
                "attachments must be unique by (attachment_kind, logical_name)"
            )


def stage_output_file(
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    *,
    local_path: Path,
    base_dir: Path,
    run_id: str,
    step_name: str,
    attempt_no: int,
    operation_id: str,
    output_name: str,
    payload_kind: str,
    payload_key: str,
    media_type: str,
) -> StagingOutputDescriptor:
    """Hash one immutable local snapshot and create-only upload it to staging."""
    resolved = validate_local_output_file(local_path, base_dir=base_dir)
    digest = hashlib.sha256()
    size = 0
    with open(resolved, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    typed_digest = TypedId.from_bare(digest.hexdigest())
    object_name = layout.staging(
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt_no,
        operation_id=operation_id,
        output_name=output_name,
        payload_kind=payload_kind,
        payload_key=payload_key,
        file_name=resolved.name,
    )
    snapshot = gcs.upload_file(
        object_name,
        resolved,
        if_generation_match=0,
        expected_sha256=typed_digest.bare,
    )
    if snapshot.sha256 != typed_digest.bare or snapshot.size_bytes != size:
        raise StagingUploadError("uploaded staging object does not match the local immutable snapshot")
    return StagingOutputDescriptor(
        output_name=output_name,
        staging_object=object_name,
        generation=snapshot.generation,
        digest=typed_digest,
        size=size,
        media_type=media_type,
    )


def stage_component_file(
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    *,
    local_path: Path,
    base_dir: Path,
    run_id: str,
    step_name: str,
    attempt_no: int,
    operation_id: str,
    output_name: str,
    role: str,
    logical_name: str,
    media_type: str,
) -> StagingComponentDescriptor:
    """Create-only stage one additional identity component."""
    component_key = compute_component_key(role, logical_name).bare
    staged = stage_output_file(
        gcs,
        layout,
        local_path=local_path,
        base_dir=base_dir,
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt_no,
        operation_id=operation_id,
        output_name=output_name,
        payload_kind="components",
        payload_key=component_key,
        media_type=media_type,
    )
    return StagingComponentDescriptor(
        role=role,
        logical_name=logical_name,
        staging_object=staged.staging_object,
        generation=staged.generation,
        digest=staged.digest,
        size=staged.size,
        media_type=staged.media_type,
    )


def stage_attachment_file(
    gcs: GcsClientV2,
    layout: GcsLayoutV2,
    *,
    local_path: Path,
    base_dir: Path,
    run_id: str,
    step_name: str,
    attempt_no: int,
    operation_id: str,
    output_name: str,
    attachment_kind: str,
    logical_name: str,
    media_type: str,
) -> StagingAttachmentDescriptor:
    """Create-only stage one non-identity attachment."""
    payload_key = digest_sha256_of_jcs(
        ["aigear.attachment-slot.v2", attachment_kind, logical_name]
    )
    staged = stage_output_file(
        gcs,
        layout,
        local_path=local_path,
        base_dir=base_dir,
        run_id=run_id,
        step_name=step_name,
        attempt_no=attempt_no,
        operation_id=operation_id,
        output_name=output_name,
        payload_kind="attachments",
        payload_key=payload_key,
        media_type=media_type,
    )
    return StagingAttachmentDescriptor(
        attachment_kind=attachment_kind,
        logical_name=logical_name,
        staging_object=staged.staging_object,
        generation=staged.generation,
        digest=staged.digest,
        size=staged.size,
        media_type=staged.media_type,
    )


@dataclass(frozen=True)
class StepCompletionMessage:
    """The one-shot completion message a worker publishes for a Step Attempt
    (spec 6.5 step 5): every declared output's staged-upload result, all at once."""

    run_id: str
    step_name: str
    attempt_no: int
    operation_id: str
    outputs: Tuple[StagingOutputDescriptor, ...]
    fencing_token: Optional[int] = None
    firestore_database_id: Optional[str] = None
    firestore_database_resource: Optional[str] = None
    registry_binding_id: Optional[str] = None
    registry_binding_epoch: Optional[int] = None
    write_epoch: Optional[int] = None
    publisher_principal: Optional[str] = None
    message_id: Optional[str] = None
    issued_at: Optional[str] = None
    expires_at: Optional[str] = None

    def __post_init__(self) -> None:
        if isinstance(self.outputs, list):
            object.__setattr__(self, "outputs", tuple(self.outputs))

        object.__setattr__(self, "run_id", validate_segment(self.run_id, field_name="run_id"))
        object.__setattr__(
            self, "step_name", validate_segment(self.step_name, field_name="step_name")
        )
        _require_positive_int("attempt_no", self.attempt_no)
        _require_non_empty_str("operation_id", self.operation_id)

        security_values = (
            self.fencing_token,
            self.firestore_database_id,
            self.firestore_database_resource,
            self.registry_binding_id,
            self.registry_binding_epoch,
            self.write_epoch,
            self.publisher_principal,
            self.message_id,
            self.issued_at,
            self.expires_at,
        )
        if any(value is not None for value in security_values) and any(
            value is None for value in security_values
        ):
            raise StagingUploadError(
                "completion security envelope must provide fencing_token, firestore_database_id, "
                "firestore_database_resource, registry_binding_id/epoch, write_epoch, "
                "publisher_principal and message_id together"
            )
        if self.fencing_token is not None:
            _require_positive_int("fencing_token", self.fencing_token)
            _require_non_empty_str("firestore_database_id", self.firestore_database_id)
            _require_non_empty_str("firestore_database_resource", self.firestore_database_resource)
            _require_non_empty_str("registry_binding_id", self.registry_binding_id)
            _require_positive_int("registry_binding_epoch", self.registry_binding_epoch)
            _require_positive_int("write_epoch", self.write_epoch)
            _require_non_empty_str("publisher_principal", self.publisher_principal)
            _require_non_empty_str("message_id", self.message_id)
            _require_non_empty_str("issued_at", self.issued_at)
            _require_non_empty_str("expires_at", self.expires_at)

        if not self.outputs or not all(
            isinstance(output, StagingOutputDescriptor) for output in self.outputs
        ):
            raise StagingUploadError(
                "outputs must be a non-empty sequence of StagingOutputDescriptor"
            )
        output_names = [output.output_name for output in self.outputs]
        if len(set(output_names)) != len(output_names):
            raise StagingUploadError(f"outputs must be unique by output_name, got {output_names!r}")


def validate_step_completion_message(message: StepCompletionMessage, run_spec: RunSpec) -> None:
    """Reject a completion message that does not cover its Step's declared
    outputs exactly once each (spec 6.5 step 5: "该 Step 的全部声明输出...
    缺失/重复/未知一律拒绝"). Duplicate ``output_name`` within ``message`` is
    already rejected by :class:`StepCompletionMessage` itself.
    """
    step = next((s for s in run_spec.steps if s.step_name == message.step_name), None)
    if step is None:
        raise StagingUploadError(
            f"RunSpec has no step named {message.step_name!r}"
        )

    declared_output_names = {output.output_name for output in step.outputs}
    reported_output_names = {output.output_name for output in message.outputs}

    missing = declared_output_names - reported_output_names
    if missing:
        raise StagingUploadError(
            f"completion message for step {message.step_name!r} is missing required "
            f"outputs: {sorted(missing)!r}"
        )

    unknown = reported_output_names - declared_output_names
    if unknown:
        raise StagingUploadError(
            f"completion message for step {message.step_name!r} references unknown "
            f"outputs: {sorted(unknown)!r}"
        )

    slots = {output.output_name: output for output in step.outputs}
    for reported in message.outputs:
        slot = slots[reported.output_name]
        expected_components = {
            (item.role, item.logical_name) for item in slot.additional_components
        }
        actual_components = {
            (item.role, item.logical_name) for item in reported.additional_components
        }
        if actual_components != expected_components:
            raise StagingUploadError(
                f"completion message component set for output {reported.output_name!r} "
                f"does not match RunSpec: expected {sorted(expected_components)!r}, "
                f"got {sorted(actual_components)!r}"
            )
        expected_attachments = {
            (item.attachment_kind, item.logical_name) for item in slot.attachments
        }
        actual_attachments = {
            (item.attachment_kind, item.logical_name) for item in reported.attachments
        }
        if actual_attachments != expected_attachments:
            raise StagingUploadError(
                f"completion message attachment set for output {reported.output_name!r} "
                f"does not match RunSpec: expected {sorted(expected_attachments)!r}, "
                f"got {sorted(actual_attachments)!r}"
            )

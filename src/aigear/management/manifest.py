"""Version-one asset data contracts; no storage or execution side effects.

Production revisions are opaque immutable strings. Manifest revisions are local
nonnegative counters and must never be used as production identities.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field,
    StringConstraints, ValidationError, field_validator, model_validator,
)

from aigear.management import asset_paths as paths
from aigear.management.asset_errors import IntegrityError

Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
Identifier = Annotated[Text, AfterValidator(paths.identifier)]
ArtifactPath = Annotated[Text, AfterValidator(paths.artifact_path)]
ReleasePath = Annotated[Text, AfterValidator(paths.release_path)]
ObjectKey = Annotated[Text, AfterValidator(paths.relative_path)]
Generation = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]+$")]
Counter = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
StepStatus = Literal["missing", "running", "succeeded", "failed", "stale"]
OperationStatus = Literal["idle", "preparing", "copying", "moving", "cleaning", "incomplete", "completed"]


def _time_input(value):
    if not isinstance(value, (datetime, str)):
        raise ValueError("Time must be an ISO datetime or datetime object")
    return value


UTCTime = Annotated[datetime, BeforeValidator(_time_input), AfterValidator(paths.utc_datetime)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class ObjectVersion(Contract):
    key: ObjectKey
    generation: Generation


class ArtifactObject(Contract):
    path: ArtifactPath
    size: Counter
    crc32c: Text
    generation: Generation


class ProductionIdentity(Contract):
    producer_run_id: Identifier
    producer_step_revision: Text


class ManagedInput(Contract):
    kind: Literal["managed"] = "managed"
    run_id: Identifier
    step_name: Text
    step_revision: Text
    artifact: ArtifactPath
    observed_object: ObjectVersion


class ExternalInput(Contract):
    """Only sanitized URI/name/version data may be supplied by managed IO."""

    kind: Literal["external"] = "external"
    name: Text
    uri: Optional[Text] = None
    version: Optional[Text] = None


Input = Annotated[Union[ManagedInput, ExternalInput], Field(discriminator="kind")]


def _unique(values, description):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {description}")


class StepRecord(Contract):
    status: StepStatus
    producer_run_id: Optional[Identifier] = None
    producer_step_revision: Optional[Text] = None
    completed_at: Optional[UTCTime] = None
    inputs: List[Input] = Field(default_factory=list)
    outputs: List[ArtifactObject] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_production(self):
        identity = (self.producer_run_id, self.producer_step_revision, self.completed_at)
        if any(v is not None for v in identity) and not all(v is not None for v in identity):
            raise ValueError("Production run, step revision and completion time must be recorded together")
        if self.status == "succeeded" or self.outputs or self.inputs:
            if not all(v is not None for v in identity):
                raise ValueError("Committed production records require a complete identity")
        paths.validate_file_set(o.path for o in self.outputs)
        return self


class Execution(Contract):
    run_id: Identifier
    run_started_at: UTCTime
    owner_token: Text
    status: Literal["running", "waiting", "succeeded", "failed", "finalizing"]
    current_step: Optional[Text] = None
    step_attempt_token: Optional[Text] = None
    committed_steps: List[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_attempt(self):
        if (self.current_step is None) != (self.step_attempt_token is None):
            raise ValueError("Current step and attempt token must be recorded together")
        _unique(self.committed_steps, "committed execution steps")
        return self


class PendingStep(Contract):
    run_id: Identifier
    step_name: Text
    step_attempt_token: Text
    inputs: List[Input] = Field(default_factory=list)
    outputs: List[ArtifactObject] = Field(default_factory=list)
    writes_started: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_outputs(self):
        paths.validate_file_set(o.path for o in self.outputs)
        if self.outputs and not self.writes_started:
            raise ValueError("Pending outputs require writes_started")
        return self


class RestoredFrom(Contract):
    # Release backups may combine several producer runs and have no snapshot run.
    run_id: Optional[Identifier] = None
    manifest: ObjectVersion
    operation_id: Text
    restored_at: UTCTime


class ObjectExpectation(Contract):
    key: ObjectKey
    # None means an object must not exist, never an unconditional write.
    generation: Optional[Generation]


class VerifiedObject(Contract):
    source: ObjectVersion
    target: ObjectVersion
    size: Counter
    crc32c: Text
    source_deleted: bool = Field(default=False, strict=True)
    custom_time_set: bool = Field(default=False, strict=True)


class PendingOperation(Contract):
    operation_id: Text
    kind: Literal["register", "seal", "retire", "restore", "publish", "rollback"]
    phase: Text
    source: FixedManifest
    # Prior workspace/release/backup content needed for cleanup and recovery.
    previous_targets: List[FixedManifest] = Field(default_factory=list)
    expected_targets: List[ObjectExpectation]
    progress: List[VerifiedObject] = Field(default_factory=list)
    target: Optional[Literal["running", "assets"]] = None
    retired_at: Optional[UTCTime] = None
    error: Optional[Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]] = None

    @model_validator(mode="after")
    def validate_progress(self):
        targets = [t.key for t in self.expected_targets]
        paths.validate_file_set(targets)
        _unique([p.target.key for p in self.progress], "verified target keys")
        if any(p.target.key not in targets for p in self.progress):
            raise ValueError("Progress must refer to a fixed expected target")
        sources = [self.source, *self.previous_targets]
        _unique([s.object.key for s in sources], "fixed manifest keys")
        files = {
            f"{source.object.key.rsplit('/', 1)[0]}/{f.path}": f
            for source in sources for f in source.manifest.committed_files()
        }
        for progress in self.progress:
            if any(progress.source == source.object for source in sources):
                continue
            source_file = files.get(progress.source.key)
            if source_file is None or (progress.source.generation, progress.size, progress.crc32c) != (
                source_file.generation, source_file.size, source_file.crc32c
            ):
                raise ValueError("Verified progress must match the fixed source version and content")
        if (self.kind == "restore") != (self.target is not None):
            raise ValueError("Only restore operations require a fixed restore target")
        return self


class Manifest(Contract):
    schema_version: Literal[1] = 1
    pipeline_version: Identifier
    revision: Counter
    updated_at: UTCTime
    steps: Dict[Text, StepRecord] = Field(default_factory=dict)
    pending_operation: Optional[PendingOperation] = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def known_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported manifest schema_version")
        return value

    @model_validator(mode="after")
    def unique_ownership(self):
        paths.validate_file_set(o.path for s in self.steps.values() for o in s.outputs)
        return self

    def committed_files(self) -> List[ArtifactObject]:
        """Recorded members, including stale history; never pending content.

        Eligibility and recursive input validity are checked by managed IO, not
        by this enumeration of historical manifest membership.
        """
        return [o for s in self.steps.values() for o in s.outputs]

    @classmethod
    def from_json(cls, data: Union[str, bytes]):
        try:
            return cls.model_validate_json(data)
        except ValidationError as exc:
            raise IntegrityError("Invalid or unsupported asset manifest", cause=exc) from exc

    def to_json(self) -> str:
        return self.model_dump_json()


class WorkspaceManifest(Manifest):
    manifest_type: Literal["workspace"] = "workspace"
    last_run_id: Optional[Identifier] = None
    last_run_started_at: Optional[UTCTime] = None
    # A restored workspace clears last_run_* but keeps the replay watermark.
    latest_run_id: Optional[Identifier] = None
    latest_run_started_at: Optional[UTCTime] = None
    execution: Optional[Execution] = None
    pending_step: Optional[PendingStep] = None
    restored_from: Optional[RestoredFrom] = None

    @model_validator(mode="after")
    def validate_run_context(self):
        for run, started in [(self.last_run_id, self.last_run_started_at), (self.latest_run_id, self.latest_run_started_at)]:
            if (run is None) != (started is None):
                raise ValueError("Run ID and start time must be recorded together")
        if self.pending_step is not None:
            step, execution = self.pending_step, self.execution
            if execution is None or execution.status != "running" or (
                step.run_id, step.step_name, step.step_attempt_token
            ) != (execution.run_id, execution.current_step, execution.step_attempt_token):
                raise ValueError("Pending step must match the active execution attempt")
            owners = {o.path: name for name, s in self.steps.items() for o in s.outputs}
            if any(owners.get(o.path, step.step_name) != step.step_name for o in step.outputs):
                raise ValueError("Pending step cannot claim another step's output")
            paths.validate_file_set([
                *[path for path, owner in owners.items() if owner != step.step_name],
                *[o.path for o in step.outputs],
            ])
        if self.restored_from is not None and self.restored_from.run_id is None:
            raise ValueError("Workspace restoration requires a source snapshot run")
        return self


class SnapshotManifest(Manifest):
    manifest_type: Literal["snapshot"] = "snapshot"
    run_id: Identifier
    run_started_at: UTCTime
    registered_at: UTCTime
    included_steps: List[Text] = Field(default_factory=list)
    sealed: bool = Field(strict=True)
    source_revision: Counter
    location: Literal["assets", "trash"]
    retired_at: Optional[UTCTime] = None
    committed: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def validate_members(self):
        _unique(self.included_steps, "included steps")
        if set(self.included_steps) != set(self.steps):
            raise ValueError("Included steps must match committed snapshot members")
        if any(s.status != "succeeded" or not s.outputs for s in self.steps.values()):
            raise ValueError("Snapshot members must be successful artifact steps")
        if not self.committed and (self.steps or self.included_steps):
            raise ValueError("Preparing snapshot cannot expose committed members")
        if self.committed and not self.steps:
            raise ValueError("Committed snapshot requires artifact members")
        if not self.committed and self.pending_operation is None:
            raise ValueError("Preparing snapshot requires a pending operation")
        if self.location != "trash" and self.retired_at is not None:
            raise ValueError("Only trash snapshots have retired_at")
        return self


class ReleaseFile(ProductionIdentity):
    path: ReleasePath
    size: Counter
    crc32c: Text
    generation: Generation
    source_path: ArtifactPath
    source_object: ObjectVersion
    step_name: Text
    inputs: List[Input] = Field(default_factory=list)


class ReleaseManifest(Manifest):
    manifest_type: Literal["release"] = "release"
    source_pipeline_version: Identifier
    published_at: Optional[UTCTime] = None
    files: List[ReleaseFile] = Field(default_factory=list)
    restored_from: Optional[RestoredFrom] = None
    committed: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def validate_release(self):
        if self.pipeline_version != self.source_pipeline_version:
            raise ValueError("Release pipeline must match source_pipeline_version")
        paths.validate_file_set(f.path for f in self.files)
        if self.committed and (not self.files or self.published_at is None):
            raise ValueError("Committed release requires files and published_at")
        if not self.committed and (self.files or self.steps or self.published_at is not None):
            raise ValueError("Preparing release cannot expose committed content")
        if not self.committed and self.pending_operation is None:
            raise ValueError("Preparing release requires a pending operation")
        return self

    def committed_files(self) -> List[ReleaseFile]:
        return list(self.files)


ManifestData = Annotated[Union[WorkspaceManifest, SnapshotManifest, ReleaseManifest], Field(discriminator="manifest_type")]


class FixedManifest(Contract):
    """Durable source content plus its exact storage version for recovery."""

    object: ObjectVersion
    manifest: ManifestData

    @model_validator(mode="after")
    def validate_source(self):
        if not self.object.key.endswith("/manifest.json"):
            raise ValueError("Fixed source must reference a manifest object")
        if self.manifest.pending_operation is not None:
            raise ValueError("Fixed source cannot contain another pending operation")
        if isinstance(self.manifest, WorkspaceManifest) and self.manifest.pending_step is not None:
            if self.manifest.pending_step.writes_started:
                raise ValueError("Fixed source cannot contain pending artifact writes")
        if not getattr(self.manifest, "committed", True):
            raise ValueError("Fixed source must be committed")
        return self


# Resolve the typed, recursive fixed-source payload once all manifests exist.
for _model in (PendingOperation, Manifest, WorkspaceManifest, SnapshotManifest, ReleaseManifest, FixedManifest):
    _model.model_rebuild()


class SnapshotInfo(Contract):
    pipeline_version: Identifier
    run_id: Identifier
    location: Literal["assets", "trash"]
    uri: Text
    registered_at: UTCTime
    included_steps: List[Text]
    sealed: bool = Field(strict=True)
    committed: bool = Field(strict=True)
    integrity_status: Literal["unverified", "complete", "incomplete"]
    operation_status: OperationStatus
    operation_id: Optional[Text] = None
    sources: Dict[Text, ProductionIdentity] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_members(self):
        _unique(self.included_steps, "included steps")
        if set(self.sources) != set(self.included_steps):
            raise ValueError("Sources must describe the committed included steps")
        if not self.committed and (self.included_steps or self.sources):
            raise ValueError("Preparing snapshot info cannot expose committed members")
        return self


class RetirementItem(Contract):
    run_id: Identifier
    uri: Text
    reason: Text
    operation_id: Optional[Text] = None


class RetirementResult(Contract):
    keep_latest: PositiveInt
    dry_run: bool = Field(strict=True)
    retained: List[RetirementItem] = Field(default_factory=list)
    planned: List[RetirementItem] = Field(default_factory=list)
    retired: List[RetirementItem] = Field(default_factory=list)
    incomplete: List[RetirementItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dry_run(self):
        if self.dry_run and self.retired:
            raise ValueError("Dry-run results cannot report completed retirements")
        return self


class RestoreResult(Contract):
    pipeline_version: Identifier
    run_id: Identifier
    target: Literal["running", "assets"]
    target_uri: Text
    operation_status: OperationStatus
    operation_id: Text


class ReleaseInfo(Contract):
    release_uri: Text
    backup_uri: Optional[Text] = None
    source_pipeline_version: Identifier
    files: List[ReleaseFile]
    operation_status: OperationStatus
    operation_id: Text

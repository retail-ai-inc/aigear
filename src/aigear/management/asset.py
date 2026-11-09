from pathlib import Path
from typing import Literal

from aigear.common.asset_context import RunOwnership, active_context
from aigear.db.bucket import LocalGCSMock, RealGCS
from aigear.db.storage_common import atomic_file, crc32c
from aigear.management.asset_errors import ExecutionConflict, IntegrityError, InvalidArtifact
from aigear.management.asset_paths import artifact_path, identifier, local_asset_roots, resolve_under, workspace_prefix
from aigear.management.artifact_validity import valid_step
from aigear.management.manifest import ManagedInput, ObjectVersion
from aigear.management.managed_io import finish_write, prepare_write


class AssetManagement:
    def __init__(self, pipeline_version: str, data_type: Literal["dataset", "feature", "training"],
                 project_id=None, bucket_name=None, bucket_on=True):
        self.pipeline_version = identifier(pipeline_version)
        self.project_dir = Path.cwd()
        self.data_type = data_type
        artifact_path(f"{data_type}/file")
        self.local_asset_path = self.project_dir / "asset"
        if bucket_on:
            self.bucket_client = RealGCS(project_id, bucket_name)
        else:
            if not bucket_name:
                raise ValueError("bucket_name is required when bucket_on=False (local mode).")
            self.bucket_client = LocalGCSMock(project_id, local_asset_roots(self.project_dir, bucket_name, pipeline_version).bucket)
        self._cache_name = bucket_name

    def _context(self, required=True):
        context = active_context(required=required)
        if context is not None:
            left, right = context.ownership.bucket, self.bucket_client
            if (context.ownership.pipeline_version != self.pipeline_version or type(left) is not type(right)
                    or str(left.bucket_name) != str(right.bucket_name)):
                raise ExecutionConflict("Asset client belongs to another workspace", phase="context")
        return context

    def _source(self, file_name):
        context = self._context(required=False)
        ownership = context.ownership if context else RunOwnership(self.bucket_client, self.pipeline_version)
        manifest, _ = ownership.read()
        if manifest.pending_operation:
            raise ExecutionConflict("Workspace operation prevents reading", phase="download")
        path = artifact_path(f"{self.data_type}/{file_name}")
        found = [(name, step) for name, step in manifest.steps.items() if any(o.path == path for o in step.outputs)]
        if len(found) != 1:
            raise InvalidArtifact("File has no committed producer", phase="download")
        name, step = found[0]
        if context and name == context.step:
            raise InvalidArtifact("A step cannot consume its own production", phase="download")
        valid_step(manifest, name, self.bucket_client, workspace_prefix(self.pipeline_version))
        output = next(o for o in step.outputs if o.path == path)
        source = ManagedInput(run_id=step.producer_run_id, step_name=name, step_revision=step.producer_step_revision,
                              artifact=path, observed_object=ObjectVersion(key=self.get_bucket_blob(file_name), generation=output.generation))
        return output, source, context

    @staticmethod
    def _record(context, source):
        if context:
            manifest, generation = context.current()
            if source not in manifest.pending_step.inputs:
                manifest.pending_step.inputs.append(source)
                context.ownership._write(manifest, generation, "input")

    def download(self, file_name: str) -> Path:
        output, source, context = self._source(file_name)
        roots = local_asset_roots(self.project_dir, self._cache_name, self.pipeline_version)
        target = resolve_under(roots.cache, f"{output.generation}/{output.path}")
        cached = target.read_bytes() if target.is_file() else None
        if cached is None or len(cached) != output.size or crc32c(cached) != output.crc32c:
            data = self.bucket_client.read_bytes(source.observed_object.key, generation=output.generation)
            if len(data) != output.size or crc32c(data) != output.crc32c:
                raise IntegrityError("Downloaded artifact checksum differs", phase="download")
            atomic_file(target, data)
        if self.bucket_client.get_metadata(source.observed_object.key).generation != output.generation:
            raise InvalidArtifact("Artifact changed while reading", phase="download")
        self._record(context, source)
        return target

    def upload(self, file_name: str) -> None:
        context = self._context()
        data = self.get_local_path(file_name).read_bytes()
        intent = prepare_write(context, artifact_path(f"{self.data_type}/{file_name}"))
        meta = self.bucket_client.write_bytes(self.get_bucket_blob(file_name), data,
            if_generation_match=intent.expected_generation, metadata={"aigear_write_id": intent.write_id})
        finish_write(context, intent, meta)

    def copy_blob(self, source_file_name: str, destination_file_name: str) -> None:
        context = self._context()
        output, source, _ = self._source(source_file_name)
        data = self.bucket_client.read_bytes(source.observed_object.key, generation=output.generation)
        if len(data) != output.size or crc32c(data) != output.crc32c:
            raise IntegrityError("Copy source checksum differs", phase="copy")
        self._record(context, source)
        intent = prepare_write(context, artifact_path(f"{self.data_type}/{destination_file_name}"))
        meta = self.bucket_client.write_bytes(self.get_bucket_blob(destination_file_name), data,
            if_generation_match=intent.expected_generation, metadata={"aigear_write_id": intent.write_id})
        finish_write(context, intent, meta)

    def get_local_path(self, local_file_name: str) -> Path:
        path = artifact_path(f"{self.data_type}/{local_file_name}")
        target = resolve_under(self.local_asset_path / self.pipeline_version, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def get_bucket_blob(self, bucket_file_name: str) -> str:
        return f"{workspace_prefix(self.pipeline_version)}/{artifact_path(f'{self.data_type}/{bucket_file_name}')}"

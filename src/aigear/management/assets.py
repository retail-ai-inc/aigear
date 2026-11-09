"""Snapshot queries, registration and sealing over versioned bucket operations."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aigear.common.asset_context import RunOwnership, active_context
from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.db.bucket import LocalGCSMock, RealGCS
from aigear.management.asset_errors import (AssetError, ExecutionConflict, InvalidArtifact, OperationIncomplete,
                                          SnapshotConflict, SnapshotNotFound)
from aigear.management.asset_paths import identifier, local_asset_roots, manifest_key, snapshot_prefix, workspace_prefix
from aigear.management.artifact_validity import valid_step
from aigear.management.asset_operations import copy_members, verify_object
from aigear.management.manifest import (ArtifactObject, FixedManifest, ObjectExpectation, ObjectVersion,
    PendingOperation, ProductionIdentity, SnapshotInfo, SnapshotManifest)
from aigear.management.managed_io import record_input


def _step_identity(step):
    value = step.model_dump(mode='json')
    value['outputs'] = sorted([{k: v for k, v in output.items() if k != 'generation'}
                               for output in value['outputs']], key=lambda output: output['path'])
    return value


class Assets:
    def __init__(self, pipeline_version, *, project_id=None, bucket_name=None, bucket_on=None):
        self.pipeline_version = identifier(pipeline_version)
        self.project_dir = Path.cwd()
        self._client = None
        self._settings = None
        if bucket_name is None:
            from aigear.common.config import AppConfig
            config = AppConfig.aigear()
            self._settings = config.gcp.bucket
            bucket_name = self._settings.train_bucket.bucket_name
            if project_id is None:
                project_id = config.gcp.gcp_project_id
            if bucket_on is None:
                bucket_on = self._settings.on
        self.project_id, self.bucket_name = project_id, bucket_name
        self.bucket_on = True if bucket_on is None else bucket_on

    @classmethod
    def _from_bucket(cls, pipeline_version, bucket):
        result = cls.__new__(cls)
        result.pipeline_version, result._client = pipeline_version, bucket
        result.project_dir = Path.cwd()
        result.project_id, result.bucket_name = bucket.project_id, bucket.bucket_name
        result._settings = None
        return result

    @property
    def _bucket(self):
        if self._client is None:
            self._client = (RealGCS(self.project_id, self.bucket_name) if self.bucket_on else
                LocalGCSMock(self.project_id, local_asset_roots(self.project_dir, self.bucket_name, self.pipeline_version).bucket))
        return self._client

    def _ownership(self):
        context = active_context(required=False)
        if context:
            bucket = context.ownership.bucket
            if (context.ownership.pipeline_version != self.pipeline_version or type(bucket) is not type(self._bucket)
                    or str(bucket.bucket_name) != str(self._bucket.bucket_name)):
                raise ExecutionConflict('Active context belongs to another workspace', phase='operation')
            return context.ownership
        return RunOwnership(self._bucket, self.pipeline_version)

    def _snapshots(self, locations=('assets',)):
        found = []
        for location in locations:
            for meta in self._bucket.list_metadata(f'{location}/{self.pipeline_version}/'):
                if not meta.name.endswith('/manifest.json'):
                    continue
                snapshot = SnapshotManifest.from_json(self._bucket.read_bytes(meta.name, generation=meta.generation))
                expected = manifest_key(snapshot_prefix(snapshot.location, snapshot.pipeline_version,
                                                        snapshot.run_id, snapshot.run_started_at))
                if snapshot.pipeline_version != self.pipeline_version or meta.name != expected:
                    raise SnapshotConflict('Snapshot identity differs from its location', phase='query')
                found.append((meta, snapshot))
        return found

    def _info(self, snapshot, *, operation=None):
        operation = operation or snapshot.pending_operation
        prefix = snapshot_prefix(snapshot.location, snapshot.pipeline_version, snapshot.run_id, snapshot.run_started_at)
        phase = operation.phase if operation else 'idle'
        return SnapshotInfo(pipeline_version=self.pipeline_version, run_id=snapshot.run_id, location=snapshot.location,
            uri=f'gs://{self.bucket_name}/{prefix}', registered_at=snapshot.registered_at,
            included_steps=snapshot.included_steps, sealed=snapshot.sealed, committed=snapshot.committed,
            integrity_status='unverified', operation_status=phase if phase in {'idle', 'preparing', 'copying', 'moving', 'cleaning', 'incomplete', 'completed'} else 'incomplete',
            operation_id=operation.operation_id if operation else None,
            sources={name: ProductionIdentity(producer_run_id=step.producer_run_id,
                     producer_step_revision=step.producer_step_revision) for name, step in snapshot.steps.items()})

    def list(self, *, include_trash=False):
        values = self._snapshots(('assets', 'trash') if include_trash else ('assets',))
        runs = {}
        for meta, snapshot in values:
            if snapshot.run_id in runs:
                raise SnapshotConflict('Multiple snapshot locations require reconciliation', phase='query')
            runs[snapshot.run_id] = snapshot
        return [self._info(snapshot) for snapshot in sorted(runs.values(), key=lambda s: (-s.registered_at.timestamp(), s.run_id))]

    def get(self, run_id):
        identifier(run_id)
        matches = [value for value in self.list(include_trash=True) if value.run_id == run_id]
        if not matches:
            raise SnapshotNotFound('Snapshot run was not found', phase='query')
        return matches[0]

    @staticmethod
    def record_input(name, *, uri=None, version=None):
        return record_input(name, uri=uri, version=version)

    def _persist_operation(self, operation):
        ownership = RunOwnership(self._bucket, self.pipeline_version)
        current, generation = ownership.read()
        if current.pending_operation is None or current.pending_operation.operation_id != operation.operation_id:
            raise ExecutionConflict('Workspace operation changed', operation_id=operation.operation_id, phase='progress')
        current.pending_operation = operation.model_copy(deep=True)
        ownership._write(current, generation, 'operation-progress')

    def _clear_operation(self, operation):
        ownership = RunOwnership(self._bucket, self.pipeline_version)
        current, generation = ownership.read()
        if current.pending_operation is None:
            return
        if current.pending_operation.operation_id != operation.operation_id:
            raise ExecutionConflict('Workspace operation changed', phase='complete')
        current.pending_operation = None
        ownership._write(current, generation, 'operation-complete')

    def register(self):
        ownership = self._ownership()
        manifest, generation = ownership.read()
        context = active_context(required=False)
        if manifest.pending_operation:
            if manifest.pending_operation.kind != 'register':
                raise OperationIncomplete('Another workspace operation is pending', phase='register')
            if context is None and manifest.execution.status in {'running', 'waiting'}:
                raise ExecutionConflict('An active run owns pending registration', phase='register')
            return self._resume_register(manifest.pending_operation)
        if context is None and manifest.execution and manifest.execution.status in {'running', 'waiting', 'finalizing'}:
            raise ExecutionConflict('Registration requires the active workflow context', phase='register')
        if manifest.pending_step and (manifest.pending_step.writes_started or manifest.pending_step.outputs):
            raise InvalidArtifact('Pending outputs cannot be registered', phase='register')
        run_id = manifest.execution.run_id if context else manifest.last_run_id
        if run_id is None or manifest.execution is None:
            raise SnapshotNotFound('No completed or active run to register', phase='register')
        selected = {name: step for name, step in manifest.steps.items() if step.outputs}
        prefix = snapshot_prefix('assets', self.pipeline_version, run_id, manifest.execution.run_started_at)
        key = manifest_key(prefix)
        try:
            meta = self._bucket.get_metadata(key)
            previous = SnapshotManifest.from_json(self._bucket.read_bytes(key, generation=meta.generation))
        except ObjectNotFound:
            meta, previous = None, None
        if not selected and previous is None:
            raise InvalidArtifact('Cannot register an empty workspace', phase='register')
        for name in selected:
            try:
                valid_step(manifest, name, self._bucket, workspace_prefix(self.pipeline_version))
            except InvalidArtifact as exc:
                if previous and name in previous.steps:
                    raise SnapshotConflict('Existing snapshot member is no longer valid', phase='register', cause=exc) from exc
                raise
        if previous:
            if previous.pending_operation or not previous.committed:
                raise OperationIncomplete('Snapshot has an unfinished operation', phase='register')
            for name, step in previous.steps.items():
                if name not in selected or _step_identity(selected[name]) != _step_identity(step):
                    raise SnapshotConflict('Existing snapshot member changed or disappeared', phase='register')
                try:
                    valid_step(previous, name, self._bucket, prefix)
                except InvalidArtifact as exc:
                    raise SnapshotConflict('Existing snapshot content is incomplete', phase='register', cause=exc) from exc
            if set(previous.steps) == set(selected):
                if context is None and not previous.sealed:
                    self._seal_run(manifest)
                    return self.get(run_id)
                return self._info(previous)
            if previous.sealed:
                raise SnapshotConflict('Sealed snapshots cannot be appended', phase='register')
        added = [output for name, step in selected.items() if previous is None or name not in previous.steps for output in step.outputs]
        operation = PendingOperation(operation_id=uuid4().hex, kind='register', phase='preparing',
            started_at=datetime.now(timezone.utc), source=FixedManifest(object=ObjectVersion(key=ownership.key, generation=generation), manifest=manifest.model_copy(deep=True)),
            previous_targets=[FixedManifest(object=ObjectVersion(key=key, generation=meta.generation), manifest=previous)] if previous else [],
            expected_targets=[ObjectExpectation(key=f'{prefix}/{output.path}', generation=None) for output in added]
                             + [ObjectExpectation(key=key, generation=meta.generation if meta else None)])
        manifest.pending_operation = operation
        ownership._write(manifest, generation, 'register-preparing')
        return self._resume_register(operation)

    def _resume_register(self, operation, *, sealing=False):
        try:
            return self._continue_register(operation, sealing=sealing)
        except AssetError as exc:
            exc.operation_id = exc.operation_id or operation.operation_id
            exc.partial_result = exc.partial_result or {'run_id': operation.source.manifest.execution.run_id,
                'operation_id': operation.operation_id, 'phase': operation.phase}
            raise
        except Exception as exc:
            raise OperationIncomplete('Snapshot registration is incomplete', operation_id=operation.operation_id,
                phase=operation.phase, cause=exc, partial_result={'run_id': operation.source.manifest.execution.run_id}) from exc

    def _validate_register_source(self, operation):
        source = operation.source.manifest
        try:
            for name, step in source.steps.items():
                if step.outputs:
                    valid_step(source, name, self._bucket, workspace_prefix(self.pipeline_version))
        except InvalidArtifact as exc:
            self._abort_registration(operation)
            raise SnapshotConflict('Fixed registration source changed; owned candidates were removed',
                operation_id=operation.operation_id, phase='aborted', cause=exc) from exc

    def _abort_registration(self, operation):
        if operation.phase != 'cleaning':
            operation.phase = 'cleaning'
            self._persist_operation(operation)
        key = operation.expected_targets[-1].key
        try:
            meta = self._bucket.get_metadata(key)
        except ObjectNotFound:
            if operation.previous_targets:
                raise SnapshotConflict('Previous snapshot disappeared during cleanup', phase='abort')
            self._clear_operation(operation)
            return
        target = SnapshotManifest.from_json(self._bucket.read_bytes(key, generation=meta.generation))
        if target.pending_operation is None and operation.previous_targets:
            previous = operation.previous_targets[0].manifest
            if (target.run_id == previous.run_id and target.registered_at == previous.registered_at
                    and set(target.steps) == set(previous.steps)
                    and all(_step_identity(target.steps[name]) == _step_identity(step) for name, step in previous.steps.items())):
                self._clear_operation(operation)
                return
        if target.pending_operation is None or target.pending_operation.operation_id != operation.operation_id:
            raise SnapshotConflict('Cannot clean a changed snapshot manifest', phase='abort')
        progress = {item.target.key: item for item in operation.progress}
        for expected in operation.expected_targets[:-1]:
            try:
                member = self._bucket.get_metadata(expected.key)
            except ObjectNotFound:
                continue
            if member.metadata.get('aigear_operation') != operation.operation_id or (
                expected.key in progress and member.generation != progress[expected.key].target.generation):
                raise SnapshotConflict('Cannot clean an externally changed candidate', phase='abort')
            self._bucket.delete(expected.key, if_generation_match=member.generation)
        if operation.previous_targets:
            previous = operation.previous_targets[0].manifest.model_copy(deep=True)
            previous.revision = target.revision + 1
            previous.updated_at = datetime.now(timezone.utc)
            self._bucket.write_json(key, previous.model_dump(mode='json'), if_generation_match=meta.generation)
        else:
            self._bucket.delete(key, if_generation_match=meta.generation)
        self._clear_operation(operation)

    def _continue_register(self, operation, *, sealing=False):
        if operation.phase == 'cleaning':
            self._abort_registration(operation)
            raise SnapshotConflict('Registration was aborted after a source conflict', phase='aborted')
        source = operation.source.manifest
        execution = source.execution
        prefix = snapshot_prefix('assets', self.pipeline_version, execution.run_id, execution.run_started_at)
        key = manifest_key(prefix)
        previous = operation.previous_targets[0].manifest if operation.previous_targets else None
        selected = {name: step for name, step in source.steps.items() if step.outputs}
        try:
            target_meta = self._bucket.get_metadata(key)
            target = SnapshotManifest.from_json(self._bucket.read_bytes(key, generation=target_meta.generation))
        except ObjectNotFound:
            target_meta, target = None, None
        if target and target.pending_operation is None and target.committed and target.source_revision == source.revision:
            if set(target.steps) != set(selected) or any(_step_identity(target.steps[name]) != _step_identity(step) for name, step in selected.items()):
                raise SnapshotConflict('Committed retry differs from fixed registration', phase='register')
            for name in target.steps:
                valid_step(target, name, self._bucket, prefix)
            self._clear_operation(operation)
            return self._info(target)
        if target and target.pending_operation and target.pending_operation.operation_id == operation.operation_id:
            self._validate_register_source(operation)
        if target is None or target.pending_operation is None:
            expected = operation.expected_targets[-1].generation or '0'
            if (target_meta.generation if target_meta else '0') != expected:
                raise SnapshotConflict('Snapshot manifest changed', operation_id=operation.operation_id, phase='prepare')
            target = SnapshotManifest(pipeline_version=self.pipeline_version, revision=(previous.revision + 1 if previous else 0),
                updated_at=datetime.now(timezone.utc), run_id=execution.run_id, run_started_at=execution.run_started_at,
                registered_at=previous.registered_at if previous else operation.started_at,
                included_steps=previous.included_steps if previous else [], steps=previous.steps if previous else {},
                sealed=False, source_revision=source.revision, location='assets', committed=previous is not None, pending_operation=operation)
            target_meta = self._bucket.write_json(key, target.model_dump(mode='json'), if_generation_match=expected)
        elif target.pending_operation.operation_id != operation.operation_id:
            raise SnapshotConflict('Snapshot is owned by another operation', phase='register')
        self._validate_register_source(operation)
        operation.phase = 'copying'
        self._persist_operation(operation)
        added = [output for name, step in selected.items() if previous is None or name not in previous.steps for output in step.outputs]
        try:
            copies = copy_members(self._bucket, self._bucket, operation, added, workspace_prefix(self.pipeline_version), prefix, self._persist_operation)
        except Exception:
            self._validate_register_source(operation)
            raise
        self._validate_register_source(operation)
        steps = {name: step.model_copy(deep=True) for name, step in selected.items()}
        for name, step in steps.items():
            if previous and name in previous.steps:
                step.outputs = previous.steps[name].outputs
            else:
                step.outputs = [ArtifactObject(path=o.path, generation=copies[o.path].generation, size=o.size, crc32c=o.crc32c) for o in step.outputs]
        current, _ = RunOwnership(self._bucket, self.pipeline_version).read()
        target.steps, target.included_steps = steps, list(steps)
        target.committed, target.pending_operation = True, None
        target.sealed = sealing or current.execution.status in {'finalizing', 'succeeded', 'failed'}
        target.revision += 1
        target.updated_at = datetime.now(timezone.utc)
        for member in target.committed_files():
            verify_object(self._bucket, f'{prefix}/{member.path}', member)
        target = SnapshotManifest.model_validate(target.model_dump())
        self._bucket.write_json(key, target.model_dump(mode='json'), if_generation_match=target_meta.generation)
        self._clear_operation(operation)
        return self._info(target)

    def _seal_run(self, manifest):
        current, _ = RunOwnership(self._bucket, self.pipeline_version).read()
        if current.pending_operation:
            if current.pending_operation.kind != 'register':
                raise OperationIncomplete('Workspace operation prevents sealing', phase='seal')
            self._resume_register(current.pending_operation, sealing=True)
        for meta, snapshot in self._snapshots():
            if snapshot.run_id != manifest.execution.run_id:
                continue
            if snapshot.pending_operation or not snapshot.committed:
                raise OperationIncomplete('Snapshot registration is incomplete', phase='seal')
            prefix = meta.name.rsplit('/', 1)[0]
            for name in snapshot.steps:
                valid_step(snapshot, name, self._bucket, prefix)
            if not snapshot.sealed:
                snapshot.sealed = True
                snapshot.revision += 1
                snapshot.updated_at = datetime.now(timezone.utc)
                self._bucket.write_json(meta.name, snapshot.model_dump(mode='json'), if_generation_match=meta.generation)

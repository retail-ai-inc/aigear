"""Snapshot queries, registration and sealing over versioned bucket operations."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import logging
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
    PendingOperation, ProductionIdentity, RetirementItem, RetirementResult, RestoreResult, SnapshotInfo, SnapshotManifest)
from aigear.management.managed_io import record_input

logger = logging.getLogger(__name__)


def _step_identity(step):
    value = step.model_dump(mode='json')
    value['outputs'] = sorted([{k: v for k, v in output.items() if k != 'generation'}
                               for output in value['outputs']], key=lambda output: output['path'])
    return value


class Assets:
    def __init__(self, pipeline_version, *, project_id=None, bucket_name=None, bucket_on=None, release_bucket_name=None):
        self.pipeline_version = identifier(pipeline_version)
        self.project_dir = Path.cwd()
        self._client = None
        self._settings = None
        self._release_client = None
        self._release_name = release_bucket_name
        if bucket_name is None:
            from aigear.common.config import AppConfig
            config = AppConfig.aigear()
            self._settings = config.gcp.bucket
            if self._release_name is None:
                self._release_name = self._settings.release_bucket.bucket_name
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
        result._release_client = None
        result._release_name = None
        return result

    @property
    def _release_bucket(self):
        if self._release_client is None:
            if self._release_name is None:
                raise ValueError('release_bucket_name is required with an explicit bucket binding')
            self._release_client = (RealGCS(self.project_id, self._release_name) if self.bucket_on else
                LocalGCSMock(self.project_id, local_asset_roots(self.project_dir, self._release_name, self.pipeline_version).bucket))
        return self._release_client

    def publish(self, *, path=None, extra_files=None):
        from aigear.management.release import ReleaseManager
        return self._with_terminal_recovery(lambda: ReleaseManager(self).publish(path=path, extra_files=extra_files))

    def rollback(self, *, path=None):
        from aigear.management.release import ReleaseManager
        return self._with_terminal_recovery(lambda: ReleaseManager(self).rollback(path=path))

    def _with_terminal_recovery(self, action):
        try:
            result = action()
        except ValueError:
            raise
        except Exception:
            try:
                self._finish_finalizing()
            except Exception:
                logger.warning('asset_terminal_recovery_incomplete', extra={'pipeline_version': self.pipeline_version})
            raise
        self._finish_finalizing()
        return result

    def _finish_finalizing(self):
        if self._client is None or active_context(required=False) is not None:
            return
        ownership = RunOwnership(self._client, self.pipeline_version)
        workspace, _ = ownership.read()
        execution = workspace.execution
        if (execution and execution.status == 'finalizing' and workspace.pending_operation is None
                and workspace.pending_step is None):
            if execution.terminal_status not in {'succeeded', 'failed'}:
                raise OperationIncomplete('Run finalization has no fixed outcome', phase='finalize')
            ownership.finalize(execution.run_id, execution.owner_token, execution.current_step, execution.step_attempt_token,
                               succeeded=execution.terminal_status == 'succeeded', seal=self._seal_run)

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
        if self._local_store_absent():
            return []
        found = []
        for location in locations:
            if location == 'trash' and not self._trash_enabled():
                continue
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

    def _local_store_absent(self):
        return (self._client is None and not self.bucket_on
                and not local_asset_roots(self.project_dir, self.bucket_name, self.pipeline_version).bucket.exists())

    def _trash_enabled(self):
        return self._settings is None or self._settings.train_bucket.trash_retention_days is not None

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
        values = self._logical_snapshots()
        return [self._info(snapshot, operation=operation) for _, snapshot, operation in
                sorted(values, key=lambda value: (-value[1].registered_at.timestamp(), value[1].run_id))
                if include_trash or snapshot.location == 'assets']

    def _logical_snapshots(self):
        from aigear.management.snapshot_transfer import _same_members
        values = self._snapshots(('assets', 'trash'))
        runs = {}
        for meta, snapshot in values:
            runs.setdefault(snapshot.run_id, []).append((meta, snapshot))
        result = []
        for copies in runs.values():
            if len(copies) == 1:
                meta, snapshot = copies[0]
                result.append((meta, snapshot, snapshot.pending_operation))
                continue
            operations = [snapshot.pending_operation for _, snapshot in copies if snapshot.pending_operation]
            if (len(copies) != 2 or not operations or len({op.operation_id for op in operations}) != 1
                    or any(op.kind not in {'retire', 'restore'} for op in operations)):
                raise SnapshotConflict('Conflicting snapshot copies', phase='query')
            operation = operations[0]
            keys = {meta.name for meta, _ in copies}
            if keys != {operation.source.object.key, operation.expected_targets[-1].key}:
                raise SnapshotConflict('Snapshot locations differ from fixed migration', phase='query')
            for _, snapshot in copies:
                if snapshot.committed and not _same_members(snapshot, operation.source.manifest):
                    raise SnapshotConflict('Snapshot migration copies disagree', phase='query')
            target = next((value for value in copies if value[0].name == operation.expected_targets[-1].key and value[1].committed), None)
            meta, snapshot = target or next(value for value in copies if value[0].name == operation.source.object.key)
            result.append((meta, snapshot, operation))
        if result:
            workspace, _ = RunOwnership(self._bucket, self.pipeline_version).read()
            current = workspace.pending_operation
            if current:
                source = current.source.manifest
                run_id = source.run_id if isinstance(source, SnapshotManifest) else source.execution.run_id
                result = [(meta, snapshot, current if snapshot.run_id == run_id else operation)
                          for meta, snapshot, operation in result]
        return result

    def _begin_transfer(self, meta, snapshot, *, kind='retire', target=None):
        from aigear.management.artifact_validity import valid_step
        ownership = self._ownership()
        workspace, generation = ownership.read()
        if workspace.pending_operation:
            raise OperationIncomplete('Another workspace operation is pending', phase='transfer')
        if active_context(required=False) is None and workspace.execution and workspace.execution.status in {'running', 'waiting', 'finalizing'}:
            raise ExecutionConflict('Workspace is owned by an active run', phase='transfer')
        if not snapshot.sealed or not snapshot.committed or snapshot.pending_operation:
            raise InvalidArtifact('Only complete sealed snapshots can be transferred', phase='transfer')
        source_prefix = meta.name.rsplit('/', 1)[0]
        for name in snapshot.steps:
            valid_step(snapshot, name, self._bucket, source_prefix)
        if self._bucket.get_metadata(meta.name).generation != meta.generation:
            raise SnapshotConflict('Snapshot changed before transfer', phase='transfer')
        location = 'trash' if kind == 'retire' else 'assets'
        prefix = snapshot_prefix(location, self.pipeline_version, snapshot.run_id, snapshot.run_started_at)
        previous_target = None
        target_versions = {}
        if kind == 'restore':
            from aigear.management.snapshot_transfer import _same_members
            try:
                target_meta = self._bucket.get_metadata(manifest_key(prefix))
                target_snapshot = SnapshotManifest.from_json(self._bucket.read_bytes(target_meta.name, generation=target_meta.generation))
            except ObjectNotFound:
                target_meta = target_snapshot = None
            if target_snapshot:
                if (target_snapshot.pending_operation or not target_snapshot.committed or not _same_members(target_snapshot, snapshot)
                        or target_meta.custom_time is not None or target_snapshot.retired_at is not None):
                    raise SnapshotConflict('Existing restore destination disagrees with source', phase='restore')
                for name in target_snapshot.steps:
                    try:
                        valid_step(target_snapshot, name, self._bucket, prefix)
                    except InvalidArtifact as exc:
                        raise SnapshotConflict('Existing restore destination is incomplete', phase='restore', cause=exc) from exc
                for member in target_snapshot.committed_files():
                    info = self._bucket.get_metadata(f'{prefix}/{member.path}')
                    if info.custom_time is not None:
                        raise SnapshotConflict('Existing assets destination has trash retention metadata', phase='restore')
                    target_versions[member.path] = member.generation
                previous_target = FixedManifest(object=ObjectVersion(key=target_meta.name, generation=target_meta.generation), manifest=target_snapshot)
        operation = PendingOperation(operation_id=uuid4().hex, kind=kind, phase='preparing', target=target,
            started_at=datetime.now(timezone.utc), source=FixedManifest(object=ObjectVersion(key=meta.name, generation=meta.generation), manifest=snapshot),
            previous_targets=[previous_target] if previous_target else [],
            expected_targets=[ObjectExpectation(key=f'{prefix}/{member.path}', generation=target_versions.get(member.path)) for member in snapshot.committed_files()]
                             + [ObjectExpectation(key=manifest_key(prefix), generation=previous_target.object.generation if previous_target else None)])
        workspace.pending_operation = operation
        ownership._write(workspace, generation, 'transfer-preparing')
        return operation

    def _resume_transfer(self, operation):
        from aigear.management.snapshot_transfer import resume_transfer
        fields = {'operation_id': operation.operation_id, 'phase': operation.phase,
                  'pipeline_version': self.pipeline_version, 'run_id': operation.source.manifest.run_id}
        logger.info('asset_transfer_started', extra=fields)
        try:
            result = resume_transfer(self, operation)
        except Exception:
            logger.warning('asset_transfer_incomplete', extra={**fields, 'phase': operation.phase})
            raise
        logger.info('asset_transfer_completed', extra={**fields, 'phase': 'completed'})
        return result

    def retire(self, keep_latest, *, dry_run=False):
        if dry_run:
            return self._retire(keep_latest, dry_run=dry_run)
        return self._with_terminal_recovery(lambda: self._retire(keep_latest, dry_run=dry_run))

    def _retire(self, keep_latest, *, dry_run=False):
        result = RetirementResult(keep_latest=keep_latest, dry_run=dry_run)
        if not self._trash_enabled():
            raise InvalidArtifact('Trash retention is disabled by the bucket configuration', phase='retire')
        if self._local_store_absent():
            return result
        ownership = self._ownership()
        workspace, _ = ownership.read()
        operation = workspace.pending_operation
        if operation:
            if (not dry_run and active_context(required=False) is None and workspace.execution
                    and workspace.execution.status in {'running', 'waiting'}):
                raise ExecutionConflict('An active workflow owns the pending operation', phase='retire')
            source = operation.source.manifest
            run_id = source.run_id if isinstance(source, SnapshotManifest) else source.execution.run_id
            blocked = RetirementItem(run_id=run_id, uri=f'gs://{self.bucket_name}/{operation.source.object.key}',
                                     reason='pending operation', operation_id=operation.operation_id)
            if dry_run:
                result.incomplete.append(blocked)
            elif operation.kind == 'retire':
                try:
                    completed = self._resume_transfer(operation)
                    result.retired.append(RetirementItem(run_id=completed.run_id, uri=self._info(completed).uri,
                                                        reason='retired', operation_id=operation.operation_id))
                except Exception as exc:
                    result.incomplete.append(blocked)
                    raise OperationIncomplete('Retirement reconciliation is incomplete', operation_id=operation.operation_id,
                        phase=operation.phase, cause=exc, partial_result=result) from exc
            else:
                result.incomplete.append(blocked)
                raise OperationIncomplete('Another operation must finish before retirement', operation_id=operation.operation_id,
                                          phase=operation.phase, partial_result=result)
        workspace, _ = ownership.read()
        protected = workspace.execution.run_id if workspace.execution and workspace.execution.status in {'running', 'waiting', 'finalizing'} else None
        snapshots = sorted([value for value in self._logical_snapshots() if value[1].location == 'assets' and value[1].committed],
                           key=lambda value: (-value[1].registered_at.timestamp(), value[1].run_id))
        planned = []
        for index, (meta, snapshot, pending) in enumerate(snapshots):
            item = RetirementItem(run_id=snapshot.run_id, uri=self._info(snapshot).uri,
                                  reason='active' if snapshot.run_id == protected or not snapshot.sealed else 'recent')
            if index < keep_latest or snapshot.run_id == protected or not snapshot.sealed:
                result.retained.append(item)
            elif pending:
                result.incomplete.append(item.model_copy(update={'reason': 'pending operation', 'operation_id': pending.operation_id}))
            else:
                item.reason = 'retention limit'
                result.planned.append(item)
                planned.append((meta, snapshot, item))
        if dry_run:
            if protected and active_context(required=False) is None:
                result.incomplete.extend(item.model_copy(update={'reason': 'workspace occupied'}) for _, _, item in planned)
            return result
        if result.incomplete:
            raise OperationIncomplete('Pending snapshots prevent retirement', phase='plan', partial_result=result)
        for meta, snapshot, item in planned:
            operation = None
            try:
                operation = self._begin_transfer(meta, snapshot)
                completed = self._resume_transfer(operation)
                result.retired.append(RetirementItem(run_id=completed.run_id, uri=self._info(completed).uri,
                                                    reason='retired', operation_id=operation.operation_id))
            except Exception as exc:
                result.incomplete.append(item.model_copy(update={'reason': 'retirement incomplete',
                    'operation_id': operation.operation_id if operation else None}))
                raise OperationIncomplete('Retirement is incomplete', operation_id=operation.operation_id if operation else None,
                    phase=operation.phase if operation else 'acquire', cause=exc, partial_result=result) from exc
        return result

    def restore(self, run_id, *, target='assets'):
        return self._with_terminal_recovery(lambda: self._restore(run_id, target=target))

    def _restore(self, run_id, *, target='assets'):
        identifier(run_id)
        if target not in {'assets', 'running'}:
            raise ValueError('Restore target must be assets or running')
        if not self._trash_enabled():
            raise InvalidArtifact('Trash retention is disabled by the bucket configuration', phase='restore')
        ownership = self._ownership()
        workspace, generation = ownership.read()
        operation = workspace.pending_operation
        if operation:
            if (active_context(required=False) is None and workspace.execution
                    and workspace.execution.status in {'running', 'waiting'}):
                raise ExecutionConflict('An active workflow owns the pending restore', phase='restore')
            if operation.kind != 'restore' or operation.source.manifest.run_id != run_id or operation.target != target:
                raise SnapshotConflict('Pending restore has a different source or target', phase='restore')
        else:
            if (workspace.execution and workspace.execution.status in {'running', 'waiting', 'finalizing'}
                    and (target == 'running' or active_context(required=False) is None)):
                raise ExecutionConflict('An active or waiting run prevents restoration', phase='restore')
            matches = [(meta, snapshot) for meta, snapshot in self._snapshots(('trash',)) if snapshot.run_id == run_id]
            if not matches:
                if target == 'running' and workspace.restored_from and workspace.restored_from.run_id == run_id:
                    if workspace.last_run_id is not None or workspace.execution is not None:
                        raise SnapshotConflict('A newer run prevents replay of an old restore', phase='restore')
                    for name, step in workspace.steps.items():
                        if step.outputs:
                            valid_step(workspace, name, self._bucket, workspace_prefix(self.pipeline_version))
                    return self._restore_result(run_id, target, workspace.restored_from.operation_id)
                if target == 'assets':
                    if workspace.restored_from and workspace.restored_from.run_id == run_id:
                        raise SnapshotConflict('The completed restore targeted running', phase='restore')
                    existing = [(meta, snapshot) for meta, snapshot in self._snapshots() if snapshot.run_id == run_id]
                    if len(existing) == 1 and existing[0][0].metadata.get('aigear_operation'):
                        meta, snapshot = existing[0]
                        for name in snapshot.steps:
                            valid_step(snapshot, name, self._bucket, meta.name.rsplit('/', 1)[0])
                        return self._restore_result(run_id, target, meta.metadata['aigear_operation'], snapshot)
                raise SnapshotNotFound('Trash snapshot run was not found', phase='restore')
            if len(matches) != 1:
                raise SnapshotConflict('Multiple trash sources disagree', phase='restore')
            meta, snapshot = matches[0]
            if target == 'assets':
                operation = self._begin_transfer(meta, snapshot, kind='restore', target='assets')
            else:
                if not snapshot.committed or not snapshot.sealed or snapshot.pending_operation:
                    raise InvalidArtifact('Restore requires a complete sealed source', phase='restore')
                for name in snapshot.steps:
                    valid_step(snapshot, name, self._bucket, meta.name.rsplit('/', 1)[0])
                previous = {member.path: member for member in workspace.committed_files()}
                expected = {}
                for member in [*workspace.committed_files(), *snapshot.committed_files()]:
                    key = f'{workspace_prefix(self.pipeline_version)}/{member.path}'
                    try:
                        actual = self._bucket.get_metadata(key)
                    except ObjectNotFound:
                        expected[key] = None
                        continue
                    if member.path not in previous or actual.generation != previous[member.path].generation:
                        raise SnapshotConflict('Running destination contains an unowned version', phase='restore')
                    expected[key] = actual.generation
                operation = PendingOperation(operation_id=uuid4().hex, kind='restore', target='running', phase='preparing',
                    started_at=datetime.now(timezone.utc), source=FixedManifest(object=ObjectVersion(key=meta.name, generation=meta.generation), manifest=snapshot),
                    previous_targets=[FixedManifest(object=ObjectVersion(key=ownership.key, generation=generation), manifest=workspace.model_copy(deep=True))] if generation != '0' else [],
                    expected_targets=[ObjectExpectation(key=key, generation=expected[key])
                        for key in [f'{workspace_prefix(self.pipeline_version)}/{member.path}' for member in snapshot.committed_files()]]
                                     + [ObjectExpectation(key=ownership.key, generation=generation if generation != '0' else None)])
                workspace.pending_operation = operation
                ownership._write(workspace, generation, 'restore-preparing')
        try:
            if target == 'assets':
                snapshot = self._resume_transfer(operation)
                return self._restore_result(run_id, target, operation.operation_id, snapshot)
            from aigear.management.running_restore import resume_running_restore
            resume_running_restore(self, operation)
            return self._restore_result(run_id, target, operation.operation_id)
        except Exception as exc:
            raise OperationIncomplete('Restoration is incomplete', operation_id=operation.operation_id,
                phase=operation.phase, cause=exc, partial_result=self._restore_result(run_id, target, operation.operation_id,
                    operation.source.manifest, operation_status='incomplete')) from exc

    def _restore_result(self, run_id, target, operation_id, snapshot=None, *, operation_status='completed'):
        prefix = workspace_prefix(self.pipeline_version) if target == 'running' or snapshot is None else snapshot_prefix(
            'assets', self.pipeline_version, snapshot.run_id, snapshot.run_started_at)
        return RestoreResult(pipeline_version=self.pipeline_version, run_id=run_id, target=target,
            target_uri=f'gs://{self.bucket_name}/{prefix}', operation_status=operation_status, operation_id=operation_id)

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
        return self._with_terminal_recovery(self._register)

    def _register(self):
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

"""Conditional publication with one complete backup and resumable rollback."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from uuid import uuid4

from aigear.common.asset_context import RunOwnership, active_context
from aigear.common.schema.bucket_schema import ObjectNotFound, PreconditionFailed
from aigear.management.asset_errors import (BackupUnavailable, ExecutionConflict, IntegrityError, InvalidArtifact,
                                          OperationIncomplete, SnapshotConflict)
from aigear.management.asset_paths import artifact_path, manifest_key, release_prefix, validate_file_set, workspace_prefix
from aigear.management.artifact_validity import valid_step
from aigear.management.asset_operations import copy_members, verify_object
from aigear.management.manifest import (ArtifactObject, FixedManifest, ObjectExpectation, ObjectVersion,
    PendingOperation, ReleaseFile, ReleaseInfo, ReleaseManifest, WorkspaceManifest)

logger = logging.getLogger(__name__)


def _file_identity(file):
    return file.model_dump(mode='json', exclude={'generation'})


def _same_files(left, right):
    return {file.path: _file_identity(file) for file in left} == {file.path: _file_identity(file) for file in right}


def _read(bucket, key):
    try:
        meta = bucket.get_metadata(key)
    except ObjectNotFound:
        return None, None
    return meta, ReleaseManifest.from_json(bucket.read_bytes(key, generation=meta.generation))


def _validate(bucket, prefix, manifest):
    if manifest is None or not manifest.committed or manifest.pending_operation:
        raise InvalidArtifact('Release group is not committed', phase='validate')
    for file in manifest.files:
        verify_object(bucket, f'{prefix}/{file.path}', file)


def _write(bucket, key, manifest, generation, operation):
    return bucket.write_bytes(key, manifest.to_json().encode(), if_generation_match=generation,
        content_type='application/json', metadata={'aigear_operation': operation.operation_id})


class ReleaseManager:
    def __init__(self, assets):
        self.assets = assets

    @property
    def source_bucket(self):
        return self.assets._bucket

    @property
    def bucket(self):
        return self.assets._release_bucket

    def _setup(self, path):
        self.prefix = release_prefix(self.assets.pipeline_version, path)
        self.key = manifest_key(self.prefix)
        self.backup_prefix = 'backup/' + self.prefix
        self.backup_key = manifest_key(self.backup_prefix)

    def _select(self, workspace, extras, *, validate=True):
        records = []
        selected = set()
        for name, step in workspace.steps.items():
            files = [file for file in step.outputs if file.path.startswith('training/') or file.path in extras]
            if not files:
                continue
            if validate:
                valid_step(workspace, name, self.source_bucket, workspace_prefix(self.assets.pipeline_version))
            for file in files:
                path = file.path[len('training/'):] if file.path.startswith('training/') else file.path
                records.append(ReleaseFile(path=path, size=file.size, crc32c=file.crc32c, generation=file.generation,
                    source_path=file.path, source_object=ObjectVersion(key=f'{workspace_prefix(self.assets.pipeline_version)}/{file.path}', generation=file.generation),
                    step_name=name, inputs=step.inputs, producer_run_id=step.producer_run_id,
                    producer_step_revision=step.producer_step_revision))
                selected.add(file.path)
        if not any(file.source_path.startswith('training/') for file in records):
            raise InvalidArtifact('Publication requires valid training files', phase='select')
        if set(extras) - selected:
            raise InvalidArtifact('An extra file has no valid committed producer', phase='select')
        validate_file_set(file.path for file in records)
        return sorted(records, key=lambda file: file.path)

    def _check_paths(self, current):
        allowed = {self.key, *[f'{self.prefix}/{file.path}' for file in current.files]} if current else set()
        for meta in self.bucket.list_metadata():
            key = meta.name
            if key == self.prefix or self.prefix.startswith(key + '/'):
                raise SnapshotConflict('Release path overlaps an existing object', phase='preflight')
            if key.startswith(self.prefix + '/') and key not in allowed:
                raise SnapshotConflict('Release path contains unmanaged objects or nested targets', phase='preflight')
            if key.endswith('/manifest.json') and not key.startswith('backup/'):
                prefix = key.rsplit('/', 1)[0]
                if prefix != self.prefix and self.prefix.startswith(prefix + '/'):
                    raise SnapshotConflict('Release path overlaps another manifest target', phase='preflight')

    def _control(self):
        return RunOwnership(self.source_bucket, self.assets.pipeline_version).read()

    def _idle(self, workspace):
        if (active_context(required=False) is None and workspace.execution
                and workspace.execution.status in {'running', 'waiting', 'finalizing'}):
            raise ExecutionConflict('Use the active workflow context for publication', phase='acquire')
        if workspace.pending_step and workspace.pending_step.writes_started:
            raise InvalidArtifact('Pending outputs cannot be published', phase='acquire')

    def _pending(self, operation, workspace):
        if operation.release_path != self.prefix:
            raise SnapshotConflict('Pending operation uses a different release path', phase='resume')
        if (active_context(required=False) is None and workspace.execution
                and workspace.execution.status in {'running', 'waiting'}):
            raise ExecutionConflict('An active workflow owns the pending publication', phase='resume')

    def _expect(self, bucket, key, old=None):
        try:
            meta = bucket.get_metadata(key)
        except ObjectNotFound:
            return None
        if old is None or (meta.generation, meta.size, meta.crc32c) != (old.generation, old.size, old.crc32c):
            raise SnapshotConflict('Destination contains an unowned object version', phase='preflight')
        return meta.generation

    def publish(self, *, path=None, extra_files=None):
        self._setup(path)
        if extra_files is not None and not isinstance(extra_files, (list, tuple)):
            raise ValueError('extra_files must be a list of explicit artifact file paths')
        extras = sorted(artifact_path(value) for value in (extra_files or []))
        if any(value.endswith('/') for value in (extra_files or [])) or len(set(extras)) != len(extras):
            raise InvalidArtifact('extra_files must contain distinct files', phase='preflight')
        workspace, generation = self.assets._ownership().read()
        if workspace.pending_operation:
            operation = workspace.pending_operation
            self._pending(operation, workspace)
            if operation.kind != 'publish' or operation.extra_files != extras:
                raise SnapshotConflict('Retry cannot change the fixed publication selection', phase='resume')
            return self._execute(operation)
        self._idle(workspace)
        files = self._select(workspace, extras)
        current_meta, current = _read(self.bucket, self.key)
        backup_meta, backup = _read(self.bucket, self.backup_key)
        if (current and current.pending_operation) or (backup and backup.pending_operation):
            raise ExecutionConflict('Release target has an unfinished operation', phase='acquire')
        self._check_paths(current)
        if current:
            _validate(self.bucket, self.prefix, current)  # Never overwrite a complete backup from a broken current group.
            if _same_files(current.files, files):
                return self._info(current_meta.metadata.get('aigear_operation') or current_meta.generation, current)
        elif backup:
            _validate(self.bucket, self.backup_prefix, backup)
        old_files = {file.path: file for file in current.files} if current else {}
        expected = [ObjectExpectation(key=f'{self.prefix}/{file.path}',
                    generation=self._expect(self.bucket, f'{self.prefix}/{file.path}', old_files.get(file.path))) for file in files]
        previous = [FixedManifest(object=ObjectVersion(key=self.key, generation=current_meta.generation), manifest=current)] if current else []
        if backup:
            previous.append(FixedManifest(object=ObjectVersion(key=self.backup_key, generation=backup_meta.generation), manifest=backup))
        if current:
            old_backup = {file.path: file for file in backup.files} if backup else {}
            expected += [ObjectExpectation(key=f'{self.backup_prefix}/{file.path}',
                generation=self._expect(self.bucket, f'{self.backup_prefix}/{file.path}', old_backup.get(file.path))) for file in current.files]
            expected.append(ObjectExpectation(key=self.backup_key, generation=backup_meta.generation if backup_meta else None))
        expected.append(ObjectExpectation(key=self.key, generation=current_meta.generation if current_meta else None))
        operation = PendingOperation(operation_id=uuid4().hex, kind='publish', phase='preparing',
            release_path=self.prefix, extra_files=extras, started_at=datetime.now(timezone.utc),
            source=FixedManifest(object=ObjectVersion(key=RunOwnership(self.source_bucket, self.assets.pipeline_version).key, generation=generation), manifest=workspace.model_copy(deep=True)),
            previous_targets=previous, expected_targets=expected)
        workspace.pending_operation = operation
        RunOwnership(self.source_bucket, self.assets.pipeline_version)._write(workspace, generation, 'publish-preparing')
        return self._execute(operation)

    def _guard(self, operation):
        workspace, _ = self._control()
        if workspace.pending_operation is None or workspace.pending_operation.operation_id != operation.operation_id:
            raise ExecutionConflict('Workspace operation owner changed', phase='ownership')
        for fixed in [operation.source, *operation.previous_targets]:
            if isinstance(fixed.manifest, WorkspaceManifest) and fixed.manifest.execution and fixed.manifest.execution.status in {'running', 'waiting'}:
                expected = fixed.manifest.execution
                actual = workspace.execution
                if actual is None or (actual.run_id, actual.owner_token, actual.step_attempt_token, actual.worker_token) != (
                    expected.run_id, expected.owner_token, expected.step_attempt_token, expected.worker_token):
                    raise ExecutionConflict('Publication worker no longer owns its attempt', phase='ownership')
        meta, current = _read(self.bucket, self.key)
        if (current is None or meta.generation != operation.target_manifest_generation or current.pending_operation is None
                or current.pending_operation.operation_id != operation.operation_id):
            raise ExecutionConflict('Release target owner changed', phase='ownership')
        return meta, current

    def _reserve(self, operation):
        meta, current = _read(self.bucket, self.key)
        if current and current.pending_operation and current.pending_operation.operation_id == operation.operation_id:
            if operation.target_manifest_generation is None:
                if meta.metadata.get('aigear_operation') != operation.operation_id:
                    raise ExecutionConflict('Cannot identify target reservation', phase='acquire')
                operation.target_manifest_generation = meta.generation
                self.assets._persist_operation(operation)
            self._guard(operation)
            return
        if operation.target_manifest_generation is not None:
            raise ExecutionConflict('Target reservation changed', phase='acquire')
        expected = next(item.generation for item in operation.expected_targets if item.key == self.key) or '0'
        if (meta.generation if meta else '0') != expected or (current and current.pending_operation and current.pending_operation.operation_id != operation.predecessor_operation_id):
            self.assets._clear_operation(operation)
            raise ExecutionConflict('Another publisher acquired the target', phase='acquire')
        target = current.model_copy(deep=True) if current else ReleaseManifest(pipeline_version=self.assets.pipeline_version,
            source_pipeline_version=self.assets.pipeline_version, revision=0, updated_at=operation.started_at,
            committed=False, pending_operation=operation)
        target.pending_operation = operation
        target.revision += 1
        target.updated_at = datetime.now(timezone.utc)
        try:
            meta = _write(self.bucket, self.key, target, expected, operation)
        except PreconditionFailed:
            self.assets._clear_operation(operation)
            raise
        operation.target_manifest_generation = meta.generation
        self.assets._persist_operation(operation)

    def _prior(self, operation, key):
        return next((value for value in operation.previous_targets if value.object.key == key), None)

    def _delete(self, operation, key, generation):
        self._guard(operation)
        try:
            self.bucket.delete(key, if_generation_match=generation)
        except ObjectNotFound:
            pass

    def _collisions(self, operation, old_files, new_paths, prefix):
        for file in old_files:
            if file.path not in new_paths and any(file.path.casefold() == path.casefold()
                    or file.path.casefold().startswith(path.casefold() + '/') or path.casefold().startswith(file.path.casefold() + '/') for path in new_paths):
                self._delete(operation, f'{prefix}/{file.path}', file.generation)

    def _backup(self, operation, prior):
        old_backup = self._prior(operation, self.backup_key)
        meta, backup = _read(self.bucket, self.backup_key)
        if backup and backup.pending_operation is None and meta.metadata.get('aigear_operation') == operation.operation_id:
            if (not _same_files(backup.files, prior.manifest.files) or backup.published_at != prior.manifest.published_at
                    or backup.source_pipeline_version != prior.manifest.source_pipeline_version):
                raise SnapshotConflict('Committed backup differs from fixed prior release', phase='backup')
            for file in backup.files:
                progress = next((p for p in operation.progress if p.target.key == f'{self.backup_prefix}/{file.path}'), None)
                if progress is None or file.generation != progress.target.generation:
                    raise SnapshotConflict('Backup progress changed', phase='backup')
            _validate(self.bucket, self.backup_prefix, backup)
            operation.backup_manifest_generation = meta.generation
            self.assets._persist_operation(operation)
            self._cleanup_backup(operation, old_backup, backup)
            return
        if operation.backup_manifest_generation is None:
            if backup and backup.pending_operation:
                if backup.pending_operation.operation_id != operation.operation_id or meta.metadata.get('aigear_operation') != operation.operation_id:
                    raise ExecutionConflict('Backup belongs to another operation', phase='backup')
                operation.backup_manifest_generation = meta.generation
            else:
                expected = next(item.generation for item in operation.expected_targets if item.key == self.backup_key) or '0'
                if (meta.generation if meta else '0') != expected:
                    raise SnapshotConflict('Backup manifest changed', phase='backup')
                backup = ReleaseManifest(pipeline_version=prior.manifest.pipeline_version,
                    source_pipeline_version=prior.manifest.source_pipeline_version, revision=(backup.revision + 1 if backup else 0),
                    updated_at=operation.started_at, committed=False, pending_operation=operation)
                meta = _write(self.bucket, self.backup_key, backup, expected, operation)
                operation.backup_manifest_generation = meta.generation
            self.assets._persist_operation(operation)
        elif meta is None or meta.generation != operation.backup_manifest_generation:
            raise SnapshotConflict('Preparing backup changed', phase='backup')
        self._collisions(operation, old_backup.manifest.files if old_backup else [], {file.path for file in prior.manifest.files}, self.backup_prefix)
        copies = copy_members(self.bucket, self.bucket, operation, prior.manifest.files, self.prefix, self.backup_prefix,
            self.assets._persist_operation, before_copy=lambda: self._guard(operation))
        backup = prior.manifest.model_copy(deep=True)
        for file in backup.files:
            file.generation = copies[file.path].generation
        backup.pending_operation = None
        backup.revision += 1
        backup.updated_at = datetime.now(timezone.utc)
        meta = _write(self.bucket, self.backup_key, backup, operation.backup_manifest_generation, operation)
        operation.backup_manifest_generation = meta.generation
        self.assets._persist_operation(operation)
        self._cleanup_backup(operation, old_backup, backup)

    def _cleanup_backup(self, operation, old_backup, backup):
        if old_backup:
            retained = {file.path for file in backup.files}
            for file in old_backup.manifest.files:
                if file.path not in retained:
                    self._delete(operation, f'{self.backup_prefix}/{file.path}', file.generation)

    def _execute(self, operation):
        fields = {'operation_id': operation.operation_id, 'pipeline_version': self.assets.pipeline_version,
                  'phase': operation.phase, 'operation_kind': operation.kind}
        logger.info('asset_release_started', extra=fields)
        try:
            result = self._resume(operation)
            logger.info('asset_release_completed', extra={**fields, 'phase': 'completed'})
            return result
        except Exception as exc:
            logger.warning('asset_release_incomplete', extra={**fields, 'phase': operation.phase, 'error_type': type(exc).__name__})
            try:
                workspace, generation = self._control()
                if workspace.pending_operation and workspace.pending_operation.operation_id == operation.operation_id:
                    workspace.pending_operation.error = type(exc).__name__ + ':' + operation.phase
                    RunOwnership(self.source_bucket, self.assets.pipeline_version)._write(workspace, generation, 'release-incomplete')
            except Exception:
                pass  # The original operation and cause remain authoritative.
            raise OperationIncomplete('Release operation is incomplete', operation_id=operation.operation_id,
                phase=operation.phase, cause=exc, partial_result=ReleaseInfo(release_uri=f'gs://{self.assets._release_name}/{self.prefix}',
                    source_pipeline_version=self.assets.pipeline_version, files=[], operation_status='incomplete', operation_id=operation.operation_id)) from exc

    def _resume(self, operation):
        desired = (self._select(operation.source.manifest, operation.extra_files, validate=False) if operation.kind == 'publish'
                   else [file.model_copy(deep=True) for file in operation.source.manifest.files])
        meta, current = _read(self.bucket, self.key)
        if (current and current.pending_operation is None and meta.metadata.get('aigear_operation') == operation.operation_id
                and _same_files(current.files, desired)
                and current.source_pipeline_version == operation.source.manifest.pipeline_version
                and current.published_at == (operation.started_at if operation.kind == 'publish' else operation.source.manifest.published_at)):
            for file in current.files:
                progress = next((p for p in operation.progress if p.target.key == f'{self.prefix}/{file.path}'), None)
                if progress is None or file.generation != progress.target.generation:
                    raise SnapshotConflict('Committed candidate progress differs', phase='complete')
            _validate(self.bucket, self.prefix, current)
            self.assets._clear_operation(operation)
            return self._info(operation.operation_id, current, backup_available=operation.kind == 'rollback' or bool(self._prior(operation, self.key) or self._prior(operation, self.backup_key)))
        if operation.kind == 'publish':
            self._select(operation.source.manifest, operation.extra_files)
        self._reserve(operation)
        prior = self._prior(operation, self.key)
        if operation.kind == 'publish' and prior and operation.phase in {'preparing', 'backup'}:
            operation.phase = 'backup'
            self.assets._persist_operation(operation)
            self._backup(operation, prior)
        if operation.phase in {'preparing', 'backup'}:
            operation.phase = 'candidate'
            self.assets._persist_operation(operation)
        source_prefix = workspace_prefix(self.assets.pipeline_version) if operation.kind == 'publish' else self.backup_prefix
        source_bucket = self.source_bucket if operation.kind == 'publish' else self.bucket
        members = [ArtifactObject(path=file.source_path, generation=file.source_object.generation, size=file.size, crc32c=file.crc32c)
                   for file in desired] if operation.kind == 'publish' else operation.source.manifest.files
        mapping = {file.source_path: file.path for file in desired} if operation.kind == 'publish' else None
        old_files = prior.manifest.files if prior else []
        self._collisions(operation, old_files, {file.path for file in desired}, self.prefix)
        for cleanup in operation.cleanup_targets:
            relative = cleanup.key[len(self.prefix)+1:]
            if any(relative.casefold() == file.path.casefold() or relative.casefold().startswith(file.path.casefold() + '/')
                   or file.path.casefold().startswith(relative.casefold() + '/') for file in desired):
                self._delete(operation, cleanup.key, cleanup.generation)
        copies = copy_members(source_bucket, self.bucket, operation, members, source_prefix, self.prefix,
            self.assets._persist_operation, mapping=mapping, before_copy=lambda: self._guard(operation))
        for file in desired:
            file.generation = copies[file.source_path if operation.kind == 'publish' else file.path].generation
        retained = {file.path for file in desired}
        for expected in operation.expected_targets:
            if expected.key.startswith(self.prefix + '/') and expected.key != self.key and expected.key[len(self.prefix)+1:] not in retained and expected.generation:
                self._delete(operation, expected.key, expected.generation)
        for file in old_files:
            if file.path not in retained:
                self._delete(operation, f'{self.prefix}/{file.path}', file.generation)
        for cleanup in operation.cleanup_targets:
            self._delete(operation, cleanup.key, cleanup.generation)
        if operation.kind == 'publish':
            self._select(operation.source.manifest, operation.extra_files)  # Recheck pinned inputs before committing.
        else:
            source_meta = self.bucket.get_metadata(operation.source.object.key)
            if source_meta.generation != operation.source.object.generation:
                raise SnapshotConflict('Fixed backup manifest changed', phase='rollback')
        _, target_view = self._guard(operation)
        for file in desired:
            verify_object(self.bucket, f'{self.prefix}/{file.path}', file)
        candidate = ReleaseManifest(pipeline_version=operation.source.manifest.pipeline_version,
            source_pipeline_version=operation.source.manifest.pipeline_version,
            revision=target_view.revision + 1, updated_at=datetime.now(timezone.utc),
            published_at=operation.started_at if operation.kind == 'publish' else operation.source.manifest.published_at,
            files=desired)
        _write(self.bucket, self.key, candidate, operation.target_manifest_generation, operation)
        self.assets._clear_operation(operation)
        return self._info(operation.operation_id, candidate, backup_available=operation.kind == 'rollback' or bool(prior or self._prior(operation, self.backup_key)))

    def _info(self, operation_id, current, *, backup_available=None):
        if backup_available is None:
            _, backup = _read(self.bucket, self.backup_key)
            backup_available = bool(backup and backup.committed and not backup.pending_operation)
        return ReleaseInfo(release_uri=f'gs://{self.assets._release_name}/{self.prefix}',
            backup_uri=f'gs://{self.assets._release_name}/{self.backup_prefix}' if backup_available else None,
            source_pipeline_version=current.source_pipeline_version, files=current.files,
            operation_status='completed', operation_id=operation_id)

    def rollback(self, *, path=None):
        self._setup(path)
        workspace, generation = self.assets._ownership().read()
        pending = workspace.pending_operation
        if pending:
            self._pending(pending, workspace)
            if pending.kind == 'rollback':
                return self._execute(pending)
            if pending.kind != 'publish':
                raise OperationIncomplete('Another workspace operation is pending', phase='rollback')
            if pending.error is None:
                raise ExecutionConflict('Original publisher must have stopped before rollback', phase='rollback')
        else:
            self._idle(workspace)
        backup_meta, backup = _read(self.bucket, self.backup_key)
        if backup is None or not backup.committed or backup.pending_operation:
            raise BackupUnavailable('No complete committed backup is available', operation_id=pending.operation_id if pending else None, phase='rollback')
        try:
            _validate(self.bucket, self.backup_prefix, backup)
        except (ObjectNotFound, IntegrityError, InvalidArtifact) as exc:
            raise BackupUnavailable('Backup members are incomplete or changed', phase='rollback', cause=exc) from exc
        current_meta, current = _read(self.bucket, self.key)
        if not pending and current and current.pending_operation:
            raise ExecutionConflict('Another publisher owns this target', phase='rollback')
        if not pending and current and _same_files(current.files, backup.files):
            try:
                _validate(self.bucket, self.prefix, current)
            except (ObjectNotFound, IntegrityError, InvalidArtifact):
                pass
            else:
                return self._info(current_meta.metadata.get('aigear_operation') or current_meta.generation, current)
        known = {file.path: file for file in current.files} if current else {}
        previous = []
        if pending:
            prior = self._prior(pending, self.key)
            if prior:
                previous.append(prior)
                known = {file.path: file for file in prior.manifest.files}
            for progress in pending.progress:
                if progress.target.key.startswith(self.prefix + '/'):
                    path = progress.target.key[len(self.prefix)+1:]
                    known[path] = ArtifactObject(path='training/' + path, generation=progress.target.generation,
                        size=progress.size, crc32c=progress.crc32c)
            recorded = {item.target.key: item for item in pending.progress}
            candidates = {file.path: file for file in self._select(pending.source.manifest, pending.extra_files, validate=False)}
            # Writes whose responses were lost can be identified by their operation metadata.
            for expected in pending.expected_targets:
                if not expected.key.startswith(self.prefix + '/') or expected.key == self.key:
                    continue
                try:
                    actual = self.bucket.get_metadata(expected.key)
                except ObjectNotFound:
                    continue
                if actual.metadata.get('aigear_operation') == pending.operation_id:
                    path = expected.key[len(self.prefix)+1:]
                    if expected.key in recorded and actual.generation != recorded[expected.key].target.generation:
                        raise SnapshotConflict('Candidate version changed after publication progress', phase='rollback')
                    candidate = candidates[path]
                    if (actual.metadata.get('aigear_source_generation'), actual.size, actual.crc32c) != (candidate.source_object.generation, candidate.size, candidate.crc32c):
                        raise SnapshotConflict('Candidate provenance changed', phase='rollback')
                    known[path] = ArtifactObject(path='training/' + path, generation=actual.generation, size=actual.size, crc32c=actual.crc32c)
                else:
                    path = expected.key[len(self.prefix)+1:]
                    if path not in known or actual.generation != known[path].generation:
                        raise SnapshotConflict('Foreign candidate cannot be rolled back', phase='rollback')
            if isinstance(pending.source.manifest, WorkspaceManifest):
                previous.append(pending.source)
        elif current:
            previous.append(FixedManifest(object=ObjectVersion(key=self.key, generation=current_meta.generation), manifest=current))
        if not pending:
            previous.append(FixedManifest(object=ObjectVersion(key=RunOwnership(self.source_bucket, self.assets.pipeline_version).key, generation=generation), manifest=workspace.model_copy(deep=True)))
        paths = sorted({file.path for file in backup.files})
        versions = {path: self._expect(self.bucket, f'{self.prefix}/{path}', file) for path, file in known.items()}
        expected = [ObjectExpectation(key=f'{self.prefix}/{path}', generation=self._expect(self.bucket, f'{self.prefix}/{path}', known.get(path))) for path in paths]
        expected.append(ObjectExpectation(key=self.key, generation=current_meta.generation if current_meta else None))
        operation = PendingOperation(operation_id=uuid4().hex, kind='rollback', phase='preparing', release_path=self.prefix,
            predecessor_operation_id=pending.operation_id if pending else None, started_at=datetime.now(timezone.utc),
            source=FixedManifest(object=ObjectVersion(key=self.backup_key, generation=backup_meta.generation), manifest=backup),
            previous_targets=previous, expected_targets=expected,
            cleanup_targets=[ObjectVersion(key=f'{self.prefix}/{path}', generation=value)
                             for path, value in versions.items() if path not in paths and value is not None])
        workspace.pending_operation = operation
        RunOwnership(self.source_bucket, self.assets.pipeline_version)._write(workspace, generation, 'rollback-preparing')
        return self._execute(operation)

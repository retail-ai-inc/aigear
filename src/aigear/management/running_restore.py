"""Whole-workspace restore while a durable operation excludes readers/writers."""
from __future__ import annotations

from datetime import datetime, timezone

from aigear.common.asset_context import RunOwnership
from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.management.asset_errors import ExecutionConflict, SnapshotConflict
from aigear.management.asset_operations import copy_members, verify_object
from aigear.management.asset_paths import workspace_prefix
from aigear.management.manifest import ArtifactObject, RestoredFrom, SnapshotManifest
from aigear.management.snapshot_transfer import _same_members, write_manifest


def resume_running_restore(assets, operation):
    bucket = assets._bucket
    ownership = RunOwnership(bucket, assets.pipeline_version)
    current, _ = ownership.read()
    if current.pending_operation is None or current.pending_operation.operation_id != operation.operation_id:
        raise ExecutionConflict('Running restore no longer owns the workspace', phase='restore')
    source = operation.source.manifest
    source_key = operation.source.object.key
    source_prefix = source_key.rsplit('/', 1)[0]
    prefix = workspace_prefix(assets.pipeline_version)
    previous = operation.previous_targets[0].manifest if operation.previous_targets else None
    if operation.phase != 'cleaning':
        source_meta = bucket.get_metadata(source_key)
        source_manifest = SnapshotManifest.from_json(bucket.read_bytes(source_key, generation=source_meta.generation))
        if operation.source_manifest_generation is None:
            if source_manifest.pending_operation is None:
                if source_meta.generation != operation.source.object.generation:
                    raise SnapshotConflict('Trash source manifest changed', phase='restore')
                source_manifest.pending_operation = operation
                source_manifest.revision += 1
                source_manifest.updated_at = datetime.now(timezone.utc)
                source_meta = write_manifest(bucket, source_key, source_manifest, source_meta.generation, operation,
                                             custom_time=source_meta.custom_time)
            elif source_manifest.pending_operation.operation_id != operation.operation_id or source_meta.metadata.get('aigear_operation') != operation.operation_id:
                raise SnapshotConflict('Trash source is owned by another restore', phase='restore')
            operation.source_manifest_generation = source_meta.generation
            assets._persist_operation(operation)
        if source_meta.generation != operation.source_manifest_generation or not _same_members(source_manifest, source):
            raise SnapshotConflict('Fixed trash source changed', phase='restore')
        operation.phase = 'copying'
        assets._persist_operation(operation)
        desired = {member.path for member in source.committed_files()}
        old = {member.path: member for member in previous.committed_files()} if previous else {}
        # Portable file/dir or case aliases cannot coexist in the local projection.
        # The verified trash snapshot remains the complete source while these
        # version-fenced old workspace entries are removed.
        collisions = [name for name in old if name not in desired and any(
            name.casefold() == path.casefold() or name.casefold().startswith(path.casefold() + '/')
            or path.casefold().startswith(name.casefold() + '/') for path in desired)]
        for name in collisions:
            try:
                bucket.delete(f'{prefix}/{name}', if_generation_match=old[name].generation)
            except ObjectNotFound:
                pass
        copies = copy_members(bucket, bucket, operation, source.committed_files(), source_prefix, prefix, assets._persist_operation)
        for name, member in old.items():
            if name not in desired:
                try:
                    bucket.delete(f'{prefix}/{name}', if_generation_match=member.generation)
                except ObjectNotFound:
                    pass
        if bucket.get_metadata(source_key).generation != operation.source_manifest_generation:
            raise SnapshotConflict('Source manifest changed during restore', phase='restore')
        current, generation = ownership.read()
        if current.pending_operation is None or current.pending_operation.operation_id != operation.operation_id:
            raise ExecutionConflict('Restore operation changed', phase='commit')
        current.steps = {name: step.model_copy(deep=True) for name, step in source.steps.items()}
        for step in current.steps.values():
            step.outputs = [ArtifactObject(path=o.path, generation=copies[o.path].generation, size=o.size, crc32c=o.crc32c) for o in step.outputs]
        current.last_run_id = current.last_run_started_at = None
        current.execution = current.pending_step = None
        if current.latest_run_started_at is None:
            current.latest_run_id, current.latest_run_started_at = source.run_id, source.run_started_at
        current.restored_from = RestoredFrom(run_id=source.run_id, manifest=operation.source.object,
            operation_id=operation.operation_id, restored_at=operation.started_at)
        operation.phase = 'cleaning'
        current.pending_operation = operation
        ownership._write(current, generation, 'restore-commit')
    current, _ = ownership.read()
    if (current.pending_operation is None or current.pending_operation.operation_id != operation.operation_id
            or current.restored_from is None or current.restored_from.operation_id != operation.operation_id):
        raise ExecutionConflict('Restore commit changed', phase='cleaning')
    for member in current.committed_files():
        meta = verify_object(bucket, f'{prefix}/{member.path}', member)
        if meta.custom_time is not None:
            raise SnapshotConflict('Restored running object inherited Custom-Time', phase='cleaning')
    try:
        source_meta = bucket.get_metadata(source_key)
        if source_meta.generation != operation.source_manifest_generation:
            raise SnapshotConflict('Source manifest changed before cleanup', phase='cleaning')
    except ObjectNotFound:
        if not all(item.source_deleted for item in operation.progress):
            raise SnapshotConflict('Source manifest disappeared before member cleanup', phase='cleaning')
    for item in operation.progress:
        if item.source_deleted:
            continue
        try:
            bucket.delete(item.source.key, if_generation_match=item.source.generation)
        except ObjectNotFound:
            pass
        item.source_deleted = True
        assets._persist_operation(operation)
    try:
        bucket.delete(source_key, if_generation_match=operation.source_manifest_generation)
    except ObjectNotFound:
        pass
    assets._clear_operation(operation)

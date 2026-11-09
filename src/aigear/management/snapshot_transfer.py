"""Snapshot transfer: commit destination, remove fixed source, then start retention."""
from __future__ import annotations

from datetime import datetime, timezone

from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.management.asset_errors import SnapshotConflict
from aigear.management.asset_operations import copy_members, verify_object
from aigear.management.asset_paths import manifest_key, snapshot_prefix
from aigear.management.manifest import ArtifactObject, ObjectVersion, SnapshotManifest, VerifiedObject


def write_manifest(bucket, key, snapshot, generation, operation, *, custom_time=None):
    return bucket.write_bytes(key, snapshot.to_json().encode(), if_generation_match=generation,
        content_type='application/json', custom_time=custom_time, metadata={'aigear_operation': operation.operation_id})


def _same_members(left, right):
    from aigear.management.assets import _step_identity
    return (left.run_id == right.run_id and left.registered_at == right.registered_at
            and left.run_started_at == right.run_started_at and left.sealed == right.sealed
            and set(left.steps) == set(right.steps)
            and all(_step_identity(left.steps[name]) == _step_identity(step) for name, step in right.steps.items()))


def _read(bucket, key):
    meta = bucket.get_metadata(key)
    return meta, SnapshotManifest.from_json(bucket.read_bytes(key, generation=meta.generation))


def resume_transfer(assets, operation):
    bucket = assets._bucket
    fixed = operation.source.manifest
    source_key = operation.source.object.key
    source_prefix = source_key.rsplit('/', 1)[0]
    location = 'trash' if operation.kind == 'retire' else 'assets'
    prefix = snapshot_prefix(location, assets.pipeline_version, fixed.run_id, fixed.run_started_at)
    target_key = manifest_key(prefix)
    def verify_target(snapshot, *, retention=False):
        for member in snapshot.committed_files():
            progress = next((p for p in operation.progress if p.target.key == f'{prefix}/{member.path}'), None)
            if progress is None or progress.target.generation != member.generation:
                raise SnapshotConflict('Transfer member version differs from progress', phase='verify')
            meta = verify_object(bucket, f'{prefix}/{member.path}', member)
            if retention and meta.custom_time != (operation.retired_at if location == 'trash' else None):
                raise SnapshotConflict('Transfer member Custom-Time changed', phase='verify')
    # A final target can exist after a lost final response. Never recopy from a
    # partly removed source; verify the complete target and finish its control CAS.
    try:
        target_meta, target = _read(bucket, target_key)
    except ObjectNotFound:
        target_meta, target = None, None
    if (target and target.pending_operation is None and target.committed and _same_members(target, fixed)
            and target_meta.metadata.get('aigear_operation') == operation.operation_id):
        verify_target(target, retention=True)
        if location == 'trash' and (target.retired_at != operation.retired_at or target_meta.custom_time != operation.retired_at):
            raise SnapshotConflict('Transfer retention time changed', phase='complete')
        if location == 'assets' and target_meta.custom_time is not None:
            raise SnapshotConflict('Restored manifest inherited Custom-Time', phase='complete')
        assets._clear_operation(operation)
        return target

    if operation.phase != 'cleaning':
        source_meta, source = _read(bucket, source_key)
        if operation.source_manifest_generation is None:
            if source.pending_operation is None:
                if source_meta.generation != operation.source.object.generation:
                    raise SnapshotConflict('Source manifest changed before transfer', phase='prepare')
                source.pending_operation = operation
                source.revision += 1
                source.updated_at = datetime.now(timezone.utc)
                source_meta = write_manifest(bucket, source_key, source, source_meta.generation, operation,
                                             custom_time=source_meta.custom_time)
            elif source.pending_operation.operation_id != operation.operation_id or source_meta.metadata.get('aigear_operation') != operation.operation_id:
                raise SnapshotConflict('Source is owned by another operation', phase='prepare')
            operation.source_manifest_generation = source_meta.generation
            assets._persist_operation(operation)
        if source_meta.generation != operation.source_manifest_generation or not _same_members(source, fixed):
            raise SnapshotConflict('Fixed source manifest changed', phase='copy')
        if target is None:
            target = fixed.model_copy(deep=True)
            target.steps, target.included_steps = {}, []
            target.location, target.retired_at = location, None
            target.committed, target.pending_operation = False, operation
            target.revision += 1
            target.updated_at = datetime.now(timezone.utc)
            target_meta = write_manifest(bucket, target_key, target, '0', operation)
            operation.target_manifest_generation = target_meta.generation
            assets._persist_operation(operation)
        elif (operation.kind == 'restore' and operation.previous_targets and target.pending_operation is None
              and target.committed and _same_members(target, fixed)
              and target_meta.generation == operation.previous_targets[0].object.generation):
            for step in target.steps.values():
                for member in step.outputs:
                    verify_object(bucket, f'{prefix}/{member.path}', member)
                    original = next(o for o in fixed.committed_files() if o.path == member.path)
                    if not any(item.target.key == f'{prefix}/{member.path}' for item in operation.progress):
                        operation.progress.append(VerifiedObject(
                            source=ObjectVersion(key=f'{source_prefix}/{member.path}', generation=original.generation),
                            target=ObjectVersion(key=f'{prefix}/{member.path}', generation=member.generation),
                            size=member.size, crc32c=member.crc32c))
            assets._persist_operation(operation)
            target.pending_operation = operation
            target.revision += 1
            target.updated_at = datetime.now(timezone.utc)
            target_meta = write_manifest(bucket, target_key, target, target_meta.generation, operation)
            operation.target_manifest_generation = target_meta.generation
            assets._persist_operation(operation)
        elif target.pending_operation is None or target.pending_operation.operation_id != operation.operation_id:
            raise SnapshotConflict('Transfer destination changed', phase='copy')
        elif operation.target_manifest_generation is None:
            if target_meta.metadata.get('aigear_operation') != operation.operation_id:
                raise SnapshotConflict('Transfer destination ownership changed', phase='copy')
            operation.target_manifest_generation = target_meta.generation
            assets._persist_operation(operation)
        if target.committed:
            if not _same_members(target, fixed) or target_meta.metadata.get('aigear_operation') != operation.operation_id:
                raise SnapshotConflict('Committed destination differs from fixed source', phase='copy')
            for member in target.committed_files():
                progress = next((p for p in operation.progress if p.target.key == f'{prefix}/{member.path}'), None)
                if progress is None or progress.target.generation != member.generation:
                    raise SnapshotConflict('Committed copy progress differs', phase='copy')
                verify_object(bucket, f'{prefix}/{member.path}', member)
            operation.target_manifest_generation = target_meta.generation
        else:
            if target_meta.generation != operation.target_manifest_generation:
                raise SnapshotConflict('Preparing destination changed', phase='copy')
            operation.phase = 'copying'
            assets._persist_operation(operation)
            copies = copy_members(bucket, bucket, operation, fixed.committed_files(), source_prefix, prefix, assets._persist_operation)
            if bucket.get_metadata(source_key).generation != operation.source_manifest_generation:
                raise SnapshotConflict('Source manifest changed during copy', phase='copy')
            target = fixed.model_copy(deep=True)
            for step in target.steps.values():
                step.outputs = [ArtifactObject(path=o.path, generation=copies[o.path].generation, size=o.size, crc32c=o.crc32c) for o in step.outputs]
            target.location, target.retired_at = location, None
            target.committed, target.pending_operation = True, operation
            target.revision += 1
            target.updated_at = datetime.now(timezone.utc)
            target_meta = write_manifest(bucket, target_key, target, operation.target_manifest_generation, operation)
            operation.target_manifest_generation = target_meta.generation
        operation.phase = 'cleaning'
        assets._persist_operation(operation)

    target_meta, target = _read(bucket, target_key)
    if target_meta.generation != operation.target_manifest_generation or not target.committed or not _same_members(target, fixed):
        raise SnapshotConflict('Destination changed before source cleanup', phase='cleaning')
    verify_target(target)
    def check_source_manifest():
        try:
            meta = bucket.get_metadata(source_key)
        except ObjectNotFound:
            if not all(progress.source_deleted for progress in operation.progress):
                raise SnapshotConflict('Source manifest disappeared before file cleanup', phase='cleaning')
            return
        if meta.generation != operation.source_manifest_generation:
            raise SnapshotConflict('Source manifest changed before cleanup', phase='cleaning')
    check_source_manifest()
    for progress in operation.progress:
        if progress.source_deleted:
            continue
        check_source_manifest()
        try:
            bucket.delete(progress.source.key, if_generation_match=progress.source.generation)
        except ObjectNotFound:
            pass
        progress.source_deleted = True
        assets._persist_operation(operation)
    try:
        # Source manifest is removed last and only at the exact occupied version.
        bucket.delete(source_key, if_generation_match=operation.source_manifest_generation)
    except ObjectNotFound:
        pass
    if location == 'trash':
        if operation.retired_at is None:
            operation.retired_at = datetime.now(timezone.utc)
            assets._persist_operation(operation)
        for progress in operation.progress:
            if progress.custom_time_set:
                continue
            meta = bucket.get_metadata(progress.target.key)
            if meta.generation != progress.target.generation:
                raise SnapshotConflict('Trash object changed before retention update', phase='custom-time')
            if meta.custom_time != operation.retired_at:
                bucket.update_metadata(meta.name, if_generation_match=meta.generation,
                    if_metageneration_match=meta.metageneration, custom_time=operation.retired_at)
            progress.custom_time_set = True
            assets._persist_operation(operation)
    target.retired_at = operation.retired_at if location == 'trash' else None
    verify_target(target, retention=True)
    target.pending_operation = None
    target.revision += 1
    target.updated_at = datetime.now(timezone.utc)
    write_manifest(bucket, target_key, target, operation.target_manifest_generation, operation,
                   custom_time=operation.retired_at if location == 'trash' else None)
    assets._clear_operation(operation)
    return target

"""Fixed-source, version-fenced object copies shared by asset operations."""
from __future__ import annotations

from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.management.asset_errors import IntegrityError, SnapshotConflict
from aigear.management.manifest import ObjectVersion, VerifiedObject


def verify_object(bucket, key, member):
    meta = bucket.get_metadata(key)
    if (meta.generation, meta.size, meta.crc32c) != (member.generation, member.size, member.crc32c):
        raise IntegrityError("Object differs from its committed manifest", phase="verify")
    return meta


def copy_members(source_bucket, target_bucket, operation, members, source_prefix, target_prefix, persist,
                 *, mapping=None, before_copy=None):
    expectations = {value.key: value.generation for value in operation.expected_targets}
    progress = {value.target.key: value for value in operation.progress}
    result = {}
    for member in members:
        if before_copy:
            before_copy()
        path = mapping[member.path] if mapping is not None else member.path
        source_key, target_key = f"{source_prefix}/{member.path}", f"{target_prefix}/{path}"
        verify_object(source_bucket, source_key, member)
        try:
            meta = target_bucket.get_metadata(target_key)
        except ObjectNotFound:
            meta = None
        if target_key in progress:
            item = progress[target_key]
            if meta is None or (meta.generation, meta.size, meta.crc32c) != (item.target.generation, item.size, item.crc32c):
                raise SnapshotConflict("Copied object changed", operation_id=operation.operation_id, phase="copy")
        else:
            recovered = (meta is not None and meta.metadata.get('aigear_operation') == operation.operation_id
                         and meta.metadata.get('aigear_source_generation') == member.generation
                         and (meta.size, meta.crc32c) == (member.size, member.crc32c))
            if not recovered:
                expected = expectations[target_key] or "0"
                if (meta.generation if meta else "0") != expected:
                    raise SnapshotConflict("Copy destination changed", operation_id=operation.operation_id, phase="copy")
                meta = source_bucket.copy_blob(source_key, target_key, destination_bucket=target_bucket,
                    source_generation=member.generation, if_generation_match=expected,
                    metadata={'aigear_operation': operation.operation_id, 'aigear_source_generation': member.generation})
            if (meta.size, meta.crc32c) != (member.size, member.crc32c):
                raise IntegrityError("Copy checksum differs", operation_id=operation.operation_id, phase="copy")
            item = VerifiedObject(source=ObjectVersion(key=source_key, generation=member.generation),
                target=ObjectVersion(key=target_key, generation=meta.generation), size=meta.size, crc32c=meta.crc32c)
            operation.progress.append(item)
            persist(operation)
        result[member.path] = meta
    return result

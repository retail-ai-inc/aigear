"""Managed write intents, lineage and step commit/cleanup helpers."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from aigear.common.asset_context import active_context
from aigear.common.schema.bucket_schema import ObjectNotFound, PreconditionFailed
from aigear.management.asset_errors import ExecutionConflict, InvalidArtifact, OperationIncomplete
from aigear.management.asset_paths import validate_file_set, workspace_prefix
from aigear.management.artifact_validity import invalidate_downstream, valid_step
from aigear.management.manifest import ArtifactObject, ExternalInput, ManagedInput, PendingWrite, StepRecord


def sanitize(value):
    if value is None:
        return None
    def clean(match):
        parsed = urlsplit(match.group())
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', ''))
    value = re.sub(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s]+', clean, str(value))
    return re.sub(r'(?i)(token|password|secret|signature|credential|api_key)\s*[=:]\s*[^\s&]+', r'\1=[redacted]', value)


def record_input(name, *, uri=None, version=None):
    context = active_context()
    manifest, generation = context.current()
    source = ExternalInput(name=sanitize(name), uri=sanitize(uri), version=sanitize(version))
    if source not in manifest.pending_step.inputs:
        manifest.pending_step.inputs.append(source)
        context.ownership._write(manifest, generation, "input")


def prepare_write(context, path):
    manifest, generation = context.current()
    if manifest.pending_operation:
        raise ExecutionConflict("A workspace operation is pending", phase="write")
    others = [o.path for name, step in manifest.steps.items() if name != context.step for o in step.outputs]
    validate_file_set([*others, path])
    owned = {o.path for o in manifest.steps.get(context.step, StepRecord(status="missing")).outputs}
    owned.update(o.path for o in manifest.pending_step.outputs)
    validate_file_set([name for name in owned if name != path] + [path])
    validate_file_set([o.path for o in manifest.pending_step.outputs if o.path != path] + [path])
    key = f"{workspace_prefix(manifest.pipeline_version)}/{path}"
    try:
        meta = context.ownership.bucket.get_metadata(key)
        expected = meta.generation
        previous = next((o for o in manifest.pending_step.outputs if o.path == path), None)
        if previous is None:
            previous = next((o for o in manifest.steps.get(context.step, StepRecord(status="missing")).outputs if o.path == path), None)
        if previous is None or previous.generation != expected:
            raise ExecutionConflict("Destination is not an owned output version", phase="write")
    except ObjectNotFound:
        expected = "0"
    if any(write.path == path for write in manifest.pending_step.writes):
        raise OperationIncomplete("An earlier write requires reconciliation", phase="write")
    invalidate_downstream(manifest, context.step)
    intent = PendingWrite(path=path, expected_generation=expected, write_id=uuid4().hex)
    manifest.pending_step.writes_started = True
    manifest.pending_step.writes.append(intent)
    context.ownership._write(manifest, generation, "write-intent")
    return intent


def finish_write(context, intent, meta):
    manifest, generation = context.current()
    if intent not in manifest.pending_step.writes:
        raise ExecutionConflict("Write intent changed", phase="write")
    output = ArtifactObject(path=intent.path, generation=meta.generation, size=meta.size, crc32c=meta.crc32c)
    manifest.pending_step.outputs = [o for o in manifest.pending_step.outputs if o.path != intent.path] + [output]
    manifest.pending_step.writes.remove(intent)
    context.ownership._write(manifest, generation, "write-complete")


def reconcile_writes(context, manifest):
    bucket = context.ownership.bucket
    prefix = workspace_prefix(manifest.pipeline_version)
    for intent in list(manifest.pending_step.writes):
        try:
            meta = bucket.get_metadata(f"{prefix}/{intent.path}")
        except ObjectNotFound:
            if intent.expected_generation != "0":
                raise OperationIncomplete("Prior output disappeared during write", phase="reconcile")
            continue
        if meta.metadata.get("aigear_write_id") == intent.write_id:
            output = ArtifactObject(path=intent.path, generation=meta.generation, size=meta.size, crc32c=meta.crc32c)
            manifest.pending_step.outputs = [o for o in manifest.pending_step.outputs if o.path != intent.path] + [output]
        elif meta.generation != intent.expected_generation:
            raise ExecutionConflict("Unrecognized output version after interrupted write", phase="reconcile")
    manifest.pending_step.writes.clear()


def _delete(bucket, key, generation):
    try:
        bucket.delete(key, if_generation_match=generation)
    except ObjectNotFound:
        pass
    except PreconditionFailed as exc:
        raise ExecutionConflict("Cleanup target changed", phase="cleanup", cause=exc) from exc


def commit_step(context):
    manifest, generation = context.current()
    if manifest.pending_operation is not None:
        raise OperationIncomplete("A workspace operation must finish before step commit", phase="commit")
    reconcile_writes(context, manifest)
    pending = manifest.pending_step
    for source in pending.inputs:
        if isinstance(source, ManagedInput):
            producer = valid_step(manifest, source.step_name, context.ownership.bucket, workspace_prefix(manifest.pipeline_version))
            output = next((o for o in producer.outputs if o.path == source.artifact), None)
            if (producer.producer_run_id, producer.producer_step_revision) != (source.run_id, source.step_revision) or output is None or output.generation != source.observed_object.generation:
                raise InvalidArtifact("Input changed before step commit", phase="commit")
    previous = manifest.steps.get(context.step)
    if not pending.outputs and (pending.inputs or (previous and previous.outputs)):
        raise InvalidArtifact("An artifact step cannot commit zero outputs", phase="commit")
    invalidate_downstream(manifest, context.step)
    # Persist invalidity and reconciled versions before deleting historical files.
    context.ownership._write(manifest, generation, "commit-preparing")
    if previous:
        retained = {o.path for o in pending.outputs}
        for output in previous.outputs:
            if output.path not in retained:
                _delete(context.ownership.bucket, f"{workspace_prefix(manifest.pipeline_version)}/{output.path}", output.generation)
    def update(current):
        if current.pending_step.writes:
            raise OperationIncomplete("Unfinished writes prevent commit", phase="commit")
        if current.pending_step.outputs:
            current.steps[context.step] = StepRecord(status="succeeded", producer_run_id=context.run_id,
                producer_step_revision=uuid4().hex, completed_at=datetime.now(timezone.utc),
                inputs=current.pending_step.inputs, outputs=current.pending_step.outputs)
    return context.commit(update)


def fail_step(context):
    current, generation = context.current()
    reconcile_writes(context, current)
    context.ownership._write(current, generation, "cleanup-preparing")
    def cleanup(manifest):
        for output in manifest.pending_step.outputs:
            _delete(context.ownership.bucket, f"{workspace_prefix(manifest.pipeline_version)}/{output.path}", output.generation)
        invalidate_downstream(manifest, context.step)
        previous = manifest.steps.get(context.step)
        if previous:
            previous.status = "failed"
        else:
            manifest.steps[context.step] = StepRecord(status="failed")
    return context.fail(cleanup)

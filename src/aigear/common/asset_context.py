"""Internal run/worker ownership protocol; execution entry points opt in in PR-05.

No lease expiry or lock objects: every transition uses the running manifest CAS.
Tokens identify capabilities, not log fields. Environment identity alone never
installs an active context. Recovery requires an explicit stopped-worker check.
"""
from __future__ import annotations

import logging
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

from aigear.common.schema.bucket_schema import ObjectNotFound, PreconditionFailed, VersionMismatch
from aigear.management.asset_errors import ExecutionConflict, OperationIncomplete
from aigear.management.asset_paths import identifier, manifest_key, workspace_prefix, utc_datetime
from aigear.management.manifest import Execution, PendingStep, WorkspaceManifest

logger = logging.getLogger(__name__)
_active = ContextVar("aigear_asset_context", default=None)
_OCCUPIED = {"running", "waiting", "finalizing"}


class RunOwnership:
    def __init__(self, bucket, pipeline_version):
        self.bucket = bucket
        self.pipeline_version = identifier(pipeline_version)
        self.key = manifest_key(workspace_prefix(pipeline_version))

    def read(self):
        try:
            meta = self.bucket.get_metadata(self.key)
        except ObjectNotFound:
            return WorkspaceManifest(pipeline_version=self.pipeline_version, revision=0,
                                     updated_at=datetime.now(timezone.utc)), "0"
        try:
            manifest = WorkspaceManifest.from_json(self.bucket.read_bytes(self.key, generation=meta.generation))
        except (VersionMismatch, ObjectNotFound) as exc:
            raise ExecutionConflict("Workspace changed while reading", phase="read", cause=exc) from exc
        if manifest.pipeline_version != self.pipeline_version:
            raise ExecutionConflict("Workspace pipeline identity mismatch", phase="read")
        return manifest, meta.generation

    def _write(self, manifest, generation, phase):
        manifest.revision += 1
        manifest.updated_at = datetime.now(timezone.utc)
        manifest = WorkspaceManifest.model_validate(manifest.model_dump())
        try:
            self.bucket.write_json(self.key, manifest.model_dump(mode="json"), if_generation_match=generation)
        except (PreconditionFailed, VersionMismatch) as exc:
            raise ExecutionConflict("Workspace changed concurrently", phase=phase, cause=exc) from exc
        execution = manifest.execution
        logger.info("asset_execution_transition", extra={"phase": phase, "pipeline_version": self.pipeline_version,
                    "run_id": manifest.latest_run_id, "step_name": execution.current_step if execution else None,
                    "attempt_id": hashlib.sha256(execution.step_attempt_token.encode()).hexdigest()[:12]
                    if execution and execution.step_attempt_token else None,
                    "failure_layer": "pipeline" if phase == "failure" else None})
        return manifest

    @staticmethod
    def _owned(manifest, run_id, owner_token, step=None, attempt=None, worker=None):
        execution = manifest.execution
        if execution is None or (execution.run_id, execution.owner_token) != (run_id, owner_token):
            raise ExecutionConflict("Run owner does not match", phase="ownership")
        if step is not None and (execution.current_step, execution.step_attempt_token) != (step, attempt):
            raise ExecutionConflict("Step attempt does not match", phase="ownership")
        if worker is not None and execution.worker_token != worker:
            raise ExecutionConflict("Worker does not match", phase="ownership")
        return execution

    def acquire(self, run_id, started_at):
        identifier(run_id)
        started_at = utc_datetime(started_at)
        manifest, generation = self.read()
        if manifest.pending_operation or manifest.pending_step:
            raise ExecutionConflict("Workspace has pending work", phase="acquire")
        if manifest.execution and manifest.execution.status in _OCCUPIED:
            raise ExecutionConflict("Workspace is occupied", phase="acquire")
        if manifest.latest_run_started_at is not None and started_at <= manifest.latest_run_started_at:
            raise ExecutionConflict("Run is older than the replay watermark", phase="acquire")
        if run_id == manifest.latest_run_id:
            raise ExecutionConflict("Run ID has already been used", phase="acquire")
        manifest.latest_run_id = manifest.last_run_id = run_id
        manifest.latest_run_started_at = manifest.last_run_started_at = started_at
        manifest.execution = Execution(run_id=run_id, run_started_at=started_at,
                                       owner_token=uuid4().hex, status="waiting")
        return self._write(manifest, generation, "acquire").execution

    def prepare_step(self, run_id, owner_token, step):
        """Reserve before creating a VM; redelivery reuses the same attempt/VM.

        A committed step returns None. A running attempt is never dispatched
        again, even with the same run ID. Waiting between VMs retains the owner.
        """
        manifest, generation = self.read()
        execution = self._owned(manifest, run_id, owner_token)
        if execution.status != "waiting":
            raise ExecutionConflict("Run is not waiting for dispatch", phase="dispatch")
        if step in execution.committed_steps:
            return None
        if execution.current_step is not None:
            if execution.current_step in execution.committed_steps:
                execution.previous_step_name = execution.current_step
                execution.previous_step_attempt_token = execution.step_attempt_token
                execution.current_step = execution.step_attempt_token = None
            elif execution.current_step != step:
                raise ExecutionConflict("Another step is reserved", phase="dispatch")
            else:
                return execution
        execution.current_step, execution.step_attempt_token = step, uuid4().hex
        execution.worker_token = None
        return self._write(manifest, generation, "dispatch").execution

    @contextmanager
    def claim(self, run_id, owner_token, step, attempt):
        """Claim once per process invocation. Context identity cannot be forged by run ID."""
        if _active.get() is not None:
            raise ExecutionConflict("An execution context is already active", phase="claim")
        manifest, generation = self.read()
        execution = self._owned(manifest, run_id, owner_token, step, attempt)
        if (execution.status != "waiting" or execution.worker_token is not None
                or step in execution.committed_steps):
            raise ExecutionConflict("Attempt has already been claimed", phase="claim")
        worker = uuid4().hex
        execution.worker_token, execution.status = worker, "running"
        manifest.pending_step = PendingStep(run_id=run_id, step_name=step, step_attempt_token=attempt)
        self._write(manifest, generation, "claim")
        context = AssetContext(self, run_id, owner_token, step, attempt, worker)
        token = _active.set(context)
        try:
            yield context
        finally:
            _active.reset(token)

    def finalize(self, run_id, owner_token, step, attempt, *, succeeded, seal):
        """Persist finalizing before sealing. A failing seal keeps ownership.

        The PR-06 callback must be idempotent and seal registered checkpoints;
        no public register capability is provided by this protocol.
        """
        manifest, generation = self.read()
        execution = self._owned(manifest, run_id, owner_token, step, attempt)
        outcome = "succeeded" if succeeded else "failed"
        if execution.status in {"succeeded", "failed"}:
            if execution.status != outcome:
                raise ExecutionConflict("Terminal outcome changed", phase="finalize")
            return manifest
        if execution.terminal_status not in {None, outcome}:
            raise ExecutionConflict("Terminal outcome changed", phase="finalize")
        if succeeded and (step not in execution.committed_steps or manifest.pending_step is not None):
            raise ExecutionConflict("Success requires a committed step", phase="finalize")
        if execution.status != "finalizing":
            execution.status, execution.terminal_status = "finalizing", outcome
            # Pending outputs remain recorded for PR-05 failure cleanup, but
            # cannot remain an active PendingStep once execution is finalizing.
            if manifest.pending_step is not None:
                raise OperationIncomplete("Step must converge before finalization", phase="finalize")
            self._write(manifest, generation, "finalizing")
        try:
            seal(manifest)
        except Exception as exc:
            raise OperationIncomplete("Run sealing is incomplete", phase="seal", cause=exc) from exc
        manifest, generation = self.read()
        execution = self._owned(manifest, run_id, owner_token, step, attempt)
        if execution.status in {"succeeded", "failed"}:
            return manifest
        if execution.status != "finalizing" or execution.terminal_status != outcome:
            raise ExecutionConflict("Finalization changed", phase="finalize")
        if manifest.pending_operation or manifest.pending_step:
            raise OperationIncomplete("Pending work must converge before release", phase="release")
        execution.status = outcome
        return self._write(manifest, generation, "release")

    def recover_stopped(self, run_id, owner_token, step, attempt, *, confirm_stopped):
        """Explicit recovery, never timeout takeover. Check outside the CAS write."""
        manifest, generation = self.read()
        execution = self._owned(manifest, run_id, owner_token, step, attempt)
        if execution.status not in {"running", "waiting"} or not confirm_stopped(execution):
            raise ExecutionConflict("Original execution is not confirmed stopped", phase="recovery")
        if manifest.pending_step and manifest.pending_step.writes_started:
            raise OperationIncomplete("Pending writes require reconciliation", phase="recovery")
        manifest.pending_step = None
        execution.status = "waiting"
        # Rotate capability so all delayed messages from the stopped worker fail.
        execution.step_attempt_token, execution.worker_token = uuid4().hex, None
        execution.terminal_status = None
        return self._write(manifest, generation, "recovery").execution

    @contextmanager
    def management_operation(self, run_id, started_at, *, seal):
        """Reuse only an actual active worker; external calls acquire a new run.

        Nested lifecycle operations cannot impersonate a running workflow by
        passing its run ID. Only the outer operation commits/releases ownership.
        """
        active = _active.get()
        if active is not None:
            active.current()
            if (active.ownership.bucket is not self.bucket
                    or active.ownership.pipeline_version != self.pipeline_version):
                raise ExecutionConflict("Active context belongs to another workspace", phase="operation")
            yield active
            return
        execution = self.acquire(run_id, started_at)
        try:
            execution = self.prepare_step(run_id, execution.owner_token, "_management")
            with self.claim(run_id, execution.owner_token, execution.current_step, execution.step_attempt_token) as context:
                try:
                    yield context
                except BaseException:
                    context.fail()
                    raise
                else:
                    context.commit()
        except BaseException:
            # Release only our capability, never a new owner's run. Pending IO
            # keeps ownership until its version-protected cleanup can finish.
            manifest, _ = self.read()
            current = self._owned(manifest, run_id, execution.owner_token)
            self.finalize(run_id, current.owner_token, current.current_step, current.step_attempt_token,
                          succeeded=False, seal=seal)
            raise
        else:
            self.finalize(run_id, execution.owner_token, execution.current_step, execution.step_attempt_token,
                          succeeded=True, seal=seal)


class AssetContext:
    def __init__(self, ownership, run_id, owner, step, attempt, worker):
        self.ownership, self.run_id, self.owner = ownership, run_id, owner
        self.step, self.attempt, self.worker = step, attempt, worker

    def current(self):
        if _active.get() is not self:
            raise ExecutionConflict("Use the active workflow context", phase="context")
        manifest, generation = self.ownership.read()
        execution = self.ownership._owned(manifest, self.run_id, self.owner, self.step, self.attempt, self.worker)
        if execution.status != "running":
            raise ExecutionConflict("Worker is no longer active", phase="context")
        return manifest, generation

    def commit(self, update=None):
        """Commit output records and step deduplication in the same manifest CAS.

        A zero-output management step also commits its identity, so redelivery
        never repeats side effects. Managed IO supplies its record update later.
        """
        manifest, generation = self.current()
        if update is not None:
            update(manifest)
        elif manifest.pending_step.writes_started:
            raise OperationIncomplete("Pending writes need a commit update", phase="commit")
        execution = manifest.execution
        execution.committed_steps.append(self.step)
        execution.status, execution.worker_token = "waiting", None
        manifest.pending_step = None
        return self.ownership._write(manifest, generation, "commit")

    def fail(self, cleanup=None):
        manifest, generation = self.current()
        if cleanup is not None:
            cleanup(manifest)
        elif manifest.pending_step.writes_started:
            raise OperationIncomplete("Pending writes need failure cleanup", phase="failure")
        manifest.pending_step = None
        manifest.execution.status = "waiting"
        manifest.execution.terminal_status = "failed"
        return self.ownership._write(manifest, generation, "failure")


def active_context(*, required=True):
    context = _active.get()
    if context is None:
        if not required:
            return None
        raise ExecutionConflict("Use the managed workflow execution entry point", phase="context")
    context.current()
    return context

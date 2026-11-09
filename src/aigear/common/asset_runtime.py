"""Execution entry point for local commands and reserved cloud attempts."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aigear.common.asset_context import RunOwnership
from aigear.db.bucket import LocalGCSMock, RealGCS
from aigear.management.asset_errors import ExecutionConflict
from aigear.management.asset_paths import local_asset_roots
from aigear.management.managed_io import commit_step, fail_step


def training_bucket(config, pipeline_version):
    settings = config.gcp.bucket
    name = settings.train_bucket.bucket_name
    if settings.on:
        return RealGCS(config.gcp.gcp_project_id, name)
    return LocalGCSMock(config.gcp.gcp_project_id, local_asset_roots(Path.cwd(), name, pipeline_version).bucket)


def seal_run(bucket, manifest):
    from aigear.management.assets import Assets
    return Assets._from_bucket(manifest.pipeline_version, bucket)._seal_run(manifest)


@contextmanager
def workflow_step(pipeline_version, step_name, config):
    bucket = training_bucket(config, pipeline_version)
    ownership = RunOwnership(bucket, pipeline_version)
    cloud = bool(os.environ.get("AIGEAR_OWNER_TOKEN") or os.environ.get("AIGEAR_STEP_ATTEMPT_TOKEN"))
    try:
        if cloud:
            expected = {"AIGEAR_PIPELINE_VERSION": pipeline_version, "AIGEAR_STEP_NAME": step_name,
                        "AIGEAR_TRAIN_BUCKET": config.gcp.bucket.train_bucket.bucket_name}
            if any(os.environ.get(key) != value for key, value in expected.items()):
                raise ExecutionConflict("Cloud attempt environment does not match the workflow", phase="claim")
            execution = ownership.read()[0].execution
            if execution is None or execution.run_id != os.environ.get("AIGEAR_RUN_ID"):
                raise ExecutionConflict("Cloud run does not match workspace", phase="claim")
            owner = os.environ.get("AIGEAR_OWNER_TOKEN")
            attempt = os.environ.get("AIGEAR_STEP_ATTEMPT_TOKEN")
            run_id = execution.run_id
            # Successful message redelivery is a no-op only for this exact owner/attempt.
            ownership._owned(ownership.read()[0], run_id, owner, step_name, attempt)
            if step_name in execution.committed_steps:
                yield False
                return
        else:
            execution = ownership.acquire(uuid4().hex, datetime.now(timezone.utc))
            execution = ownership.prepare_step(execution.run_id, execution.owner_token, step_name)
            run_id, owner, attempt = execution.run_id, execution.owner_token, execution.step_attempt_token
        success = False
        try:
            with ownership.claim(run_id, owner, step_name, attempt) as context:
                try:
                    yield True
                    commit_step(context)
                    success = True
                except BaseException:
                    fail_step(context)
                    raise
        finally:
            if not cloud and ownership.read()[0].pending_step is None:
                ownership.finalize(run_id, owner, step_name, attempt, succeeded=success,
                                   seal=lambda manifest: seal_run(bucket, manifest))
    finally:
        bucket.close()

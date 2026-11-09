"""Validate a fixed manifest's lineage without consulting a current workspace."""
from __future__ import annotations

from aigear.management.asset_errors import InvalidArtifact
from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.management.manifest import ManagedInput


def valid_step(manifest, step_name, bucket, prefix, visiting=None):
    visiting = set() if visiting is None else visiting
    step = manifest.steps.get(step_name)
    if step_name in visiting or step is None or step.status != "succeeded" or not step.outputs:
        raise InvalidArtifact("Missing, cyclic, failed or stale artifact step", phase="validate")
    visiting.add(step_name)
    try:
        for output in step.outputs:
            try:
                meta = bucket.get_metadata(f"{prefix}/{output.path}")
            except ObjectNotFound as exc:
                raise InvalidArtifact("Artifact object is missing", phase="validate", cause=exc) from exc
            if (meta.generation, meta.size, meta.crc32c) != (output.generation, output.size, output.crc32c):
                raise InvalidArtifact("Artifact content/version changed", phase="validate")
        for source in step.inputs:
            if not isinstance(source, ManagedInput):
                continue
            producer = valid_step(manifest, source.step_name, bucket, prefix, visiting)
            output = next((o for o in producer.outputs if o.path == source.artifact), None)
            # Snapshot copies have different keys/generations. Validate recorded
            # production identity against its own fixed members, not running/.
            if (producer.producer_run_id, producer.producer_step_revision) != (source.run_id, source.step_revision) or output is None:
                raise InvalidArtifact("Managed input production changed", phase="validate")
            if manifest.manifest_type == "workspace" and source.observed_object.generation != output.generation:
                raise InvalidArtifact("Managed input version changed", phase="validate")
        return step
    finally:
        visiting.remove(step_name)


def invalidate_downstream(manifest, step_name):
    affected = {step_name}
    while True:
        downstream = {name for name, step in manifest.steps.items()
                      if any(isinstance(source, ManagedInput) and source.step_name in affected for source in step.inputs)}
        if downstream <= affected:
            break
        affected.update(downstream)
    for name in affected:
        if name in manifest.steps:
            manifest.steps[name].status = "stale"

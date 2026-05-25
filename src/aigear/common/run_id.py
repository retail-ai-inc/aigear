from __future__ import annotations

import hashlib


def derive_run_id(
    project_name: str | None,
    pipeline_version: str,
    run_started_at_utc: str,
) -> str:
    """
    Build a stable short run identifier.

    The hash input intentionally uses a nullable project_name to keep compatibility
    with legacy messages that don't populate this field.
    """
    normalized_project_name = project_name or ""
    source = f"{normalized_project_name}:{pipeline_version}:{run_started_at_utc}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

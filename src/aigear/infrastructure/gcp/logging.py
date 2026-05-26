from __future__ import annotations

import json
from typing import Any

from aigear.common import run_sh


def read_logs(filter_expr: str, project_id: str, limit: int) -> list[dict[str, Any]]:
    """Read Cloud Logging entries via gcloud."""
    output = run_sh(
        [
            "gcloud",
            "logging",
            "read",
            filter_expr,
            "--project",
            project_id,
            "--limit",
            str(limit),
            "--format=json",
        ]
    )
    if not output.strip():
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []

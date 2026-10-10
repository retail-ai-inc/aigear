from pathlib import Path
import tempfile

import pytest


def pytest_configure(config):
    if config.option.basetemp is not None:
        return

    temp_root = config.rootpath / ".pytest_cache" / "tmp"
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        session_temp = tempfile.TemporaryDirectory(prefix="run-", dir=temp_root)
    except OSError as exc:
        raise pytest.UsageError(
            f"Cannot create test temporary files under {temp_root}. "
            "Use --basetemp with a writable directory."
        ) from exc

    config.option.basetemp = str(Path(session_temp.name) / "fixtures")
    config.add_cleanup(session_temp.cleanup)

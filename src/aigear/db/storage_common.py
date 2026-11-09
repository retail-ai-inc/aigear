"""Shared validation, diagnostics and atomic local file replacement."""
from __future__ import annotations

import base64
import logging
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import google_crc32c
from google.api_core import exceptions as gcs_errors
from google.cloud.storage.exceptions import DataCorruption

from aigear.common.schema.bucket_schema import (
    ObjectNotFound, PreconditionFailed, StorageError, StoragePermissionDenied,
    VersionMismatch,
)
from aigear.management.asset_errors import IntegrityError

logger = logging.getLogger(__name__)


def version(value, *, optional=True, zero=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Expected a decimal generation")
    value = str(value)
    if not value.isascii() or not value.isdecimal() or int(value) < (0 if zero else 1):
        raise ValueError("Expected a positive generation (or 0 for create only)")
    return str(int(value))


def utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Custom-Time must include a timezone")
    return value.astimezone(timezone.utc)


def crc32c(data):
    return base64.b64encode(google_crc32c.value(data).to_bytes(4, "big")).decode("ascii")


def check_version(actual, expected, *, fixed=False):
    if expected is not None and actual != expected:
        cause = ValueError("Object version differs from the expected version")
        error = VersionMismatch if fixed else PreconditionFailed
        raise error(str(cause), phase="condition", cause=cause) from cause


@contextmanager
def storage_call(phase):
    try:
        yield
    except StorageError:
        raise
    except (gcs_errors.GoogleAPICallError, OSError, DataCorruption, sqlite3.Error) as exc:
        if isinstance(exc, (gcs_errors.NotFound, FileNotFoundError)):
            kind = ObjectNotFound
        elif isinstance(exc, (gcs_errors.Forbidden, gcs_errors.Unauthorized, PermissionError)):
            kind = StoragePermissionDenied
        elif isinstance(exc, gcs_errors.PreconditionFailed):
            kind = PreconditionFailed
        elif isinstance(exc, DataCorruption):
            kind = IntegrityError
        else:
            kind = StorageError
        # Do not log SDK exception strings, which can contain signed URLs.
        logger.warning("Storage operation failed", extra={"phase": phase, "result": kind.__name__})
        raise kind("Storage operation failed: " + phase, phase=phase, cause=exc) from exc


def atomic_file(path, data):
    """Flush a temporary sibling before replacement; failures preserve the old file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".aigear-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if getattr(exc, 'winerror', None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

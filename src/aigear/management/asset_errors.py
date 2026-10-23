"""Domain failures shared by managed IO and asset lifecycle operations."""

from __future__ import annotations

from typing import Any, Optional


class AssetError(Exception):
    """A failed operation may still have durable progress to resume.

    Messages and partial results supplied by callers must contain only sanitized
    diagnostics; the underlying exception remains available through ``cause``.
    """

    def __init__(
        self,
        message: str,
        *,
        operation_id: Optional[str] = None,
        phase: Optional[str] = None,
        cause: Optional[BaseException] = None,
        partial_result: Any = None,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.phase = phase
        self.cause = cause
        self.partial_result = partial_result
        if cause is not None:
            self.__cause__ = cause


class InvalidAssetPath(AssetError, ValueError):
    """An identifier or path cannot safely name a managed object."""


class ExecutionConflict(AssetError):
    """A workspace or release target is owned by another execution."""


class InvalidArtifact(AssetError):
    """An artifact is not eligible for reading, registration or publication."""


class IntegrityError(AssetError):
    """A manifest, object version or checksum does not match its contract."""


class SnapshotConflict(AssetError):
    """Existing snapshot membership or production identity has changed."""


class OperationIncomplete(AssetError):
    """A durable operation requires reconciliation before further mutation."""


class SnapshotNotFound(AssetError):
    """No snapshot with the requested version and run identity exists."""


class BackupUnavailable(AssetError):
    """No complete committed release backup is available."""

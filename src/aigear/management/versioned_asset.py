from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aigear.common.config import get_project_name
from aigear.common.run_log_context import RunLogContext
from aigear.db.bucket import LocalGCSMock, bucket_client
from aigear.management.registry import (
    AssetRef,
    AssetRecord,
    AssetRegistry,
    AssetType,
    FirestoreAssetRegistry,
)


class VersionedAssetManagement:
    def __init__(
        self,
        pipeline_version: str,
        project_name: str | None = None,
        project_id: str | None = None,
        bucket_name: str | None = None,
        bucket_on: bool = True,
        registry: AssetRegistry | None = None,
        bucket=None,
        local_asset_path: Path | None = None,
        clock=None,
    ):
        self.pipeline_version = pipeline_version
        self.project_name = project_name or get_project_name() or ""
        self.project_id = project_id
        self.bucket_name = bucket_name
        self.project_dir = Path.cwd()
        self.local_asset_path = local_asset_path or self.project_dir / "asset"
        self.bucket_client = bucket or self._create_bucket_client(
            project_id=project_id,
            bucket_name=bucket_name,
            bucket_on=bucket_on,
        )
        self.registry = registry or FirestoreAssetRegistry(
            project_name=self.project_name,
            pipeline_version=pipeline_version,
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def upload_version(
        self,
        file_name: str,
        asset_type: AssetType,
        asset_name: str | None = None,
        version: str | None = None,
        inputs: list[AssetRef] | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        step_name: str | None = None,
    ) -> AssetRecord:
        self._validate_asset_type(asset_type)
        resolved = self._resolve_context(run_id=run_id, step_name=step_name)
        resolved_asset_name = asset_name or self._default_asset_name(asset_type)
        local_path = self._resolve_local_file(
            file_name,
            asset_type=asset_type,
            asset_name=resolved_asset_name,
        )
        resolved_version = version or self._hash_file(local_path)
        blob_path = self.get_run_asset_path(
            resolved["run_id"],
            asset_type,
            resolved_asset_name,
            local_path.name,
        )

        self._ensure_run(resolved)
        self.bucket_client.upload(local_path, blob_path)
        record = AssetRecord(
            asset_type=asset_type,
            name=resolved_asset_name,
            version=resolved_version,
            uri=self._metadata_uri(blob_path),
            file_name=local_path.name,
            run_id=resolved["run_id"],
            step_name=resolved["step_name"],
            pipeline_version=resolved["pipeline_version"],
            project_name=resolved["project_name"],
            created_at_utc=resolved["created_at_utc"],
            created_by=resolved["created_by"],
            inputs=inputs or [],
            metadata=metadata or {},
            status="active",
        )
        return self.registry.register(record)

    def download_version(
        self,
        asset_type: AssetType,
        asset_name: str | None = None,
        version: str | None = None,
        file_name: str | None = None,
    ) -> Path:
        self._validate_asset_type(asset_type)
        resolved_asset_name = asset_name or self._default_asset_name(asset_type)
        record = (
            self.registry.get(asset_type, resolved_asset_name, version)
            if version
            else self.registry.latest(asset_type, resolved_asset_name)
        )
        if record is None:
            label = version or "latest"
            raise FileNotFoundError(
                f"Asset not found: {asset_type}/{resolved_asset_name}/{label}"
            )

        target_name = file_name or record.file_name or Path(record.uri).name
        local_path = (
            self.local_asset_path
            / self.pipeline_version
            / asset_type
            / resolved_asset_name
            / target_name
        )
        self.bucket_client.download(self._blob_from_uri(record.uri), local_path)
        return local_path

    def register_external(
        self,
        asset_type: AssetType,
        asset_name: str,
        uri: str,
        version: str | None = None,
        file_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        step_name: str | None = None,
    ) -> AssetRecord:
        self._validate_asset_type(asset_type)
        resolved = self._resolve_context(run_id=run_id, step_name=step_name)
        resolved_version = version or hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
        self._ensure_run(resolved)
        record = AssetRecord(
            asset_type=asset_type,
            name=asset_name,
            version=resolved_version,
            uri=uri,
            file_name=file_name,
            run_id=resolved["run_id"],
            step_name=resolved["step_name"],
            pipeline_version=resolved["pipeline_version"],
            project_name=resolved["project_name"],
            created_at_utc=resolved["created_at_utc"],
            created_by=resolved["created_by"],
            metadata=metadata or {},
            status="active",
        )
        return self.registry.register(record)

    def get_run_asset_path(
        self,
        run_id: str,
        asset_type: AssetType,
        asset_name: str,
        file_name: str,
    ) -> str:
        self._validate_asset_type(asset_type)
        return (
            f"_aigear_runs/{self.project_name}/{self.pipeline_version}/"
            f"{run_id}/{asset_type}/{asset_name}/{file_name}"
        )

    def get_local_path(
        self,
        asset_type: AssetType,
        asset_name: str,
        file_name: str,
    ) -> Path:
        self._validate_asset_type(asset_type)
        path = (
            self.local_asset_path
            / self.pipeline_version
            / asset_type
            / asset_name
            / file_name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _create_bucket_client(
        self,
        project_id: str | None,
        bucket_name: str | None,
        bucket_on: bool,
    ):
        if bucket_on:
            return bucket_client(project_id, bucket_name, bucket_on=True)
        local_bucket_name = bucket_name or "gcs_mock"
        return LocalGCSMock(project_id, self.local_asset_path / local_bucket_name)

    def _resolve_context(
        self,
        run_id: str | None = None,
        step_name: str | None = None,
    ) -> dict[str, Any]:
        now = self.clock()
        created_at_utc = self._format_utc(now)
        ctx = RunLogContext.current()
        if ctx is not None:
            return {
                "run_id": ctx.run_id,
                "step_name": step_name or ctx.step_name,
                "pipeline_version": ctx.pipeline_version or self.pipeline_version,
                "project_name": ctx.project_name or self.project_name,
                "created_at_utc": created_at_utc,
                "created_by": "auto",
                "triggered_by": "scheduler",
                "started_at_utc": ctx.run_started_at_utc,
            }

        resolved_run_id = run_id or f"manual-{now.strftime('%Y%m%d%H%M%S')}"
        return {
            "run_id": resolved_run_id,
            "step_name": step_name,
            "pipeline_version": self.pipeline_version,
            "project_name": self.project_name,
            "created_at_utc": created_at_utc,
            "created_by": "manual",
            "triggered_by": "manual",
            "started_at_utc": created_at_utc,
        }

    def _ensure_run(self, resolved: dict[str, Any]) -> None:
        self.registry.ensure_run(
            resolved["run_id"],
            {
                "run_id": resolved["run_id"],
                "pipeline_version": resolved["pipeline_version"],
                "project_name": resolved["project_name"],
                "status": "running",
                "started_at_utc": resolved["started_at_utc"],
                "finished_at_utc": None,
                "triggered_by": resolved["triggered_by"],
            },
        )

    def _default_asset_name(self, asset_type: AssetType) -> str:
        if asset_type == "model":
            return self.pipeline_version
        raise ValueError(f"asset_name is required for asset_type={asset_type}")

    def _resolve_local_file(
        self,
        file_name: str,
        asset_type: AssetType,
        asset_name: str,
    ) -> Path:
        local_path = Path(file_name)
        if local_path.is_absolute():
            if local_path.exists():
                return local_path
            raise FileNotFoundError(f"Local asset file not found: {local_path}")

        cwd_path = Path.cwd() / local_path
        if cwd_path.exists():
            return cwd_path

        managed_path = self.get_local_path(asset_type, asset_name, file_name)
        if managed_path.exists():
            return managed_path

        raise FileNotFoundError(
            f"Local asset file not found: {cwd_path} or {managed_path}"
        )

    def _metadata_uri(self, blob_path: str) -> str:
        if self.bucket_name:
            return f"gs://{self.bucket_name}/{blob_path}"
        return blob_path

    def _blob_from_uri(self, uri: str) -> str:
        if uri.startswith("gs://"):
            parts = uri.split("/", 3)
            return parts[3] if len(parts) == 4 else ""
        return uri

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:16]

    @staticmethod
    def _format_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _validate_asset_type(asset_type: AssetType) -> None:
        if asset_type == "training":
            raise ValueError('Use asset_type="model" for versioned model assets.')
        if asset_type not in {"dataset", "feature", "model", "service"}:
            raise ValueError(f"Unsupported asset_type: {asset_type}")

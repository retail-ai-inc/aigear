from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

AssetStatus = Literal["pending", "active", "failed", "archived", "deleted"]
AssetType = Literal["dataset", "feature", "model", "service"]


@dataclass(frozen=True)
class AssetRef:
    asset_type: AssetType
    name: str
    version: str

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "AssetRef":
        return cls(
            asset_type=value["asset_type"],  # type: ignore[arg-type]
            name=value["name"],
            version=value["version"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "asset_type": self.asset_type,
            "name": self.name,
            "version": self.version,
        }


@dataclass
class AssetRecord:
    asset_type: AssetType
    name: str
    version: str
    uri: str
    file_name: str | None = None
    run_id: str | None = None
    step_name: str | None = None
    pipeline_version: str | None = None
    project_name: str | None = None
    created_at_utc: str | None = None
    created_by: Literal["auto", "manual"] | None = None
    inputs: list[AssetRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: AssetStatus = "active"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AssetRecord":
        inputs = [
            item if isinstance(item, AssetRef) else AssetRef.from_dict(item)
            for item in value.get("inputs", [])
        ]
        return cls(
            asset_type=value["asset_type"],
            name=value["name"],
            version=value["version"],
            uri=value["uri"],
            file_name=value.get("file_name"),
            run_id=value.get("run_id"),
            step_name=value.get("step_name"),
            pipeline_version=value.get("pipeline_version"),
            project_name=value.get("project_name"),
            created_at_utc=value.get("created_at_utc"),
            created_by=value.get("created_by"),
            inputs=inputs,
            metadata=dict(value.get("metadata", {})),
            status=value.get("status", "active"),
        )

    @property
    def ref(self) -> AssetRef:
        return AssetRef(
            asset_type=self.asset_type,
            name=self.name,
            version=self.version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_type": self.asset_type,
            "name": self.name,
            "version": self.version,
            "uri": self.uri,
            "file_name": self.file_name,
            "run_id": self.run_id,
            "step_name": self.step_name,
            "pipeline_version": self.pipeline_version,
            "project_name": self.project_name,
            "created_at_utc": self.created_at_utc,
            "created_by": self.created_by,
            "inputs": [item.to_dict() for item in self.inputs],
            "metadata": self.metadata,
            "status": self.status,
        }


class AssetRegistry(Protocol):
    def register(self, record: AssetRecord) -> AssetRecord:
        ...

    def get(self, asset_type: AssetType, name: str, version: str) -> AssetRecord | None:
        ...

    def latest(self, asset_type: AssetType, name: str | None = None) -> AssetRecord | None:
        ...

    def list(
        self,
        asset_type: AssetType | None = None,
        name: str | None = None,
        run_id: str | None = None,
        step_name: str | None = None,
        status: AssetStatus | None = None,
    ) -> list[AssetRecord]:
        ...

    def lineage(self, asset_type: AssetType, name: str, version: str) -> list[AssetRecord]:
        ...

    def set_alias(
        self,
        asset_type: AssetType,
        name: str,
        alias: str,
        version: str,
    ) -> None:
        ...

    def get_alias(self, asset_type: AssetType, name: str, alias: str) -> str | None:
        ...


class FakeAssetRegistry:
    def __init__(self) -> None:
        self._records: dict[tuple[AssetType, str, str], AssetRecord] = {}
        self._aliases: dict[tuple[AssetType, str], dict[str, str]] = {}

    def register(self, record: AssetRecord) -> AssetRecord:
        self._records[self._record_key(record.asset_type, record.name, record.version)] = record
        return record

    def get(self, asset_type: AssetType, name: str, version: str) -> AssetRecord | None:
        return self._records.get(self._record_key(asset_type, name, version))

    def latest(self, asset_type: AssetType, name: str | None = None) -> AssetRecord | None:
        candidates = self.list(asset_type=asset_type, name=name, status="active")
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda record: (
                record.created_at_utc or "",
                record.version,
            ),
        )

    def list(
        self,
        asset_type: AssetType | None = None,
        name: str | None = None,
        run_id: str | None = None,
        step_name: str | None = None,
        status: AssetStatus | None = None,
    ) -> list[AssetRecord]:
        records = list(self._records.values())
        if asset_type is not None:
            records = [record for record in records if record.asset_type == asset_type]
        if name is not None:
            records = [record for record in records if record.name == name]
        if run_id is not None:
            records = [record for record in records if record.run_id == run_id]
        if step_name is not None:
            records = [record for record in records if record.step_name == step_name]
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(
            records,
            key=lambda record: (
                record.asset_type,
                record.name,
                record.created_at_utc or "",
                record.version,
            ),
        )

    def lineage(self, asset_type: AssetType, name: str, version: str) -> list[AssetRecord]:
        result: list[AssetRecord] = []
        visited: set[tuple[AssetType, str, str]] = set()

        def visit(ref: AssetRef) -> None:
            key = self._record_key(ref.asset_type, ref.name, ref.version)
            if key in visited:
                return
            record = self._records.get(key)
            if record is None:
                return
            visited.add(key)
            for input_ref in record.inputs:
                visit(input_ref)
            result.append(record)

        visit(AssetRef(asset_type=asset_type, name=name, version=version))
        return result

    def set_alias(
        self,
        asset_type: AssetType,
        name: str,
        alias: str,
        version: str,
    ) -> None:
        aliases = self._aliases.setdefault((asset_type, name), {})
        if alias == "champion":
            current = aliases.get("champion")
            if current and current != version:
                aliases["previous"] = current
        aliases[alias] = version

    def get_alias(self, asset_type: AssetType, name: str, alias: str) -> str | None:
        return self._aliases.get((asset_type, name), {}).get(alias)

    @staticmethod
    def _record_key(
        asset_type: AssetType,
        name: str,
        version: str,
    ) -> tuple[AssetType, str, str]:
        return (asset_type, name, version)


class FirestoreAssetRegistry:
    def __init__(
        self,
        project_name: str,
        pipeline_version: str,
        client: Any | None = None,
        server_timestamp: Any | None = None,
    ) -> None:
        self.project_name = project_name
        self.pipeline_version = pipeline_version
        if client is None or server_timestamp is None:
            from google.cloud import firestore

            if client is None:
                client = firestore.Client()
            if server_timestamp is None:
                server_timestamp = firestore.SERVER_TIMESTAMP
        self.client = client
        self.server_timestamp = server_timestamp

    def register(self, record: AssetRecord) -> AssetRecord:
        data = record.to_dict()
        data["project_name"] = record.project_name or self.project_name
        data["pipeline_version"] = record.pipeline_version or self.pipeline_version
        data["created_at"] = self._server_timestamp()
        data["created_at_utc"] = record.created_at_utc
        self._asset_doc(record.asset_type, record.name, record.version).set(data)
        return AssetRecord.from_dict(data)

    def get(self, asset_type: AssetType, name: str, version: str) -> AssetRecord | None:
        snapshot = self._asset_doc(asset_type, name, version).get()
        if not getattr(snapshot, "exists", False):
            return None
        return self._snapshot_to_record(snapshot)

    def latest(self, asset_type: AssetType, name: str | None = None) -> AssetRecord | None:
        records = self.list(asset_type=asset_type, name=name, status="active", limit=1)
        return records[0] if records else None

    def list(
        self,
        asset_type: AssetType | None = None,
        name: str | None = None,
        run_id: str | None = None,
        step_name: str | None = None,
        status: AssetStatus | None = None,
        limit: int | None = None,
    ) -> list[AssetRecord]:
        query = self._assets_collection()
        for field_name, value in [
            ("asset_type", asset_type),
            ("name", name),
            ("run_id", run_id),
            ("step_name", step_name),
            ("status", status),
        ]:
            if value is not None:
                query = query.where(field_name, "==", value)
        query = query.order_by("created_at", direction="DESCENDING")
        if limit is not None:
            query = query.limit(limit)
        return [self._snapshot_to_record(snapshot) for snapshot in query.stream()]

    def lineage(self, asset_type: AssetType, name: str, version: str) -> list[AssetRecord]:
        result: list[AssetRecord] = []
        visited: set[tuple[AssetType, str, str]] = set()

        def visit(ref: AssetRef) -> None:
            key = (ref.asset_type, ref.name, ref.version)
            if key in visited:
                return
            record = self.get(ref.asset_type, ref.name, ref.version)
            if record is None:
                return
            visited.add(key)
            for input_ref in record.inputs:
                visit(input_ref)
            result.append(record)

        visit(AssetRef(asset_type=asset_type, name=name, version=version))
        return result

    def set_alias(
        self,
        asset_type: AssetType,
        name: str,
        alias: str,
        version: str,
    ) -> None:
        doc = self._alias_doc(asset_type, name)
        payload: dict[str, Any] = {alias: version, "updated_at": self._server_timestamp()}
        if alias == "champion":
            snapshot = doc.get()
            if getattr(snapshot, "exists", False):
                current = snapshot.to_dict().get("champion")
                if current and current != version:
                    payload["previous"] = current
        doc.set(payload, merge=True)

    def get_alias(self, asset_type: AssetType, name: str, alias: str) -> str | None:
        snapshot = self._alias_doc(asset_type, name).get()
        if not getattr(snapshot, "exists", False):
            return None
        return snapshot.to_dict().get(alias)

    def _asset_doc(self, asset_type: AssetType, name: str, version: str) -> Any:
        return self._assets_collection().document(
            self.asset_doc_id(asset_type, name, version)
        )

    def _alias_doc(self, asset_type: AssetType, name: str) -> Any:
        return self._aliases_collection().document(self.alias_doc_id(asset_type, name))

    def _assets_collection(self) -> Any:
        return self._pipeline_doc().collection("assets")

    def _aliases_collection(self) -> Any:
        return self._pipeline_doc().collection("aliases")

    def _pipeline_doc(self) -> Any:
        return (
            self.client.collection("aigear_projects")
            .document(self.project_name)
            .collection("pipelines")
            .document(self.pipeline_version)
        )

    def _server_timestamp(self) -> Any:
        if callable(self.server_timestamp):
            return self.server_timestamp()
        return self.server_timestamp

    @staticmethod
    def asset_doc_id(asset_type: AssetType, name: str, version: str) -> str:
        return f"{asset_type}__{name}__{version}"

    @staticmethod
    def alias_doc_id(asset_type: AssetType, name: str) -> str:
        return f"{asset_type}__{name}"

    @staticmethod
    def _snapshot_to_record(snapshot: Any) -> AssetRecord:
        return AssetRecord.from_dict(snapshot.to_dict())

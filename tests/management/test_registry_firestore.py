from __future__ import annotations

from typing import Any

from aigear.management.registry import AssetRecord, FirestoreAssetRegistry


class FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None):
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data or {})


class FakeDocument:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self.data: dict[str, Any] | None = None
        self.children: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> "FakeCollection":
        return self.children.setdefault(name, FakeCollection(name))

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        if merge and self.data:
            self.data.update(data)
        else:
            self.data = dict(data)

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self.data)


class FakeCollection:
    def __init__(self, name: str):
        self.name = name
        self.docs: dict[str, FakeDocument] = {}

    def document(self, doc_id: str) -> FakeDocument:
        return self.docs.setdefault(doc_id, FakeDocument(doc_id))

    def where(self, field_name: str, op: str, value: Any) -> "FakeQuery":
        return FakeQuery(list(self.docs.values())).where(field_name, op, value)

    def order_by(self, field_name: str, direction: str = "ASCENDING") -> "FakeQuery":
        return FakeQuery(list(self.docs.values())).order_by(field_name, direction)

    def stream(self) -> list[FakeSnapshot]:
        return [doc.get() for doc in self.docs.values() if doc.data is not None]


class FakeQuery:
    def __init__(self, docs: list[FakeDocument]):
        self.docs = docs

    def where(self, field_name: str, op: str, value: Any) -> "FakeQuery":
        assert op == "=="
        self.docs = [
            doc for doc in self.docs if doc.data and doc.data.get(field_name) == value
        ]
        return self

    def order_by(self, field_name: str, direction: str = "ASCENDING") -> "FakeQuery":
        reverse = direction == "DESCENDING"
        self.docs = sorted(
            self.docs,
            key=lambda doc: doc.data.get(field_name) if doc.data else None,
            reverse=reverse,
        )
        return self

    def limit(self, count: int) -> "FakeQuery":
        self.docs = self.docs[:count]
        return self

    def stream(self) -> list[FakeSnapshot]:
        return [doc.get() for doc in self.docs if doc.data is not None]


class FakeFirestoreClient:
    def __init__(self):
        self.collections: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))


def _timestamp_counter():
    value = 0

    def next_value():
        nonlocal value
        value += 1
        return value

    return next_value


def _registry() -> FirestoreAssetRegistry:
    return FirestoreAssetRegistry(
        project_name="aigear_sklearn_pipeline",
        pipeline_version="logistic_regression",
        client=FakeFirestoreClient(),
        server_timestamp=_timestamp_counter(),
    )


def test_firestore_registry_registers_asset_with_expected_doc_id():
    registry = _registry()
    record = AssetRecord(
        asset_type="model",
        name="logistic_regression",
        version="model-v1",
        uri="gs://bucket/model.pkl",
        created_at_utc="2026-06-29T00:00:00Z",
    )

    registered = registry.register(record)

    assert registry.get("model", "logistic_regression", "model-v1") == registered
    assert (
        FirestoreAssetRegistry.asset_doc_id("model", "logistic_regression", "model-v1")
        == "model__logistic_regression__model-v1"
    )


def test_firestore_registry_latest_queries_active_records_by_created_at():
    registry = _registry()
    registry.register(
        AssetRecord(
            asset_type="model",
            name="logistic_regression",
            version="model-v1",
            uri="gs://bucket/model-v1.pkl",
            created_at_utc="2026-06-01T00:00:00Z",
            status="active",
        )
    )
    latest = registry.register(
        AssetRecord(
            asset_type="model",
            name="logistic_regression",
            version="model-v2",
            uri="gs://bucket/model-v2.pkl",
            created_at_utc="2026-06-02T00:00:00Z",
            status="active",
        )
    )
    registry.register(
        AssetRecord(
            asset_type="model",
            name="logistic_regression",
            version="model-v3",
            uri="gs://bucket/model-v3.pkl",
            created_at_utc="2026-06-03T00:00:00Z",
            status="failed",
        )
    )

    assert registry.latest("model", "logistic_regression") == latest


def test_firestore_registry_lineage_uses_record_inputs():
    registry = _registry()
    dataset = registry.register(
        AssetRecord(
            asset_type="dataset",
            name="breast_cancer",
            version="dataset-v1",
            uri="gs://bucket/dataset.pkl",
        )
    )
    feature = registry.register(
        AssetRecord(
            asset_type="feature",
            name="training_features",
            version="feature-v1",
            uri="gs://bucket/feature.pkl",
            inputs=[dataset.ref],
        )
    )
    model = registry.register(
        AssetRecord(
            asset_type="model",
            name="logistic_regression",
            version="model-v1",
            uri="gs://bucket/model.pkl",
            inputs=[feature.ref],
        )
    )

    assert registry.lineage("model", model.name, model.version) == [
        dataset,
        feature,
        model,
    ]


def test_firestore_registry_alias_tracks_champion_and_previous():
    registry = _registry()

    registry.set_alias("service", "svc", "champion", "service-v1")
    registry.set_alias("service", "svc", "champion", "service-v2")

    assert registry.get_alias("service", "svc", "champion") == "service-v2"
    assert registry.get_alias("service", "svc", "previous") == "service-v1"

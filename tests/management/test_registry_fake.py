from aigear.management.registry import AssetRecord, AssetRef, FakeAssetRegistry


def test_fake_registry_register_get_and_list():
    registry = FakeAssetRegistry()
    record = AssetRecord(
        asset_type="dataset",
        name="breast_cancer",
        version="dataset-v1",
        uri="gs://bucket/dataset.pkl",
        run_id="run-1",
        step_name="fetch_data",
    )

    registry.register(record)

    assert registry.get("dataset", "breast_cancer", "dataset-v1") == record
    assert registry.list(asset_type="dataset") == [record]
    assert registry.list(run_id="run-1") == [record]
    assert registry.list(step_name="fetch_data") == [record]


def test_fake_registry_latest_returns_newest_active_record_only():
    registry = FakeAssetRegistry()
    failed = AssetRecord(
        asset_type="model",
        name="logistic_regression",
        version="model-v3",
        uri="gs://bucket/model-v3.pkl",
        created_at_utc="2026-06-03T00:00:00Z",
        status="failed",
    )
    archived = AssetRecord(
        asset_type="model",
        name="logistic_regression",
        version="model-v2",
        uri="gs://bucket/model-v2.pkl",
        created_at_utc="2026-06-02T00:00:00Z",
        status="archived",
    )
    active = AssetRecord(
        asset_type="model",
        name="logistic_regression",
        version="model-v1",
        uri="gs://bucket/model-v1.pkl",
        created_at_utc="2026-06-01T00:00:00Z",
        status="active",
    )
    for record in [failed, archived, active]:
        registry.register(record)

    assert registry.latest("model", "logistic_regression") == active


def test_fake_registry_lineage_returns_dependencies_before_target():
    registry = FakeAssetRegistry()
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
            uri="gs://bucket/features.pkl",
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
    service = registry.register(
        AssetRecord(
            asset_type="service",
            name="logistic-regression-service",
            version="service-v1",
            uri="gs://bucket/deployment.yaml",
            inputs=[model.ref],
        )
    )

    assert registry.lineage("service", service.name, service.version) == [
        dataset,
        feature,
        model,
        service,
    ]


def test_fake_registry_alias_tracks_champion_and_previous():
    registry = FakeAssetRegistry()

    registry.set_alias("service", "svc", "champion", "service-v1")
    registry.set_alias("service", "svc", "champion", "service-v2")

    assert registry.get_alias("service", "svc", "champion") == "service-v2"
    assert registry.get_alias("service", "svc", "previous") == "service-v1"


def test_asset_record_round_trips_to_dict():
    record = AssetRecord(
        asset_type="model",
        name="logistic_regression",
        version="model-v1",
        uri="gs://bucket/model.pkl",
        inputs=[AssetRef("feature", "training_features", "feature-v1")],
        metadata={"accuracy": 0.94},
    )

    assert AssetRecord.from_dict(record.to_dict()) == record

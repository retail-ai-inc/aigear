from unittest.mock import patch

import pytest

from aigear.infrastructure.gcp.registry_v2_bootstrap import (
    FirestoreCompositeIndex,
    RegistryV2FirestoreIndexes,
    RegistryV2GcsIam,
    RegistryV2TtlPolicies,
)
from aigear.management.v2.gcs_layout import GcsLayoutV2

_MODULE = "aigear.infrastructure.gcp.registry_v2_bootstrap"


# ── FirestoreCompositeIndex ────────────────────────────────────────────────────


def test_composite_index_requires_non_empty_collection_group():
    with pytest.raises(ValueError):
        FirestoreCompositeIndex(collection_group="", fields=(("a", "ASCENDING"),))


def test_composite_index_requires_at_least_one_field():
    with pytest.raises(ValueError):
        FirestoreCompositeIndex(collection_group="blobs", fields=())


def test_composite_index_rejects_invalid_order():
    with pytest.raises(ValueError):
        FirestoreCompositeIndex(collection_group="blobs", fields=(("a", "SIDEWAYS"),))


def test_composite_index_rejects_empty_field_path():
    with pytest.raises(ValueError):
        FirestoreCompositeIndex(collection_group="blobs", fields=(("", "ASCENDING"),))


# ── RegistryV2FirestoreIndexes ─────────────────────────────────────────────────


def test_default_index_definitions_cover_blobs_labels_occurrences_runs():
    groups = {i.collection_group for i in RegistryV2FirestoreIndexes.default_index_definitions()}
    assert groups == {
        "asset_versions",
        "blobs",
        "labels",
        "occurrences",
        "outbox",
        "runs",
    }
    outbox_fields = [
        tuple(field for field, _order in index.fields)
        for index in RegistryV2FirestoreIndexes.default_index_definitions()
        if index.collection_group == "outbox"
    ]
    assert ("status", "next_attempt_at", "created_at", "event_id") in outbox_fields
    assert ("status", "lease_expires_at", "created_at", "event_id") in outbox_fields


@patch(f"{_MODULE}.run_sh")
def test_create_builds_expected_command(mock_run_sh):
    indexer = RegistryV2FirestoreIndexes(project_id="my-project", database_id="my-db")
    index = FirestoreCompositeIndex(
        collection_group="blobs",
        fields=(("availability_state", "ASCENDING"), ("created_at", "DESCENDING")),
    )
    indexer.create(index)
    cmd = mock_run_sh.call_args[0][0]
    assert cmd[:5] == ["gcloud", "firestore", "indexes", "composite", "create"]
    assert "--collection-group=blobs" in cmd
    assert "--database=my-db" in cmd
    assert "--project=my-project" in cmd
    assert "--field-config=field-path=availability_state,order=ASCENDING" in cmd
    assert "--field-config=field-path=created_at,order=DESCENDING" in cmd
    assert mock_run_sh.call_args.kwargs.get("check") is True


@patch(f"{_MODULE}.run_sh")
def test_create_all_creates_every_default_index(mock_run_sh):
    indexer = RegistryV2FirestoreIndexes(project_id="my-project", database_id="my-db")
    indexer.create_all()
    assert mock_run_sh.call_count == len(RegistryV2FirestoreIndexes.default_index_definitions())


@patch(f"{_MODULE}.run_sh")
def test_create_all_accepts_custom_index_list(mock_run_sh):
    indexer = RegistryV2FirestoreIndexes(project_id="my-project", database_id="my-db")
    custom = [FirestoreCompositeIndex(collection_group="blobs", fields=(("a", "ASCENDING"),))]
    indexer.create_all(custom)
    assert mock_run_sh.call_count == 1
    assert "--collection-group=blobs" in mock_run_sh.call_args[0][0]


# ── RegistryV2TtlPolicies ───────────────────────────────────────────────────────


def test_default_ttl_fields_never_delete_correctness_fences():
    assert RegistryV2TtlPolicies.DEFAULT_TTL_FIELDS == ()
    assert RegistryV2TtlPolicies.FORBIDDEN_TTL_COLLECTIONS == {
        "blob_claims",
        "operations",
        "tombstones",
    }


@patch(f"{_MODULE}.run_sh")
def test_enable_builds_expected_command(mock_run_sh):
    ttl = RegistryV2TtlPolicies(project_id="my-project", database_id="my-db")
    ttl.enable("projection_delivery_receipts", "expires_at")
    cmd = mock_run_sh.call_args[0][0]
    assert cmd[:5] == ["gcloud", "firestore", "fields", "ttls", "update"]
    assert "expires_at" in cmd
    assert "--collection-group=projection_delivery_receipts" in cmd
    assert "--database=my-db" in cmd
    assert "--project=my-project" in cmd
    assert "--enable-ttl" in cmd
    assert mock_run_sh.call_args.kwargs.get("check") is True


@patch(f"{_MODULE}.run_sh")
def test_enable_all_enables_every_default_ttl_field(mock_run_sh):
    ttl = RegistryV2TtlPolicies(project_id="my-project", database_id="my-db")
    ttl.enable_all()
    assert mock_run_sh.call_count == len(RegistryV2TtlPolicies.DEFAULT_TTL_FIELDS)


@patch(f"{_MODULE}.run_sh")
def test_enable_all_accepts_custom_fields(mock_run_sh):
    ttl = RegistryV2TtlPolicies(project_id="my-project", database_id="my-db")
    ttl.enable_all([("projection_delivery_receipts", "expires_at")])
    assert mock_run_sh.call_count == 1
    assert "--collection-group=projection_delivery_receipts" in mock_run_sh.call_args[0][0]


@pytest.mark.parametrize("collection_group", ["blob_claims", "operations", "tombstones"])
@patch(f"{_MODULE}.run_sh")
def test_enable_rejects_ttl_on_correctness_fences(mock_run_sh, collection_group):
    ttl = RegistryV2TtlPolicies(project_id="my-project", database_id="my-db")
    with pytest.raises(ValueError, match="TTL is forbidden"):
        ttl.enable(collection_group, "expires_at")
    mock_run_sh.assert_not_called()


# ── RegistryV2GcsIam ─────────────────────────────────────────────────────────


def _layout() -> GcsLayoutV2:
    return GcsLayoutV2(
        bucket_name="aigear-prod-assets",
        project_name="aigear_sklearn_pipeline",
        pipeline_version="logistic_regression",
    )


def test_bucket_gs_derived_from_layout():
    iam = RegistryV2GcsIam(layout=_layout(), project_id="my-project")
    assert iam.bucket_gs == "gs://aigear-prod-assets"


def test_condition_expression_scopes_to_v2_object_prefix():
    layout = _layout()
    iam = RegistryV2GcsIam(layout=layout, project_id="my-project")
    expression = iam.condition_expression()
    assert layout.object_prefix in expression
    assert layout.bucket_name in expression
    assert expression.startswith('resource.name.startsWith(')


@patch(f"{_MODULE}.run_sh")
def test_add_prefix_scoped_binding_builds_expected_command(mock_run_sh):
    layout = _layout()
    iam = RegistryV2GcsIam(layout=layout, project_id="my-project")
    iam.add_prefix_scoped_binding(member="serviceAccount:sa@my-project.iam.gserviceaccount.com")
    cmd = mock_run_sh.call_args[0][0]
    assert cmd[:4] == ["gcloud", "storage", "buckets", "add-iam-policy-binding"]
    assert iam.bucket_gs in cmd
    assert "--member=serviceAccount:sa@my-project.iam.gserviceaccount.com" in cmd
    assert "--role=roles/storage.objectAdmin" in cmd
    assert any(arg.startswith("--condition=") and layout.object_prefix in arg for arg in cmd)
    assert "--project=my-project" in cmd
    assert mock_run_sh.call_args.kwargs.get("check") is True


@patch(f"{_MODULE}.run_sh")
def test_add_prefix_scoped_binding_accepts_custom_role(mock_run_sh):
    iam = RegistryV2GcsIam(layout=_layout(), project_id="my-project")
    iam.add_prefix_scoped_binding(member="serviceAccount:sa@x.iam.gserviceaccount.com", role="roles/storage.objectViewer")
    cmd = mock_run_sh.call_args[0][0]
    assert "--role=roles/storage.objectViewer" in cmd

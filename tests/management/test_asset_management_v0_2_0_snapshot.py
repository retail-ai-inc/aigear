"""Behavior snapshot for ``aigear.management.asset.AssetManagement`` (Aigear v0.2.0).

Pipeline V2 (see docs/pipeline-asset-lifecycle-management-v2.md, section 4.1) requires
that any code that could import, instantiate and call ``AssetManagement`` under Aigear
v0.2.0 keeps working unmodified after the V2 rollout. This module pins down the exact
public surface and observable behavior of the class *before* any V2 work touches
adjacent code, so a regression here fails loudly instead of silently changing Legacy
semantics.

These tests must keep passing without any changes to ``aigear.management.asset``.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path

import pytest

from aigear.db.bucket import LocalGCSMock, RealGCS
from aigear.management.asset import AssetManagement


# ── Constructor signature ─────────────────────────────────────────────────────


def test_constructor_signature_is_pinned():
    signature = inspect.signature(AssetManagement.__init__)
    parameters = list(signature.parameters.values())

    assert [p.name for p in parameters] == [
        "self",
        "pipeline_version",
        "data_type",
        "project_id",
        "bucket_name",
        "bucket_on",
    ]
    by_name = {p.name: p for p in parameters}
    assert by_name["pipeline_version"].default is inspect.Parameter.empty
    assert by_name["data_type"].default is inspect.Parameter.empty
    assert by_name["project_id"].default is None
    assert by_name["bucket_name"].default is None
    assert by_name["bucket_on"].default is True
    # All parameters remain usable positionally, matching v0.2.0 call sites.
    assert all(
        p.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        )
        for p in parameters
    )


# ── Public method signatures ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method_name,expected_params",
    [
        ("download", ["self", "file_name"]),
        ("upload", ["self", "file_name"]),
        ("copy_blob", ["self", "source_file_name", "destination_file_name"]),
        ("get_local_path", ["self", "local_file_name"]),
        ("get_bucket_blob", ["self", "bucket_file_name"]),
    ],
)
def test_public_method_signature_is_pinned(method_name, expected_params):
    method = getattr(AssetManagement, method_name)
    signature = inspect.signature(method)
    assert list(signature.parameters.keys()) == expected_params


def test_download_returns_path_and_upload_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="dataset",
        bucket_name="gcs_mock",
        bucket_on=False,
    )
    source = manager.get_local_path("input.csv")
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    manager.upload("input.csv")

    # Remove the local copy so download() has to materialize a fresh file.
    source.unlink()
    result = manager.download("input.csv")

    assert isinstance(result, Path)
    assert result == source
    assert result.read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_upload_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="dataset",
        bucket_name="gcs_mock",
        bucket_on=False,
    )
    local_path = manager.get_local_path("input.csv")
    local_path.write_text("a,b\n1,2\n", encoding="utf-8")

    assert manager.upload("input.csv") is None


# ── GCS / local path layout ────────────────────────────────────────────────────


def test_get_bucket_blob_uses_pipeline_version_data_type_file_name_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="dataset",
        bucket_name="gcs_mock",
        bucket_on=False,
    )
    assert manager.get_bucket_blob("input.csv") == "logistic_regression/dataset/input.csv"


def test_get_local_path_uses_asset_pipeline_version_data_type_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="dataset",
        bucket_name="gcs_mock",
        bucket_on=False,
    )
    local_path = manager.get_local_path("input.csv")

    assert local_path == tmp_path / "asset" / "logistic_regression" / "dataset" / "input.csv"
    assert local_path.parent.exists()


def test_data_type_training_is_still_accepted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="training",
        bucket_name="gcs_mock",
        bucket_on=False,
    )
    assert manager.data_type == "training"
    assert manager.get_bucket_blob("model.pkl") == "logistic_regression/training/model.pkl"


# ── bucket_on / backend selection ─────────────────────────────────────────────


def test_bucket_on_false_uses_local_gcs_mock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="dataset",
        project_id="my-project",
        bucket_name="gcs_mock",
        bucket_on=False,
    )
    assert isinstance(manager.bucket_client, LocalGCSMock)
    assert manager.bucket_client.bucket_path == tmp_path / "asset" / "gcs_mock"


def test_bucket_on_true_constructs_real_gcs(monkeypatch):
    created = {}

    class _FakeRealGCS:
        def __init__(self, project_id, bucket_name):
            created["project_id"] = project_id
            created["bucket_name"] = bucket_name

    monkeypatch.setattr("aigear.management.asset.RealGCS", _FakeRealGCS)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="dataset",
        project_id="my-project",
        bucket_name="asset-bucket",
        bucket_on=True,
    )
    assert isinstance(manager.bucket_client, _FakeRealGCS)
    assert created == {"project_id": "my-project", "bucket_name": "asset-bucket"}


def test_copy_blob_delegates_to_bucket_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = AssetManagement(
        pipeline_version="logistic_regression",
        data_type="dataset",
        bucket_name="gcs_mock",
        bucket_on=False,
    )
    local_path = manager.get_local_path("input.csv")
    local_path.write_text("a,b\n1,2\n", encoding="utf-8")
    manager.upload("input.csv")

    manager.copy_blob("input.csv", "input-copy.csv")

    copied_path = manager.bucket_client.bucket_path / manager.get_bucket_blob(
        "input-copy.csv"
    )
    assert copied_path.exists()


# ── No implicit warnings ──────────────────────────────────────────────────────


def test_normal_usage_emits_no_deprecation_or_future_warnings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager = AssetManagement(
            pipeline_version="logistic_regression",
            data_type="dataset",
            bucket_name="gcs_mock",
            bucket_on=False,
        )
        local_path = manager.get_local_path("input.csv")
        local_path.write_text("a,b\n1,2\n", encoding="utf-8")
        manager.upload("input.csv")
        manager.download("input.csv")
        manager.copy_blob("input.csv", "input-copy.csv")
        manager.get_bucket_blob("input.csv")

    disallowed = [
        item
        for item in caught
        if issubclass(item.category, (DeprecationWarning, FutureWarning))
    ]
    assert disallowed == []


def test_normal_usage_survives_warnings_as_errors(tmp_path, monkeypatch):
    """Projects that run with ``-W error`` must keep working after upgrade."""
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        warnings.simplefilter("error", FutureWarning)
        manager = AssetManagement(
            pipeline_version="logistic_regression",
            data_type="dataset",
            bucket_name="gcs_mock",
            bucket_on=False,
        )
        local_path = manager.get_local_path("input.csv")
        local_path.write_text("a,b\n1,2\n", encoding="utf-8")
        manager.upload("input.csv")
        manager.download("input.csv")


def test_real_gcs_backend_is_still_importable_and_used_when_bucket_on():
    # Guards against accidentally swapping the Legacy backend for a V2 client.
    assert AssetManagement.__init__.__globals__["RealGCS"] is RealGCS

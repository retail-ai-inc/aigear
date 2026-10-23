from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import os

import pytest

from aigear.common.run_id import derive_run_id
from aigear.management import asset_paths as paths
from aigear.management.asset_errors import InvalidAssetPath


@pytest.mark.parametrize("value", [
    "", "/tmp/model", "//server/share", "gs://bucket/model", "https://example/model",
    "C:/model", "C:model", "a\\b", "a//b", "a/./b", "a/../b", ".", "..",
    "a\x00b", "a\nb", "a/*", "a/?", "a/nul.txt", "a/COM1", "a/x.", "a/x ",
])
def test_unsafe_relative_paths_are_rejected(value):
    with pytest.raises(InvalidAssetPath):
        paths.relative_path(value)


@pytest.mark.parametrize("value", [None, 12, "a/b", "v1/", "..", "running", "TRASH", "manifest.json", "a b", "NUL", "v1."])
def test_identifiers_are_safe_single_segments(value):
    with pytest.raises(InvalidAssetPath):
        paths.identifier(value)


@pytest.mark.parametrize("value", [
    "running/v1", "assets/v1", "trash/v1", "backup/v1", "_aigear/x", "Running/v1",
    "manifest.json", "models/manifest.json", "models/Manifest.JSON/child",
])
def test_release_reserved_prefixes_and_manifest_collisions(value):
    with pytest.raises(InvalidAssetPath):
        paths.release_path(value)


@pytest.mark.parametrize("value", ["training", "other/file", "feature/manifest.json", "training/a/../file"])
def test_artifact_requires_category_and_safe_file(value):
    with pytest.raises(InvalidAssetPath):
        paths.artifact_path(value)


def test_layout_and_trailing_slash_normalization():
    assert paths.release_path("models/risk///") == "models/risk"
    assert paths.artifact_path("feature/nested/scaler.pkl/") == "feature/nested/scaler.pkl"
    assert paths.workspace_prefix("v1.2") == "running/v1.2"
    assert paths.release_prefix("v1") == "v1"
    assert paths.release_prefix("v1", "models/risk/", backup=True) == "backup/models/risk"
    assert paths.manifest_key("models/risk/") == "models/risk/manifest.json"
    assert paths.relative_path("running/v1/manifest.json") == "running/v1/manifest.json"


def test_snapshot_uses_existing_run_id_and_original_start_in_utc():
    started = datetime(2026, 10, 23, 12, 34, 56, 123456, tzinfo=timezone(timedelta(hours=8)))
    run_id = derive_run_id("project", "v1", "2026-10-23T04:34:56.123456Z")
    expected = f"{run_id}_20261023T043456123456Z"
    assert paths.snapshot_directory(run_id, started) == expected
    for location in ("assets", "trash"):
        assert paths.snapshot_prefix(location, "v1", run_id, started) == f"{location}/v1/{expected}"
    with pytest.raises(ValueError, match="timezone"):
        paths.snapshot_directory(run_id, started.replace(tzinfo=None))
    with pytest.raises(InvalidAssetPath):
        paths.snapshot_prefix("running", "v1", run_id, started)


def test_local_roots_are_separate_and_pure(tmp_path):
    roots = paths.local_asset_roots(tmp_path, "train-bucket", "v1")
    assert roots.bucket == tmp_path / "asset/buckets/train-bucket"
    assert roots.cache == tmp_path / "asset/cache/train-bucket/v1"
    assert not (tmp_path / "asset").exists()
    assert paths.resolve_under(roots.bucket, "running/v1/training/model") == roots.bucket / "running/v1/training/model"
    with pytest.raises(InvalidAssetPath):
        paths.resolve_under(roots.cache, "../buckets/model")


@pytest.mark.parametrize("values", [
    ["training/a", "training/a/model"], ["feature/a", "feature/A"], ["models/a/", "models/a"],
])
def test_portable_file_set_rejects_collisions(values):
    with pytest.raises(InvalidAssetPath):
        paths.validate_file_set(values)


def _directory_link(link: Path, target: Path):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # Windows junctions require no developer-mode or symlink privilege.
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if result.returncode != 0:
            pytest.skip("This environment does not permit directory symlinks or junctions")


def test_resolved_link_cannot_escape_root(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked"
    _directory_link(link, outside)
    try:
        with pytest.raises(InvalidAssetPath, match="escapes"):
            paths.resolve_under(root, "linked/model")
    finally:
        link.unlink() if link.is_symlink() else link.rmdir()


def test_bucket_root_cannot_alias_cache_namespace(tmp_path):
    buckets = tmp_path / "asset/buckets"
    cache = tmp_path / "asset/cache/train/v1"
    buckets.mkdir(parents=True)
    cache.mkdir(parents=True)
    link = buckets / "train"
    _directory_link(link, cache)
    try:
        with pytest.raises(InvalidAssetPath, match="namespaces|overlap"):
            paths.local_asset_roots(tmp_path, "train", "v1")
    finally:
        link.unlink() if link.is_symlink() else link.rmdir()

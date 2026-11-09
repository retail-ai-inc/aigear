from types import SimpleNamespace

from aigear.common import asset_runtime
from aigear.management import asset
from aigear.management.assets import Assets
from aigear.management.release import read_release


def test_publish_backup_and_rollback_use_same_cross_bucket_backend_contract(stores, tmp_path, monkeypatch):
    source, release = stores
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset, 'RealGCS', lambda *args: source)
    monkeypatch.setattr(asset_runtime, 'training_bucket', lambda *args: source)
    manager = asset.AssetManagement('v1', 'training', bucket_name='train')
    assets = Assets._from_bucket('v1', source)
    assets._release_client, assets._release_name = release, str(release.bucket_name)
    for content in [b'one', b'two']:
        with asset_runtime.workflow_step('v1', 'training', SimpleNamespace()):
            manager.get_local_path('model').write_bytes(content); manager.upload('model')
        assets.publish()
    assert release.read_bytes('v1/model') == b'two'
    assert release.read_bytes('backup/v1/model') == b'one'
    backup_generation = release.get_metadata('backup/v1/manifest.json').generation
    assets.rollback()
    assert release.read_bytes('v1/model') == b'one'
    assert release.get_metadata('backup/v1/manifest.json').generation == backup_generation
    bundle = read_release(release, 'v1')
    assert bundle.files == {'model': b'one'}
    assert isinstance(bundle.manifest_generation, str)

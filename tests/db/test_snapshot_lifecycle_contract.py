from types import SimpleNamespace

from aigear.common import asset_runtime
from aigear.management import asset
from aigear.management.assets import Assets


def test_retirement_uses_same_fixed_generation_and_custom_time_contract(stores, tmp_path, monkeypatch):
    source, _ = stores
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset, 'RealGCS', lambda *args: source)
    monkeypatch.setattr(asset_runtime, 'training_bucket', lambda *args: source)
    manager = asset.AssetManagement('v1', 'feature', bucket_name='train')
    assets = Assets._from_bucket('v1', source)
    for number in range(2):
        with asset_runtime.workflow_step('v1', 'features', SimpleNamespace()):
            manager.get_local_path('x').write_bytes(str(number).encode())
            manager.upload('x')
        assets.register()
    result = assets.retire(1)
    assert len(result.retired) == 1 and len(assets.list()) == 1
    meta, snapshot = assets._snapshots(('trash',))[0]
    assert meta.custom_time == snapshot.retired_at
    prefix = meta.name.rsplit('/', 1)[0]
    for member in snapshot.committed_files():
        assert source.get_metadata(f'{prefix}/{member.path}').custom_time == snapshot.retired_at


def test_running_restore_has_same_lineage_and_retention_behavior_on_both_backends(stores, tmp_path, monkeypatch):
    source, _ = stores
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset, 'RealGCS', lambda *args: source)
    monkeypatch.setattr(asset_runtime, 'training_bucket', lambda *args: source)
    manager = asset.AssetManagement('v1', 'feature', bucket_name='train')
    assets = Assets._from_bucket('v1', source)
    checkpoints = []
    for content in [b'original', b'new']:
        with asset_runtime.workflow_step('v1', 'features', SimpleNamespace()):
            manager.get_local_path('x').write_bytes(content); manager.upload('x')
        checkpoints.append(assets.register())
    assets.retire(1)
    assets.restore(checkpoints[0].run_id, target='running')
    assert manager.download('x').read_bytes() == b'original'
    assert source.get_metadata('running/v1/feature/x').custom_time is None

from types import SimpleNamespace

import pytest

from aigear.common import asset_runtime
from aigear.common.asset_context import RunOwnership
from aigear.management import asset


@pytest.mark.parametrize('succeeded', [True, False])
def test_managed_execution_uses_same_backend_contract(stores, tmp_path, monkeypatch, succeeded):
    source, _ = stores
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset, 'RealGCS', lambda *args: source)
    monkeypatch.setattr(asset_runtime, 'training_bucket', lambda *args: source)
    manager = asset.AssetManagement('v1', 'feature', bucket_name='train')
    config = SimpleNamespace()
    def execute():
        with asset_runtime.workflow_step('v1', 'features', config):
            manager.get_local_path('x').write_bytes(b'features')
            manager.upload('x')
            if not succeeded:
                raise RuntimeError('pipeline failed')
    if succeeded:
        execute()
        assert manager.download('x').read_bytes() == b'features'
    else:
        with pytest.raises(RuntimeError):
            execute()
        assert source.list_metadata('running/v1/feature/') == []
    manifest = RunOwnership(source, 'v1').read()[0]
    assert manifest.execution.status == ('succeeded' if succeeded else 'failed')

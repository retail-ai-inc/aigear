"""Framework acceptance without importing or migrating example projects."""
import json
from types import SimpleNamespace

import pytest

from aigear import Assets
from aigear.common import asset_runtime
from aigear.common.asset_context import RunOwnership
from aigear.management import asset
from aigear.management.asset_errors import ExecutionConflict
from aigear.management.release import read_release


def test_failed_cloud_checkpoints_retire_restore_publish_rollback_and_consumer(stores, tmp_path, monkeypatch):
    source, release = stores
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset, 'RealGCS', lambda *args: source)
    monkeypatch.setattr(asset_runtime, 'training_bucket', lambda *args: source)
    feature = asset.AssetManagement('v1', 'feature', bucket_name='train')
    model = asset.AssetManagement('v1', 'training', bucket_name='train')
    assets = Assets._from_bucket('v1', source)
    assets._release_client, assets._release_name = release, str(release.bucket_name)
    config = SimpleNamespace(gcp=SimpleNamespace(bucket=SimpleNamespace(train_bucket=SimpleNamespace(bucket_name='train'))))
    ownership = RunOwnership(source, 'v1')
    from datetime import datetime, timezone
    initial = ownership.acquire('cloud-checkpoint', datetime.now(timezone.utc))
    source.write_bytes('v1/training/legacy.bin', b'legacy-layout')

    def save(manager, name, version):
        manager.get_local_path(name).write_bytes(json.dumps({'version': version}).encode())
        manager.upload(name)

    def cloud_step(name, body):
        e = ownership.prepare_step(initial.run_id, initial.owner_token, name)
        for key, value in dict(AIGEAR_OWNER_TOKEN=e.owner_token, AIGEAR_STEP_ATTEMPT_TOKEN=e.step_attempt_token,
            AIGEAR_RUN_ID=e.run_id, AIGEAR_PIPELINE_VERSION='v1', AIGEAR_STEP_NAME=name, AIGEAR_TRAIN_BUCKET='train').items():
            monkeypatch.setenv(key, value)
        with asset_runtime.workflow_step('v1', name, config):
            return body()

    cloud_step('preprocessing', lambda: save(feature, 'scaler.json', 1))
    assert assets.list() == []
    first = cloud_step('save_features', assets.register)
    def train_one():
        feature.download('scaler.json')
        save(model, 'model.json', 1)
    cloud_step('training', train_one)
    second = cloud_step('save_model', assets.register)
    assert first.registered_at == second.registered_at and len(assets.list()) == 1
    with pytest.raises(RuntimeError):
        cloud_step('after_checkpoint', lambda: (_ for _ in ()).throw(RuntimeError('execution failed')))
    e = ownership.read()[0].execution
    ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=False, seal=assets._seal_run)
    assert assets.get(initial.run_id).sealed
    assert ownership.read()[0].execution.status == 'failed'
    for key in ['AIGEAR_OWNER_TOKEN', 'AIGEAR_STEP_ATTEMPT_TOKEN', 'AIGEAR_RUN_ID', 'AIGEAR_PIPELINE_VERSION',
                'AIGEAR_STEP_NAME', 'AIGEAR_TRAIN_BUCKET']:
        monkeypatch.delenv(key)

    def local_pair(version):
        with asset_runtime.workflow_step('v1', 'preprocessing', config):
            save(feature, 'scaler.json', version)
        with asset_runtime.workflow_step('v1', 'training', config):
            feature.download('scaler.json')
            save(model, 'model.json', version)
    local_pair(2)
    newer = assets.register()
    before = ownership.read()[0]
    plan = assets.retire(1, dry_run=True)
    assert plan.planned[0].run_id == initial.run_id and plan.retired == []
    assets.retire(1)
    assert assets.get(initial.run_id).location == 'trash'
    assets.restore(initial.run_id, target='assets')
    assert assets.get(initial.run_id).registered_at == first.registered_at
    assets.retire(1)
    assets.restore(initial.run_id, target='running')
    restored = ownership.read()[0]
    assert restored.last_run_id is None and restored.latest_run_started_at == before.latest_run_started_at
    with pytest.raises(ExecutionConflict): ownership.acquire(initial.run_id, initial.run_started_at)
    assert json.loads(feature.download('scaler.json').read_bytes())['version'] == 1
    assert json.loads(model.download('model.json').read_bytes())['version'] == 1
    first_release = assets.publish(extra_files=['feature/scaler.json'])
    def consume():
        bundle = read_release(release, 'v1')
        return (json.loads(bundle.files['model.json'])['version'], json.loads(bundle.files['feature/scaler.json'])['version'])
    current = consume()
    assert current == (1, 1)
    assert {file.producer_run_id for file in first_release.files} == {initial.run_id}
    local_pair(3)
    assets.publish(extra_files=['feature/scaler.json'])
    current = consume()
    assert current == (3, 3)
    backup_version = release.get_metadata('backup/v1/manifest.json').generation
    assets.rollback()
    current = consume()
    assert current == (1, 1)
    assert release.get_metadata('backup/v1/manifest.json').generation == backup_version
    assert source.read_bytes('v1/training/legacy.bin') == b'legacy-layout'
    assert [value.run_id for value in assets.list()] == [newer.run_id]
    assert source.get_lifecycle().rules == []  # Asset operations never configure infra rules.

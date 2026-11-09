from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from aigear import Assets
from aigear.common.asset_context import RunOwnership
from aigear.common.asset_runtime import workflow_step
from aigear.management.asset import AssetManagement
from aigear.management.asset_errors import ExecutionConflict, OperationIncomplete, SnapshotConflict, SnapshotNotFound


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id='project', logging=False,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='train'))))
    feature = AssetManagement('v1', 'feature', project_id='project', bucket_name='train', bucket_on=False)
    model = AssetManagement('v1', 'training', project_id='project', bucket_name='train', bucket_on=False)
    assets = Assets('v1', project_id='project', bucket_name='train', bucket_on=False)
    with workflow_step('v1', 'features', config):
        feature.get_local_path('x').write_bytes(b'original-feature'); feature.upload('x')
    with workflow_step('v1', 'training', config):
        feature.download('x')
        model.get_local_path('model').write_bytes(b'original-model'); model.upload('model')
    original = assets.register()
    with workflow_step('v1', 'features', config):
        feature.get_local_path('x').write_bytes(b'new-feature'); feature.upload('x')
    with workflow_step('v1', 'training', config):
        feature.download('x')
        model.get_local_path('model').write_bytes(b'new-model'); model.upload('model')
        model.get_local_path('extra').write_bytes(b'new-extra'); model.upload('extra')
    newer = assets.register()
    assets.retire(1)
    return config, feature, model, assets, original, newer


def test_restore_to_assets_keeps_registration_identity_sealed_and_clears_custom_time(project):
    config, feature, model, assets, original, newer = project
    result = assets.restore(original.run_id)
    assert result.target == 'assets' and result.operation_status == 'completed'
    restored = assets.get(original.run_id)
    assert restored.location == 'assets' and restored.sealed
    assert restored.registered_at == original.registered_at and restored.sources == original.sources
    assert assets._snapshots(('trash',)) == []
    meta, snapshot = next((m, s) for m, s in assets._snapshots() if s.run_id == original.run_id)
    assert meta.custom_time is None and snapshot.retired_at is None
    for output in snapshot.committed_files():
        assert assets._bucket.get_metadata(f'{meta.name.rsplit("/",1)[0]}/{output.path}').custom_time is None
    assert assets.restore(original.run_id) == result
    retired = assets.retire(1)
    assert retired.retired[0].run_id == original.run_id


def test_restore_running_is_whole_workspace_preserves_lineage_and_watermark(project):
    config, feature, model, assets, original, newer = project
    ownership = RunOwnership(feature.bucket_client, 'v1')
    before, _ = ownership.read()
    # Files not managed by a manifest must not be removed.
    assets._bucket.write_bytes('running/v1/training/untracked', b'untracked')
    result = assets.restore(original.run_id, target='running')
    after, _ = ownership.read()
    assert after.last_run_id is None and after.last_run_started_at is None and after.execution is None
    assert (after.latest_run_id, after.latest_run_started_at) == (before.latest_run_id, before.latest_run_started_at)
    assert after.revision > before.revision and after.restored_from.run_id == original.run_id
    assert after.steps['features'].producer_run_id == original.sources['features'].producer_run_id
    assert feature.download('x').read_bytes() == b'original-feature'
    assert model.download('model').read_bytes() == b'original-model'
    assert assets._bucket.read_bytes('running/v1/training/untracked') == b'untracked'
    assert not assets._bucket.list_metadata('running/v1/training/extra')
    assert assets.restore(original.run_id, target='running') == result
    with pytest.raises(ExecutionConflict):
        ownership.acquire(before.latest_run_id, before.latest_run_started_at)
    with workflow_step('v1', 'training', config):
        feature.download('x')
        model.get_local_path('model').write_bytes(b'after-restore'); model.upload('model')
    with pytest.raises(SnapshotConflict):
        assets.restore(original.run_id, target='running')
    assert model.download('model').read_bytes() == b'after-restore'


@pytest.mark.parametrize('target', ['assets', 'running'])
def test_restore_rejects_waiting_or_active_run(project, target):
    config, feature, model, assets, original, newer = project
    ownership = RunOwnership(feature.bucket_client, 'v1')
    ownership.acquire('waiting', datetime.now(timezone.utc))
    with pytest.raises(ExecutionConflict):
        assets.restore(original.run_id, target=target)


@pytest.mark.parametrize('target', ['assets', 'running'])
def test_restore_copy_response_loss_resumes_same_target(project, monkeypatch, target):
    config, feature, model, assets, original, newer = project
    original_copy = assets._bucket.copy_blob
    faulted = False
    def interrupted(*args, **kwargs):
        nonlocal faulted
        result = original_copy(*args, **kwargs)
        if not faulted:
            faulted = True
            raise OSError('copy response lost')
        return result
    monkeypatch.setattr(assets._bucket, 'copy_blob', interrupted)
    with pytest.raises(OperationIncomplete) as failure:
        assets.restore(original.run_id, target=target)
    assert failure.value.operation_id and failure.value.partial_result.target == target
    with pytest.raises(SnapshotConflict):
        assets.restore(original.run_id, target='running' if target == 'assets' else 'assets')
    with pytest.raises(ExecutionConflict):
        feature.download('x')
    with pytest.raises(ExecutionConflict):
        with workflow_step('v1', 'new', config):
            pass
    result = assets.restore(original.run_id, target=target)
    assert result.operation_id == failure.value.operation_id


def test_running_restore_protects_external_unowned_destination_before_any_write(project):
    config, feature, model, assets, original, newer = project
    assets._bucket.write_bytes('running/v1/training/model', b'external')
    before = assets._ownership().read()[0].revision
    with pytest.raises(SnapshotConflict):
        assets.restore(original.run_id, target='running')
    assert assets._ownership().read()[0].revision == before
    assert assets._bucket.read_bytes('running/v1/training/model') == b'external'
    assert assets.get(original.run_id).location == 'trash'


def test_restore_unknown_run_and_invalid_target_fail(project):
    config, feature, model, assets, original, newer = project
    with pytest.raises(SnapshotNotFound):
        assets.restore('unknown')
    with pytest.raises(ValueError):
        assets.restore(original.run_id, target='invalid')


def test_consistent_existing_assets_destination_can_finish_source_cleanup(project):
    from aigear.management.manifest import ArtifactObject
    from aigear.management.asset_paths import snapshot_prefix, manifest_key
    config, feature, model, assets, original, newer = project
    meta, source = assets._snapshots(('trash',))[0]
    prefix = snapshot_prefix('assets', 'v1', source.run_id, source.run_started_at)
    target = source.model_copy(deep=True)
    target.location, target.retired_at = 'assets', None
    for step in target.steps.values():
        for member in step.outputs:
            copied = assets._bucket.copy_blob(f'{meta.name.rsplit("/",1)[0]}/{member.path}', f'{prefix}/{member.path}', source_generation=member.generation, if_generation_match='0')
            member.generation = copied.generation
    assets._bucket.write_json(manifest_key(prefix), target.model_dump(mode='json'), if_generation_match='0')
    with pytest.raises(SnapshotConflict):
        assets.get(original.run_id)  # Queries never arbitrarily choose between duplicates.
    assert assets.restore(original.run_id).operation_status == 'completed'
    assert assets._snapshots(('trash',)) == []
    assert assets.get(original.run_id).registered_at == original.registered_at


def test_assets_restore_reenters_active_management_context_but_running_restore_rejects(project):
    config, feature, model, assets, original, newer = project
    with workflow_step('v1', 'management', config):
        with pytest.raises(ExecutionConflict):
            assets.restore(original.run_id, target='running')
        assert assets.restore(original.run_id, target='assets').operation_status == 'completed'


@pytest.mark.parametrize('stage', ['old_cleanup', 'target_commit', 'source_cleanup', 'control_complete'])
@pytest.mark.parametrize('after', [False, True])
def test_running_restore_each_commit_and_cleanup_stage_resumes(project, monkeypatch, stage, after):
    config, feature, model, assets, original, newer = project
    bucket = assets._bucket
    methods = {name: getattr(bucket, name) for name in ['write_bytes', 'delete']}
    faulted = False
    def wrapper(name):
        def call(*args, **kwargs):
            nonlocal faulted
            key = str(args[0])
            match = False
            if not faulted and name == 'delete':
                match = ((stage == 'old_cleanup' and key == 'running/v1/training/extra')
                         or (stage == 'source_cleanup' and key.startswith('trash/')))
            elif not faulted and name == 'write_bytes' and key == 'running/v1/manifest.json':
                value = json.loads(args[1])
                match = bool(value.get('restored_from')) and ((stage == 'target_commit' and value.get('pending_operation') and value['pending_operation']['phase'] == 'cleaning')
                    or (stage == 'control_complete' and value.get('pending_operation') is None))
            if match: faulted = True
            if match and not after: raise OSError('interrupted before ' + stage)
            result = methods[name](*args, **kwargs)
            if match: raise OSError('response lost after ' + stage)
            return result
        return call
    for name in methods:
        monkeypatch.setattr(bucket, name, wrapper(name))
    with pytest.raises(OperationIncomplete):
        assets.restore(original.run_id, target='running')
    assert faulted
    result = assets.restore(original.run_id, target='running')
    assert result.operation_status == 'completed'
    assert feature.download('x').read_bytes() == b'original-feature'
    assert model.download('model').read_bytes() == b'original-model'
    assert bucket.list_metadata('running/v1/training/extra') == []


@pytest.mark.parametrize('source_name,current_name', [('x', 'x/part'), ('x/part', 'x')])
def test_running_restore_handles_old_file_directory_shape_changes(tmp_path, monkeypatch, source_name, current_name):
    from aigear.management.manifest import ArtifactObject
    from aigear.common.schema.bucket_schema import ObjectNotFound
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id='project', logging=False,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='train'))))
    manager = AssetManagement('v1', 'feature', project_id='project', bucket_name='train', bucket_on=False)
    assets = Assets('v1', project_id='project', bucket_name='train', bucket_on=False)
    snapshots = []
    for content in [b'original', b'newer']:
        with workflow_step('v1', 'features', config):
            manager.get_local_path(source_name).write_bytes(content); manager.upload(source_name)
        snapshots.append(assets.register())
    assets.retire(1)
    ownership = RunOwnership(manager.bucket_client, 'v1')
    workspace, generation = ownership.read()
    old = workspace.steps['features'].outputs[0]
    assets._bucket.delete('running/v1/' + old.path, if_generation_match=old.generation)
    current = assets._bucket.write_bytes('running/v1/feature/' + current_name, b'current-layout')
    workspace.steps['features'].outputs = [ArtifactObject(path='feature/' + current_name,
        generation=current.generation, size=current.size, crc32c=current.crc32c)]
    ownership._write(workspace, generation, 'fixture-layout')
    assets.restore(snapshots[0].run_id, target='running')
    assert manager.download(source_name).read_bytes() == b'original'
    with pytest.raises(ObjectNotFound):
        assets._bucket.get_metadata('running/v1/feature/' + current_name)

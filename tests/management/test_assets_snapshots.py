from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from aigear import Assets
from aigear.common.asset_context import RunOwnership
from aigear.common.asset_runtime import workflow_step
from aigear.management.asset import AssetManagement
from aigear.management.asset_errors import (ExecutionConflict, InvalidArtifact, OperationIncomplete,
                                          SnapshotConflict, SnapshotNotFound)
from aigear.management.asset_paths import snapshot_prefix, manifest_key
from aigear.management.manifest import SnapshotManifest
from aigear.management.managed_io import commit_step, fail_step


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id='project', logging=False,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='train'))))
    manager = AssetManagement('v1', 'feature', project_id='project', bucket_name='train', bucket_on=False)
    assets = Assets('v1', project_id='project', bucket_name='train', bucket_on=False)
    return config, manager, assets


def produce(config, manager, name='features', file='x', data=b'data'):
    with workflow_step('v1', name, config):
        manager.get_local_path(file).write_bytes(data)
        manager.upload(file)


@contextmanager
def cloud_step(ownership, name):
    current = ownership.read()[0].execution
    e = ownership.prepare_step(current.run_id, current.owner_token, name)
    with ownership.claim(e.run_id, e.owner_token, name, e.step_attempt_token) as context:
        try:
            yield e
            commit_step(context)
        except BaseException:
            fail_step(context)
            raise


def checkpoint_in_cloud(manager, assets):
    ownership = RunOwnership(manager.bucket_client, 'v1')
    ownership.acquire('cloud-run', datetime.now(timezone.utc))
    with cloud_step(ownership, 'features'):
        manager.get_local_path('x').write_bytes(b'features'); manager.upload('x')
    with cloud_step(ownership, 'save_features'):
        first = assets.register()
    return ownership, first


def test_construction_and_import_do_not_access_storage(tmp_path, monkeypatch):
    import aigear.management.assets as module
    def no_io(*args):
        raise AssertionError('constructor performed storage IO')
    monkeypatch.setattr(module, 'RealGCS', no_io)
    monkeypatch.setattr(module, 'LocalGCSMock', no_io)
    Assets('v1', bucket_name='train', bucket_on=False)
    Assets('v1', bucket_name='train', bucket_on=True)


def test_no_registration_creates_no_snapshot(project):
    config, manager, assets = project
    produce(config, manager)
    assert assets.list() == []


def test_register_last_completed_run_is_sealed_idempotent_and_read_only_queries(project, monkeypatch):
    config, manager, assets = project
    produce(config, manager)
    result = assets.register()
    assert result.sealed and result.committed and result.included_steps == ['features']
    assert assets.register() == result
    def no_write(*args, **kwargs):
        raise AssertionError('query mutated state')
    monkeypatch.setattr(assets._bucket, 'write_json', no_write)
    assert assets.list() == [result]
    assert assets.get(result.run_id) == result
    with pytest.raises(SnapshotNotFound):
        assets.get('unknown')


def test_cloud_run_two_registrations_append_then_seal(project, monkeypatch):
    config, feature, assets = project
    model = AssetManagement('v1', 'training', project_id='project', bucket_name='train', bucket_on=False)
    ownership = RunOwnership(feature.bucket_client, 'v1')
    run = ownership.acquire('cloud-run', datetime.now(timezone.utc))
    first = None
    for name in ['preprocessing', 'save_features', 'training', 'save_model']:
        e = ownership.prepare_step(run.run_id, run.owner_token, name)
        for key, value in dict(AIGEAR_OWNER_TOKEN=e.owner_token, AIGEAR_STEP_ATTEMPT_TOKEN=e.step_attempt_token,
            AIGEAR_RUN_ID=e.run_id, AIGEAR_PIPELINE_VERSION='v1', AIGEAR_STEP_NAME=name, AIGEAR_TRAIN_BUCKET='train').items():
            monkeypatch.setenv(key, value)
        with workflow_step('v1', name, config):
            if name == 'preprocessing':
                feature.get_local_path('x').write_bytes(b'features'); feature.upload('x')
            elif name == 'training':
                feature.download('x')
                model.get_local_path('model').write_bytes(b'model'); model.upload('model')
            else:
                result = assets.register()
                assert not result.sealed
                if first is None:
                    first = result
                else:
                    assert result.registered_at == first.registered_at
                    assert result.included_steps == ['preprocessing', 'training']
    ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token,
                       succeeded=True, seal=assets._seal_run)
    result = assets.get(run.run_id)
    assert result.sealed and len(assets.list()) == 1
    assert result.sources['training'].producer_run_id == run.run_id


def test_snapshot_validity_independent_of_current_workspace(project):
    config, manager, assets = project
    produce(config, manager)
    checkpoint = assets.register()
    produce(config, manager, data=b'changed')
    assert assets.get(checkpoint.run_id).sealed
    _, snapshot = assets._snapshots()[0]
    from aigear.management.artifact_validity import valid_step
    prefix = snapshot_prefix('assets', 'v1', snapshot.run_id, snapshot.run_started_at)
    assert valid_step(snapshot, 'features', assets._bucket, prefix)


def test_pending_output_cannot_be_registered(project):
    config, manager, assets = project
    with workflow_step('v1', 'features', config):
        manager.get_local_path('x').write_bytes(b'data'); manager.upload('x')
        with pytest.raises(InvalidArtifact):
            assets.register()
    assert assets.list() == []


def test_empty_stale_and_failed_workspace_rejects_registration(project):
    config, manager, assets = project
    with pytest.raises(SnapshotNotFound):
        assets.register()
    with workflow_step('v1', 'management', config):
        pass
    with pytest.raises(InvalidArtifact):
        assets.register()
    produce(config, manager)
    with pytest.raises(RuntimeError):
        with workflow_step('v1', 'features', config):
            raise RuntimeError('failed')
    with pytest.raises(InvalidArtifact):
        assets.register()


def test_external_call_cannot_impersonate_waiting_run(project):
    config, manager, assets = project
    produce(config, manager)
    ownership = RunOwnership(manager.bucket_client, 'v1')
    ownership.acquire('another', datetime.now(timezone.utc))
    with pytest.raises(ExecutionConflict):
        assets.register()


def test_changed_old_member_rejects_append_and_failed_run_keeps_sealed_checkpoint(project):
    config, manager, assets = project
    ownership, first = checkpoint_in_cloud(manager, assets)
    manager.bucket_client.write_bytes('running/v1/feature/x', b'external')
    with pytest.raises(SnapshotConflict):
        with cloud_step(ownership, 'save_model'):
            assets.register()
    e = ownership.read()[0].execution
    ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=False, seal=assets._seal_run)
    result = assets.get(first.run_id)
    assert result.sealed and result.included_steps == ['features']
    assert result.registered_at == first.registered_at


def test_append_interruption_keeps_old_members_and_finalization_completes_fixed_pending(project, monkeypatch):
    config, manager, assets = project
    ownership, first = checkpoint_in_cloud(manager, assets)
    model = AssetManagement('v1', 'training', project_id='project', bucket_name='train', bucket_on=False)
    with cloud_step(ownership, 'training'):
        model.get_local_path('model').write_bytes(b'model'); model.upload('model')
    original = assets._bucket.copy_blob
    monkeypatch.setattr(assets._bucket, 'copy_blob', lambda *a, **k: (_ for _ in ()).throw(OSError('copy interrupted')))
    with pytest.raises(OperationIncomplete):
        with cloud_step(ownership, 'save_model'):
            assets.register()
    assert assets.get(first.run_id).included_steps == ['features']
    monkeypatch.setattr(assets._bucket, 'copy_blob', original)
    e = ownership.read()[0].execution
    ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=False, seal=assets._seal_run)
    result = assets.get(first.run_id)
    assert result.sealed and result.included_steps == ['features', 'training']
    assert result.registered_at == first.registered_at


def test_sealing_write_failure_retains_run_until_retry(project, monkeypatch):
    config, manager, assets = project
    ownership, first = checkpoint_in_cloud(manager, assets)
    original = assets._bucket.write_json
    def interrupted(key, value, **kwargs):
        if key.startswith('assets/') and value['sealed']:
            raise OSError('seal interrupted')
        return original(key, value, **kwargs)
    monkeypatch.setattr(assets._bucket, 'write_json', interrupted)
    e = ownership.read()[0].execution
    with pytest.raises(OperationIncomplete):
        ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=True, seal=assets._seal_run)
    assert ownership.read()[0].execution.status == 'finalizing'
    with pytest.raises(ExecutionConflict):
        ownership.acquire('next', datetime.now(timezone.utc))
    monkeypatch.setattr(assets._bucket, 'write_json', original)
    ownership.finalize(e.run_id, e.owner_token, e.current_step, e.step_attempt_token, succeeded=True, seal=assets._seal_run)
    assert assets.get(first.run_id).sealed


def test_timeout_after_copy_reuses_owned_remote_version(project, monkeypatch):
    config, manager, assets = project
    produce(config, manager)
    original = assets._bucket.copy_blob
    calls = []
    def timeout_once(*args, **kwargs):
        meta = original(*args, **kwargs)
        calls.append(meta)
        if len(calls) == 1:
            raise TimeoutError('response lost')
        return meta
    monkeypatch.setattr(assets._bucket, 'copy_blob', timeout_once)
    with pytest.raises(OperationIncomplete) as failure:
        assets.register()
    assert failure.value.operation_id and failure.value.partial_result
    result = assets.register()
    assert result.sealed and len(calls) == 1
    assert assets._ownership().read()[0].pending_operation is None


def test_interrupted_target_commit_is_reconciled_without_second_copy(project, monkeypatch):
    config, manager, assets = project
    produce(config, manager)
    original = assets._bucket.write_json
    def lost_response(key, value, **kwargs):
        result = original(key, value, **kwargs)
        if key.startswith('assets/') and value['committed']:
            raise TimeoutError('commit response lost')
        return result
    monkeypatch.setattr(assets._bucket, 'write_json', lost_response)
    with pytest.raises(OperationIncomplete):
        assets.register()
    monkeypatch.setattr(assets._bucket, 'write_json', original)
    assert assets.register().sealed


def test_interrupted_registration_preserves_source_and_blocks_new_run(project, monkeypatch):
    config, manager, assets = project
    produce(config, manager)
    monkeypatch.setattr(assets._bucket, 'copy_blob', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OperationIncomplete):
        assets.register()
    assert manager.bucket_client.read_bytes('running/v1/feature/x') == b'data'
    assert assets.list()[0].committed is False
    with pytest.raises(ExecutionConflict):
        with workflow_step('v1', 'next', config):
            pass


def test_source_conflict_aborts_only_owned_candidates_and_releases_operation(project, monkeypatch):
    config, manager, assets = project
    produce(config, manager)
    original = assets._bucket.copy_blob
    def changed_source(*args, **kwargs):
        result = original(*args, **kwargs)
        manager.bucket_client.write_bytes('running/v1/feature/x', b'external')
        return result
    monkeypatch.setattr(assets._bucket, 'copy_blob', changed_source)
    with pytest.raises(SnapshotConflict):
        assets.register()
    assert assets.list() == []
    assert assets._ownership().read()[0].pending_operation is None
    assert manager.bucket_client.read_bytes('running/v1/feature/x') == b'external'


@pytest.mark.parametrize('fault', [None, 'copy_after', 'commit_after'])
def test_python_pending_registration_can_be_finished_and_sealed_by_node(project, monkeypatch, fault):
    import base64
    import json
    from pathlib import Path
    import subprocess
    from aigear.management.manifest import WorkspaceManifest
    config, manager, assets = project
    produce(config, manager)
    original = assets._bucket.copy_blob
    monkeypatch.setattr(assets._bucket, 'copy_blob', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OperationIncomplete):
        assets.register()
    monkeypatch.setattr(assets._bucket, 'copy_blob', original)
    manifest = assets._ownership().read()[0]
    objects = {meta.name: dict(generation=meta.generation, metageneration=meta.metageneration, size=str(meta.size),
        crc32c=meta.crc32c, metadata=meta.metadata,
        body_b64=base64.b64encode(assets._bucket.read_bytes(meta.name, generation=meta.generation)).decode())
        for meta in assets._bucket.list_metadata()}
    bridge = Path(__file__).parents[1] / 'infrastructure/gcp/snapshot_protocol_bridge.mjs'
    result = subprocess.run(['node', str(bridge)], input=json.dumps(dict(manifest=manifest.model_dump(mode='json'), objects=objects, fault=fault)),
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert WorkspaceManifest.model_validate(output['manifest']).pending_operation is None
    snapshots = [value for key, value in output['objects'].items() if key.startswith('assets/') and key.endswith('/manifest.json')]
    snapshot = SnapshotManifest.from_json(base64.b64decode(snapshots[0]['body_b64']))
    assert snapshot.sealed and snapshot.committed
    assert snapshot.registered_at == manifest.pending_operation.started_at
    assert output['copies'] == 1
    assert output['interrupted'] is bool(fault)

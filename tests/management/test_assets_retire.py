from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from aigear import Assets
from aigear.common.asset_context import RunOwnership
from aigear.common.asset_runtime import workflow_step
from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.management.asset import AssetManagement
from aigear.management.asset_errors import InvalidArtifact, OperationIncomplete, SnapshotConflict
from aigear.management.asset_paths import snapshot_prefix


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id='project', logging=False,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='train'))))
    manager = AssetManagement('v1', 'feature', project_id='project', bucket_name='train', bucket_on=False)
    assets = Assets('v1', project_id='project', bucket_name='train', bucket_on=False)
    snapshots = []
    for count in range(3):
        with workflow_step('v1', 'features', config):
            for name in ['x', 'y']:
                manager.get_local_path(name).write_bytes(f'features-{count}-{name}'.encode())
                manager.upload(name)
        snapshots.append(assets.register())
    return assets, snapshots, config, manager


def test_retire_moves_only_older_groups_and_starts_custom_time_together(project):
    assets, snapshots, _, _ = project
    before = {value.run_id: value for value in snapshots}
    result = assets.retire(1)
    assert [item.run_id for item in result.retained] == [snapshots[-1].run_id]
    assert {item.run_id for item in result.retired} == {snapshots[0].run_id, snapshots[1].run_id}
    assert len(assets.list()) == 1 and len(assets.list(include_trash=True)) == 3
    for meta, snapshot in assets._snapshots(('trash',)):
        assert snapshot.registered_at == before[snapshot.run_id].registered_at
        assert snapshot.retired_at is not None and meta.custom_time == snapshot.retired_at
        prefix = meta.name.rsplit('/', 1)[0]
        for member in snapshot.committed_files():
            assert assets._bucket.get_metadata(f'{prefix}/{member.path}').custom_time == snapshot.retired_at
        source = snapshot_prefix('assets', 'v1', snapshot.run_id, snapshot.run_started_at)
        assert assets._bucket.list_metadata(source + '/') == []
    assert assets.retire(1).retired == []


@pytest.mark.parametrize('count', [True, False, 0, -1, 1.5, '2', None])
def test_retention_count_is_strict_and_checked_before_io(tmp_path, monkeypatch, count):
    monkeypatch.chdir(tmp_path)
    assets = Assets('v1', bucket_name='train', bucket_on=False)
    with pytest.raises(ValueError):
        assets.retire(count)
    assert not (tmp_path / 'asset').exists()


def test_empty_dry_run_does_not_create_bucket_or_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assets = Assets('v1', bucket_name='train', bucket_on=False)
    assert assets.retire(1, dry_run=True).planned == []
    assert assets.list() == []
    assert not (tmp_path / 'asset').exists()


def test_dry_run_is_zero_write_and_stable_sort_uses_run_id_on_ties(project, monkeypatch):
    assets, snapshots, _, _ = project
    first_time = snapshots[0].registered_at
    for meta, snapshot in assets._snapshots():
        snapshot.registered_at = first_time
        assets._bucket.write_json(meta.name, snapshot.model_dump(mode='json'), if_generation_match=meta.generation)
    def no_write(*args, **kwargs):
        raise AssertionError('dry-run attempted a mutation')
    for method in ['write_bytes', 'copy_blob', 'delete', 'update_metadata']:
        monkeypatch.setattr(assets._bucket, method, no_write)
    result = assets.retire(1, dry_run=True)
    ordered = sorted(value.run_id for value in snapshots)
    assert [item.run_id for item in result.retained] == ordered[:1]
    assert [item.run_id for item in result.planned] == ordered[1:]
    assert result.retired == []


def test_active_run_is_protected_and_can_exceed_keep_count(project):
    assets, snapshots, _, manager = project
    ownership = RunOwnership(manager.bucket_client, 'v1')
    workspace, generation = ownership.read()
    old_meta, old = next((m, s) for m, s in assets._snapshots() if s.run_id == snapshots[0].run_id)
    # Reproduce an old registered checkpoint still owned by a cloud run.
    workspace.execution.run_id = old.run_id
    workspace.execution.status = 'waiting'
    workspace.execution.terminal_status = None
    ownership._write(workspace, generation, 'test-active')
    result = assets.retire(1, dry_run=True)
    assert {item.run_id for item in result.retained} == {snapshots[-1].run_id, old.run_id}
    assert any(item.reason == 'workspace occupied' for item in result.incomplete)


@pytest.mark.parametrize('stage', ['source_mark', 'target_prepare', 'copy', 'target_commit', 'file_delete',
                                  'source_delete', 'custom_time', 'target_final'])
@pytest.mark.parametrize('after', [False, True])
def test_every_transfer_stage_can_resume_without_losing_group(project, monkeypatch, stage, after):
    assets, snapshots, _, _ = project
    # Keep two so exactly the oldest group is moved in this operation.
    bucket = assets._bucket
    methods = {name: getattr(bucket, name) for name in ['write_bytes', 'copy_blob', 'delete', 'update_metadata']}
    triggered = False
    def inject(name, args, kwargs):
        nonlocal triggered
        if triggered:
            return False
        key = str(args[0])
        match = False
        if name == 'write_bytes' and key.endswith('/manifest.json') and not key.startswith('running/'):
            value = json.loads(args[1])
            match = ((stage == 'source_mark' and key.startswith('assets/') and value.get('pending_operation'))
                or (stage == 'target_prepare' and key.startswith('trash/') and not value['committed'])
                or (stage == 'target_commit' and key.startswith('trash/') and value['committed'] and value.get('pending_operation'))
                or (stage == 'target_final' and key.startswith('trash/') and value['committed'] and not value.get('pending_operation')))
        elif stage == 'copy' and name == 'copy_blob': match = True
        elif stage == 'file_delete' and name == 'delete' and not key.endswith('/manifest.json'): match = True
        elif stage == 'source_delete' and name == 'delete' and key.endswith('/manifest.json'): match = True
        elif stage == 'custom_time' and name == 'update_metadata': match = True
        if match:
            triggered = True
        return bool(match)
    def wrap(name):
        def call(*args, **kwargs):
            fail = inject(name, args, kwargs)
            if fail and not after: raise OSError('interrupted before ' + stage)
            value = methods[name](*args, **kwargs)
            if fail: raise OSError('response lost after ' + stage)
            return value
        return call
    for name in methods:
        monkeypatch.setattr(bucket, name, wrap(name))
    with pytest.raises(OperationIncomplete) as failure:
        assets.retire(2)
    assert triggered and failure.value.operation_id and failure.value.partial_result.incomplete
    # Queries merge both locations and never expose the preparing copy as a second group.
    assert len(assets.list(include_trash=True)) == 3
    result = assets.retire(2)
    assert len(result.retired) == 1
    assert len(assets.list()) == 2
    moved = assets.get(snapshots[0].run_id)
    assert moved.location == 'trash'
    assert assets._ownership().read()[0].pending_operation is None


def test_external_source_version_is_never_deleted_after_target_commit(project, monkeypatch):
    assets, snapshots, _, _ = project
    bucket = assets._bucket
    original = bucket.delete
    modified = None
    def external_then_delete(key, **kwargs):
        nonlocal modified
        if not key.endswith('/manifest.json') and modified is None:
            modified = key
            bucket.write_bytes(key, b'external')
        return original(key, **kwargs)
    monkeypatch.setattr(bucket, 'delete', external_then_delete)
    with pytest.raises(OperationIncomplete):
        assets.retire(2)
    assert bucket.read_bytes(modified) == b'external'
    assert assets.get(snapshots[0].run_id).location == 'trash'
    assert len(assets.list(include_trash=True)) == 3


def test_external_source_manifest_change_stops_before_deleting_members(project, monkeypatch):
    assets, _, _, _ = project
    original = assets._persist_operation
    changed = False
    def change_manifest(operation):
        nonlocal changed
        original(operation)
        if operation.phase == 'cleaning' and not changed:
            changed = True
            key = operation.source.object.key
            assets._bucket.write_bytes(key, assets._bucket.read_bytes(key))
    monkeypatch.setattr(assets, '_persist_operation', change_manifest)
    with pytest.raises(OperationIncomplete):
        assets.retire(2)
    operation = assets._ownership().read()[0].pending_operation
    assert changed and all(not value.source_deleted for value in operation.progress)
    for value in operation.progress:
        assert assets._bucket.get_metadata(value.source.key).generation == value.source.generation


def test_custom_time_retry_uses_fixed_retired_at(project, monkeypatch):
    assets, snapshots, _, _ = project
    bucket = assets._bucket
    original = bucket.update_metadata
    once = False
    def interrupted(*args, **kwargs):
        nonlocal once
        value = original(*args, **kwargs)
        if not once:
            once = True
            raise OSError('patch response lost')
        return value
    monkeypatch.setattr(bucket, 'update_metadata', interrupted)
    with pytest.raises(OperationIncomplete):
        assets.retire(2)
    retired_at = assets._ownership().read()[0].pending_operation.retired_at
    bucket._clock = lambda: retired_at + timedelta(days=100)
    assets.retire(2)
    _, snapshot = next((meta, s) for meta, s in assets._snapshots(('trash',)) if s.run_id == snapshots[0].run_id)
    assert snapshot.retired_at == retired_at
    assert all(meta.custom_time == retired_at for meta in bucket.list_metadata(f'trash/v1/{snapshot.run_id}_'))


def test_partial_success_is_reported_when_later_group_fails(project, monkeypatch):
    assets, _, _, _ = project
    original = assets._bucket.copy_blob
    copies = 0
    def interrupted(*args, **kwargs):
        nonlocal copies
        copies += 1
        if copies == 3:
            raise OSError('second group failed')
        return original(*args, **kwargs)
    monkeypatch.setattr(assets._bucket, 'copy_blob', interrupted)
    with pytest.raises(OperationIncomplete) as failure:
        assets.retire(1)
    assert len(failure.value.partial_result.retired) == 1
    assert len(failure.value.partial_result.incomplete) == 1
    assert failure.value.cause is not None
    assert len(assets.retire(1).retired) == 1


def test_old_files_enter_trash_with_new_custom_time(project):
    assets, snapshots, _, _ = project
    old = datetime(2001, 1, 1, tzinfo=timezone.utc)
    meta, snapshot = next((meta, s) for meta, s in assets._snapshots() if s.run_id == snapshots[0].run_id)
    for member in snapshot.committed_files():
        info = assets._bucket.get_metadata(f'{meta.name.rsplit("/", 1)[0]}/{member.path}')
        assets._bucket.update_metadata(info.name, if_generation_match=info.generation,
            if_metageneration_match=info.metageneration, custom_time=old)
    started = datetime.now(timezone.utc)
    assets.retire(2)
    _, moved = next((meta, s) for meta, s in assets._snapshots(('trash',)))
    assert moved.retired_at >= started and moved.registered_at == snapshot.registered_at


def test_legacy_retention_none_skips_trash_listing_and_creation(project, monkeypatch):
    assets, _, _, _ = project
    assets._settings = SimpleNamespace(train_bucket=SimpleNamespace(trash_retention_days=None))
    original = assets._bucket.list_metadata
    def no_trash(prefix=''):
        assert not prefix.startswith('trash/')
        return original(prefix)
    monkeypatch.setattr(assets._bucket, 'list_metadata', no_trash)
    assert len(assets.list(include_trash=True)) == 3
    with pytest.raises(InvalidArtifact, match='disabled'):
        assets.retire(1)

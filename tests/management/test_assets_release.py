from types import SimpleNamespace
import json

import pytest

from aigear import Assets
from aigear.common.asset_context import RunOwnership
from aigear.common.asset_runtime import workflow_step
from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.management.asset import AssetManagement
from aigear.management.asset_errors import (BackupUnavailable, ExecutionConflict, InvalidArtifact,
                                          OperationIncomplete, SnapshotConflict)
from aigear.management.manifest import ReleaseManifest


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id='project', logging=False,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='train'))))
    model = AssetManagement('v1', 'training', project_id='project', bucket_name='train', bucket_on=False)
    feature = AssetManagement('v1', 'feature', project_id='project', bucket_name='train', bucket_on=False)
    assets = Assets('v1', project_id='project', bucket_name='train', bucket_on=False, release_bucket_name='release')
    return config, feature, model, assets


def produce(project, value):
    config, feature, model, assets = project
    with workflow_step('v1', 'features', config):
        feature.get_local_path('scaler.pkl').write_bytes(b'scaler-' + value); feature.upload('scaler.pkl')
    with workflow_step('v1', 'training', config):
        feature.download('scaler.pkl')
        model.get_local_path('model.pkl').write_bytes(b'model-' + value); model.upload('model.pkl')


def manifest(assets, prefix='v1'):
    return ReleaseManifest.from_json(assets._release_bucket.read_bytes(prefix + '/manifest.json'))


def test_publish_maps_training_and_extra_files_backup_and_rollback_preserve_origins(project):
    config, feature, model, assets = project
    produce(project, b'one')
    first = assets.publish(extra_files=['feature/scaler.pkl'])
    assert first.backup_uri is None
    assert {file.path for file in first.files} == {'model.pkl', 'feature/scaler.pkl'}
    initial = manifest(assets)
    assert assets.publish(extra_files=['feature/scaler.pkl']).operation_id == first.operation_id
    produce(project, b'two')
    second = assets.publish(extra_files=['feature/scaler.pkl'])
    backup = manifest(assets, 'backup/v1')
    assert second.backup_uri and backup.committed
    assert {file.source_object.key for file in backup.files} == {file.source_object.key for file in initial.files}
    assert assets._release_bucket.read_bytes('backup/v1/model.pkl') == b'model-one'
    assert assets._release_bucket.read_bytes('backup/v1/feature/scaler.pkl') == b'scaler-one'
    assert assets._release_bucket.read_bytes('v1/model.pkl') == b'model-two'
    backup_generation = assets._release_bucket.get_metadata('backup/v1/manifest.json').generation
    result = assets.rollback()
    assert result.operation_status == 'completed'
    assert assets._release_bucket.read_bytes('v1/model.pkl') == b'model-one'
    assert assets._release_bucket.read_bytes('v1/feature/scaler.pkl') == b'scaler-one'
    assert assets._release_bucket.get_metadata('backup/v1/manifest.json').generation == backup_generation
    assert assets.rollback().operation_id == result.operation_id
    assert [file.producer_run_id for file in manifest(assets).files] == [file.producer_run_id for file in backup.files]


def test_extra_file_removal_changes_release_and_full_backup_retains_scaler(project):
    config, feature, model, assets = project
    produce(project, b'one')
    assets.publish(extra_files=['feature/scaler.pkl'])
    result = assets.publish()
    assert [file.path for file in result.files] == ['model.pkl']
    with pytest.raises(ObjectNotFound):
        assets._release_bucket.get_metadata('v1/feature/scaler.pkl')
    assert assets._release_bucket.read_bytes('backup/v1/feature/scaler.pkl') == b'scaler-one'
    assets.rollback()
    assert assets._release_bucket.read_bytes('v1/feature/scaler.pkl') == b'scaler-one'


def test_custom_path_requires_matching_rollback_path(project):
    produce(project, b'one'); assets = project[-1]
    assets.publish(path='models/custom')
    produce(project, b'two'); assets.publish(path='models/custom')
    with pytest.raises(BackupUnavailable):
        assets.rollback()
    assert assets.rollback(path='models/custom').release_uri.endswith('/models/custom')
    assert assets._release_bucket.read_bytes('models/custom/model.pkl') == b'model-one'


@pytest.mark.parametrize('extra', [['feature/missing'], ['https://host/file'], ['feature/*.pkl'], ['feature/scaler.pkl/'], 'feature/scaler.pkl'])
def test_invalid_extra_selection_fails_before_writing_release(project, extra):
    produce(project, b'one'); assets = project[-1]
    with pytest.raises((ValueError, InvalidArtifact)):
        assets.publish(extra_files=extra)
    assert assets._release_client is None
    assert assets._ownership().read()[0].pending_operation is None


def test_missing_current_does_not_destroy_complete_backup_and_rollback_uses_create_only(project):
    produce(project, b'one'); assets = project[-1]; assets.publish()
    produce(project, b'two'); assets.publish()
    backup_version = assets._release_bucket.get_metadata('backup/v1/manifest.json').generation
    current = assets._release_bucket.get_metadata('v1/model.pkl')
    assets._release_bucket.delete('v1/model.pkl', if_generation_match=current.generation)
    with pytest.raises((InvalidArtifact, ObjectNotFound)):
        assets.publish()
    assert assets._release_bucket.get_metadata('backup/v1/manifest.json').generation == backup_version
    assets.rollback()
    assert assets._release_bucket.read_bytes('v1/model.pkl') == b'model-one'
    current = assets._release_bucket.get_metadata('v1/model.pkl')
    assets._release_bucket.delete('v1/model.pkl', if_generation_match=current.generation)
    assets.rollback()  # Identical logical release with a missing physical member must be repaired.
    assert assets._release_bucket.read_bytes('v1/model.pkl') == b'model-one'


def test_failed_first_publish_has_no_invented_backup_and_can_resume(project, monkeypatch):
    produce(project, b'one'); assets = project[-1]
    original = assets._bucket.copy_blob
    once = False
    def interrupted(*args, **kwargs):
        nonlocal once
        result = original(*args, **kwargs)
        if not once:
            once = True
            raise OSError('candidate response lost')
        return result
    monkeypatch.setattr(assets._bucket, 'copy_blob', interrupted)
    with pytest.raises(OperationIncomplete) as failure:
        assets.publish()
    assert failure.value.operation_id and failure.value.partial_result.operation_status == 'incomplete'
    with pytest.raises(BackupUnavailable):
        assets.rollback()
    assert assets._ownership().read()[0].pending_operation is not None
    assert assets.publish().operation_id == failure.value.operation_id


def test_failed_candidate_can_rollback_from_complete_backup_without_finishing_publish(project, monkeypatch):
    produce(project, b'one'); assets = project[-1]; assets.publish(extra_files=['feature/scaler.pkl'])
    produce(project, b'two')
    original = assets._bucket.copy_blob
    once = False
    def interrupted(*args, **kwargs):
        nonlocal once
        result = original(*args, **kwargs)
        if not once:
            once = True
            raise OSError('candidate response lost')
        return result
    monkeypatch.setattr(assets._bucket, 'copy_blob', interrupted)
    with pytest.raises(OperationIncomplete):
        assets.publish(extra_files=['feature/scaler.pkl'])
    assert manifest(assets, 'backup/v1').committed
    result = assets.rollback()
    assert result.operation_status == 'completed'
    assert assets._release_bucket.read_bytes('v1/model.pkl') == b'model-one'
    assert assets._release_bucket.read_bytes('v1/feature/scaler.pkl') == b'scaler-one'
    assert assets._ownership().read()[0].pending_operation is None


@pytest.mark.parametrize('stage', ['target_reserve', 'backup_prepare', 'backup_copy', 'backup_commit',
                                  'candidate_copy', 'obsolete_cleanup', 'final_commit'])
@pytest.mark.parametrize('after', [False, True])
def test_publication_stages_resume_one_fixed_operation_with_complete_backup(project, monkeypatch, stage, after):
    produce(project, b'one'); assets = project[-1]; assets.publish(extra_files=['feature/scaler.pkl'])
    produce(project, b'two')
    release, source = assets._release_bucket, assets._bucket
    methods = {(id(client), name): getattr(client, name) for client, name in
               [(release, 'write_bytes'), (release, 'copy_blob'), (release, 'delete'), (source, 'copy_blob')]}
    faulted = False
    def wrap(client, name):
        def call(*args, **kwargs):
            nonlocal faulted
            key = str(args[0])
            match = False
            if not faulted and name == 'write_bytes' and key.endswith('/manifest.json'):
                value = json.loads(args[1])
                match = ((stage == 'target_reserve' and key == 'v1/manifest.json' and value.get('pending_operation'))
                    or (stage == 'backup_prepare' and key == 'backup/v1/manifest.json' and not value['committed'])
                    or (stage == 'backup_commit' and key == 'backup/v1/manifest.json' and value['committed'])
                    or (stage == 'final_commit' and key == 'v1/manifest.json' and value.get('pending_operation') is None))
            elif not faulted and name == 'copy_blob':
                match = ((stage == 'backup_copy' and client is release) or (stage == 'candidate_copy' and client is source))
            elif not faulted and stage == 'obsolete_cleanup' and name == 'delete' and key == 'v1/feature/scaler.pkl':
                match = True
            if match: faulted = True
            if match and not after: raise OSError('interrupted before ' + stage)
            value = methods[(id(client), name)](*args, **kwargs)
            if match: raise OSError('response lost after ' + stage)
            return value
        return call
    for client, name in [(release, 'write_bytes'), (release, 'copy_blob'), (release, 'delete'), (source, 'copy_blob')]:
        monkeypatch.setattr(client, name, wrap(client, name))
    with pytest.raises(OperationIncomplete) as failure:
        assets.publish()  # Removing scaler is a real change and tests obsolete-file cleanup.
    assert faulted and failure.value.operation_id and failure.value.cause
    result = assets.publish()
    assert result.operation_id == failure.value.operation_id
    assert release.read_bytes('v1/model.pkl') == b'model-two'
    assert release.read_bytes('backup/v1/model.pkl') == b'model-one'
    assert release.read_bytes('backup/v1/feature/scaler.pkl') == b'scaler-one'
    assert manifest(assets, 'backup/v1').committed
    assert not release.list_metadata('v1/feature/scaler.pkl')
    assert assets._ownership().read()[0].pending_operation is None


@pytest.mark.parametrize('after', [False, True])
def test_rollback_copy_interruption_keeps_backup_and_is_resumable(project, monkeypatch, after):
    produce(project, b'one'); assets = project[-1]; assets.publish(extra_files=['feature/scaler.pkl'])
    produce(project, b'two'); assets.publish()
    source = assets._release_bucket
    before = source.get_metadata('backup/v1/manifest.json').generation
    original = source.copy_blob
    faulted = False
    def interrupted(*args, **kwargs):
        nonlocal faulted
        fail = not faulted
        faulted = True
        if fail and not after: raise OSError('rollback interrupted')
        value = original(*args, **kwargs)
        if fail: raise OSError('rollback response lost')
        return value
    monkeypatch.setattr(source, 'copy_blob', interrupted)
    with pytest.raises(OperationIncomplete):
        assets.rollback()
    assets.rollback()
    assert source.read_bytes('v1/model.pkl') == b'model-one'
    assert source.read_bytes('v1/feature/scaler.pkl') == b'scaler-one'
    assert source.get_metadata('backup/v1/manifest.json').generation == before


def test_external_candidate_version_is_not_overwritten_by_rollback(project, monkeypatch):
    produce(project, b'one'); assets = project[-1]; assets.publish()
    produce(project, b'two')
    original = assets._bucket.copy_blob
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('candidate response lost')
    monkeypatch.setattr(assets._bucket, 'copy_blob', interrupted)
    with pytest.raises(OperationIncomplete): assets.publish()
    assets._release_bucket.write_bytes('v1/model.pkl', b'foreign')
    with pytest.raises(SnapshotConflict): assets.rollback()
    assert assets._release_bucket.read_bytes('v1/model.pkl') == b'foreign'


def test_pending_retry_cannot_change_source_selection_or_custom_path(project, monkeypatch):
    produce(project, b'one'); assets = project[-1]
    monkeypatch.setattr(assets._bucket, 'copy_blob', lambda *a, **k: (_ for _ in ()).throw(OSError('interrupted')))
    with pytest.raises(OperationIncomplete): assets.publish(path='models/custom')
    with pytest.raises(SnapshotConflict): assets.publish(path='models/other')
    with pytest.raises(SnapshotConflict): assets.publish(path='models/custom', extra_files=['feature/scaler.pkl'])
    assert assets._ownership().read()[0].pending_operation.release_path == 'models/custom'


def test_target_parent_child_overlap_and_unknown_objects_fail_without_workspace_lock(project):
    produce(project, b'one'); assets = project[-1]; assets.publish(path='models/custom')
    for path in ['models', 'models/custom/nested']:
        with pytest.raises(SnapshotConflict): assets.publish(path=path)
        assert assets._ownership().read()[0].pending_operation is None
    assets._release_bucket.write_bytes('other/untracked', b'foreign')
    with pytest.raises(SnapshotConflict): assets.publish(path='other')
    assert assets._release_bucket.read_bytes('other/untracked') == b'foreign'


def test_training_extra_mapping_collision_is_rejected_before_release_io(project):
    produce(project, b'one'); config, feature, model, assets = project
    with workflow_step('v1', 'training', config):
        feature.download('scaler.pkl')
        model.get_local_path('feature/scaler.pkl').write_bytes(b'other'); model.upload('feature/scaler.pkl')
    with pytest.raises(ValueError): assets.publish(extra_files=['feature/scaler.pkl'])
    assert assets._release_client is None


def test_target_cas_conflict_releases_only_this_publishers_workspace(project, monkeypatch):
    from aigear.management.release import ReleaseManager
    produce(project, b'one'); config, _, model, first = project
    second_model = AssetManagement('v2', 'training', project_id='project', bucket_name='train', bucket_on=False)
    second = Assets('v2', project_id='project', bucket_name='train', bucket_on=False, release_bucket_name='release')
    with workflow_step('v2', 'training', config):
        second_model.get_local_path('model.pkl').write_bytes(b'other-pipeline'); second_model.upload('model.pkl')
    original = ReleaseManager._reserve
    raced = False
    def competing_reserve(manager, operation):
        nonlocal raced
        if manager.assets is first and not raced:
            raced = True
            second.publish(path='models/shared')
        return original(manager, operation)
    monkeypatch.setattr(ReleaseManager, '_reserve', competing_reserve)
    with pytest.raises(OperationIncomplete) as failure:
        first.publish(path='models/shared')
    assert isinstance(failure.value.cause, ExecutionConflict)
    assert first._ownership().read()[0].pending_operation is None
    assert second._ownership().read()[0].pending_operation is None
    assert second._release_bucket.read_bytes('models/shared/model.pkl') == b'other-pipeline'
    first.publish(path='models/shared')
    assert first._release_bucket.read_bytes('backup/models/shared/model.pkl') == b'other-pipeline'


def test_backup_commit_response_loss_still_removes_old_backup_extras(project, monkeypatch):
    produce(project, b'one'); assets = project[-1]; assets.publish(extra_files=['feature/scaler.pkl'])
    produce(project, b'two'); assets.publish()
    produce(project, b'three')
    original = assets._release_bucket.write_bytes
    faulted = False
    def interrupted(key, data, **kwargs):
        nonlocal faulted
        result = original(key, data, **kwargs)
        if key == 'backup/v1/manifest.json' and json.loads(data)['committed'] and not faulted:
            faulted = True
            raise OSError('backup commit response lost')
        return result
    monkeypatch.setattr(assets._release_bucket, 'write_bytes', interrupted)
    with pytest.raises(OperationIncomplete): assets.publish()
    assets.publish()
    assert assets._release_bucket.list_metadata('backup/v1/feature/scaler.pkl') == []
    assert assets._release_bucket.read_bytes('backup/v1/model.pkl') == b'model-two'


@pytest.mark.parametrize('caught', [False, True])
def test_operation_retry_finishes_failed_local_run_finalization(project, monkeypatch, caught):
    produce(project, b'one'); config, feature, model, assets = project
    original = assets._bucket.copy_blob
    faulted = False
    def interrupted(*args, **kwargs):
        nonlocal faulted
        result = original(*args, **kwargs)
        if not faulted:
            faulted = True
            raise OSError('candidate response lost')
        return result
    monkeypatch.setattr(assets._bucket, 'copy_blob', interrupted)
    with pytest.raises(OperationIncomplete):
        with workflow_step('v1', 'publish', config):
            if caught:
                with pytest.raises(OperationIncomplete):
                    assets.publish()
            else:
                assets.publish()
    assert assets._ownership().read()[0].execution.status == 'finalizing'
    assets.publish()
    assert assets._ownership().read()[0].execution.status == 'failed'
    with workflow_step('v1', 'next', config):
        pass


def test_pending_outputs_block_publication_and_publication_does_not_register(project):
    config, feature, model, assets = project
    with workflow_step('v1', 'training', config):
        model.get_local_path('model.pkl').write_bytes(b'model'); model.upload('model.pkl')
        with pytest.raises(InvalidArtifact):
            assets.publish()
        assert assets._release_client is None
    assets.publish()
    assert assets.list() == []


def test_same_bucket_release_rejects_reserved_or_legacy_object_conflicts(project):
    produce(project, b'one'); config, feature, model, _ = project
    assets = Assets('v1', project_id='project', bucket_name='train', bucket_on=False, release_bucket_name='train')
    for path in ['running/v1', 'assets/model', 'trash/model', 'backup/model']:
        with pytest.raises(ValueError): assets.publish(path=path)
    assets._bucket.write_bytes('legacy/training/model', b'legacy')
    with pytest.raises(SnapshotConflict): assets.publish(path='legacy')
    assert assets._bucket.read_bytes('legacy/training/model') == b'legacy'
    result = assets.publish()
    assert result.release_uri.endswith('/v1')
    assert assets._bucket.read_bytes('v1/model.pkl') == b'model-one'
    assert model.download('model.pkl').read_bytes() == b'model-one'

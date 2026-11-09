from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from unittest.mock import Mock

from aigear.common.asset_context import RunOwnership, active_context
from aigear.common.asset_runtime import workflow_step
from aigear.common.schema.bucket_schema import ObjectNotFound
from aigear.management.asset import AssetManagement
from aigear.management.asset_errors import ExecutionConflict, InvalidArtifact, InvalidAssetPath
from aigear.management.artifact_validity import valid_step
from aigear.management.managed_io import record_input


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id='project', logging=False,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='train'))))
    managers = {kind: AssetManagement('v1', kind, project_id='project', bucket_name='train', bucket_on=False)
                for kind in ['dataset', 'feature', 'training']}
    return config, managers


def put(manager, name, content=b'data'):
    manager.get_local_path(name).write_bytes(content)
    manager.upload(name)


def test_cross_run_reuse_invalidation_and_unrelated_preservation(project):
    config, m = project
    feature, model = m['feature'], m['training']
    with workflow_step('v1', 'features', config):
        put(feature, 'x')
    with workflow_step('v1', 'training', config):
        assert feature.download('x').read_bytes() == b'data'
        put(model, 'model')
    with workflow_step('v1', 'unrelated', config):
        put(m['dataset'], 'other')
    ownership = RunOwnership(feature.bucket_client, 'v1')
    before = ownership.read()[0]
    assert before.steps['training'].inputs[0].run_id == before.steps['features'].producer_run_id
    with workflow_step('v1', 'features', config):
        put(feature, 'x', b'new')
        during = ownership.read()[0]
        assert during.steps['training'].status == 'stale'
        assert during.steps['unrelated'].status == 'succeeded'
    after = ownership.read()[0]
    assert after.steps['training'].inputs == before.steps['training'].inputs
    with pytest.raises(InvalidArtifact):
        model.download('model')
    assert feature.download('x').read_bytes() == b'new'


def test_multiple_writes_take_final_version_and_remove_obsolete_outputs(project):
    config, m = project
    manager = m['training']
    with workflow_step('v1', 'train', config):
        put(manager, 'old')
        put(manager, 'model', b'one')
        put(manager, 'model', b'two')
    with workflow_step('v1', 'train', config):
        put(manager, 'model', b'three')
    manifest = RunOwnership(manager.bucket_client, 'v1').read()[0]
    assert [o.path for o in manifest.steps['train'].outputs] == ['training/model']
    with pytest.raises(ObjectNotFound):
        manager.bucket_client.get_metadata('running/v1/training/old')
    assert manager.download('model').read_bytes() == b'three'


def test_failed_partial_output_is_cleaned_and_cannot_be_read(project):
    config, m = project
    with pytest.raises(RuntimeError):
        with workflow_step('v1', 'train', config):
            put(m['training'], 'model')
            raise RuntimeError('failed')
    manifest = RunOwnership(m['training'].bucket_client, 'v1').read()[0]
    assert manifest.execution.status == 'failed'
    assert manifest.steps['train'].status == 'failed'
    assert manifest.pending_step is None
    assert not m['training'].bucket_client.list_metadata('running/v1/training/')
    with pytest.raises(InvalidArtifact):
        m['training'].download('model')


def test_timeout_after_upload_is_reconciled_before_failure_cleanup(project, monkeypatch):
    config, m = project
    client = m['feature'].bucket_client
    original = client.write_bytes
    def timed_out(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError('upload response lost')
    monkeypatch.setattr(client, 'write_bytes', timed_out)
    with pytest.raises(TimeoutError):
        with workflow_step('v1', 'features', config):
            put(m['feature'], 'x')
    assert not client.list_metadata('running/v1/feature/')
    assert RunOwnership(client, 'v1').read()[0].execution.status == 'failed'


def test_external_overwrite_is_not_deleted_by_failed_step(project):
    config, m = project
    client = m['feature'].bucket_client
    with pytest.raises(ExecutionConflict):
        with workflow_step('v1', 'features', config):
            put(m['feature'], 'x')
            client.write_bytes('running/v1/feature/x', b'external')
            raise RuntimeError('failed')
    assert client.read_bytes('running/v1/feature/x') == b'external'
    assert RunOwnership(client, 'v1').read()[0].pending_step is not None


def test_zero_output_rerun_does_not_reuse_old_outputs(project):
    config, m = project
    with workflow_step('v1', 'train', config):
        put(m['training'], 'model')
    with pytest.raises(InvalidArtifact):
        with workflow_step('v1', 'train', config):
            pass
    with pytest.raises(InvalidArtifact):
        m['training'].download('model')


def test_cross_step_output_ownership_rejects_before_write(project):
    config, m = project
    with workflow_step('v1', 'producer', config):
        put(m['feature'], 'x')
    with pytest.raises(InvalidAssetPath):
        with workflow_step('v1', 'other', config):
            put(m['feature'], 'x', b'overwrite')
    assert m['feature'].download('x').read_bytes() == b'data'


def test_cache_corruption_refetches_and_generation_change_rejects(project):
    config, m = project
    with workflow_step('v1', 'producer', config):
        put(m['feature'], 'x')
    target = m['feature'].download('x')
    target.write_bytes(b'corrupt')
    assert m['feature'].download('x').read_bytes() == b'data'
    m['feature'].bucket_client.write_bytes('running/v1/feature/x', b'changed')
    with pytest.raises(InvalidArtifact):
        m['feature'].download('x')


def test_failed_read_does_not_record_input(project, monkeypatch):
    config, m = project
    with workflow_step('v1', 'producer', config):
        put(m['feature'], 'x')
    with workflow_step('v1', 'management', config):
        monkeypatch.setattr(m['feature'].bucket_client, 'read_bytes', lambda *a, **k: (_ for _ in ()).throw(OSError('download failed')))
        with pytest.raises(OSError):
            m['feature'].download('x')
        assert active_context().current()[0].pending_step.inputs == []


def test_copy_records_both_input_and_output(project):
    config, m = project
    with workflow_step('v1', 'producer', config):
        put(m['feature'], 'x')
    with workflow_step('v1', 'copy', config):
        m['feature'].copy_blob('x', 'copy')
    record = RunOwnership(m['feature'].bucket_client, 'v1').read()[0].steps['copy']
    assert record.inputs[0].step_name == 'producer'
    assert record.outputs[0].path == 'feature/copy'


def test_external_input_sanitized_and_requires_active_artifact_step(project):
    config, m = project
    with pytest.raises(ExecutionConflict):
        record_input('source')
    with workflow_step('v1', 'producer', config):
        record_input('source', uri='https://alice:password@host/path?token=SECRET&sig=SIGNED#fragment', version='token=HIDDEN')
        put(m['feature'], 'x')
    manifest = RunOwnership(m['feature'].bucket_client, 'v1').read()[0]
    source = manifest.steps['producer'].inputs[0]
    assert source.uri == 'https://host/path'
    assert all(secret not in manifest.to_json() for secret in ['alice', 'password@', 'SECRET', 'SIGNED', 'HIDDEN'])


def test_local_commands_use_distinct_run_ids_and_management_commits(project):
    config, m = project
    ownership = RunOwnership(m['feature'].bucket_client, 'v1')
    ids = []
    for _ in range(2):
        with workflow_step('v1', 'management', config):
            ids.append(active_context().run_id)
    assert ids[0] != ids[1]
    assert ownership.read()[0].execution.committed_steps == ['management']


def test_cloud_reserved_attempt_commits_and_replay_skips_body(project, monkeypatch):
    config, m = project
    ownership = RunOwnership(m['feature'].bucket_client, 'v1')
    e = ownership.acquire('cloud-run', datetime.now(timezone.utc))
    e = ownership.prepare_step(e.run_id, e.owner_token, 'features')
    for name, value in dict(AIGEAR_RUN_ID=e.run_id, AIGEAR_OWNER_TOKEN=e.owner_token,
                            AIGEAR_STEP_ATTEMPT_TOKEN=e.step_attempt_token, AIGEAR_PIPELINE_VERSION='v1',
                            AIGEAR_STEP_NAME='features', AIGEAR_TRAIN_BUCKET='train').items():
        monkeypatch.setenv(name, value)
    with workflow_step('v1', 'features', config) as execute:
        assert execute
        put(m['feature'], 'x')
    assert ownership.read()[0].execution.status == 'waiting'
    with workflow_step('v1', 'features', config) as execute:
        assert not execute
    monkeypatch.setenv('AIGEAR_STEP_ATTEMPT_TOKEN', 'old')
    with pytest.raises(ExecutionConflict):
        with workflow_step('v1', 'features', config):
            pass


@pytest.mark.parametrize('succeeded', [True, False])
def test_real_workflow_entry_commits_or_cleans_partial_upload(project, monkeypatch, succeeded):
    from aigear.cli import task_running
    config, m = project
    monkeypatch.setattr(task_running.AigearConfig, 'get_config', lambda: config)
    monkeypatch.setattr(task_running.PipelinesConfig, 'get_version_config', lambda version: {'features': {'pipeline_step': 'test.step'}})
    monkeypatch.setattr(task_running.RunLogContext, 'install_from_env', lambda **kwargs: None)
    monkeypatch.setattr(task_running, 'Logging', Mock())
    def body(**kwargs):
        put(m['feature'], 'x')
        if not succeeded:
            raise RuntimeError('body failed')
    monkeypatch.setattr(task_running, 'LoadModule', lambda module: SimpleNamespace(load_module=lambda: body))
    assert task_running.run_workflow('v1', 'features') is succeeded
    manifest = RunOwnership(m['feature'].bucket_client, 'v1').read()[0]
    assert manifest.execution.status == ('succeeded' if succeeded else 'failed')
    assert manifest.steps['features'].status == ('succeeded' if succeeded else 'failed')


def test_input_modified_after_read_is_rejected_before_commit(project):
    config, m = project
    with workflow_step('v1', 'producer', config):
        put(m['feature'], 'x')
    with pytest.raises(InvalidArtifact):
        with workflow_step('v1', 'training', config):
            m['feature'].download('x')
            put(m['training'], 'model')
            m['feature'].bucket_client.write_bytes('running/v1/feature/x', b'external')
    assert not m['feature'].bucket_client.list_metadata('running/v1/training/')


def test_cyclic_and_missing_lineage_cannot_be_consumed(project):
    from aigear.management.manifest import ManagedInput, ObjectVersion
    config, m = project
    with workflow_step('v1', 'producer', config):
        put(m['feature'], 'x')
    manifest = RunOwnership(m['feature'].bucket_client, 'v1').read()[0]
    producer = manifest.steps['producer']
    for name in ['producer', 'missing']:
        producer.inputs = [ManagedInput(run_id=producer.producer_run_id, step_name=name,
            step_revision=producer.producer_step_revision, artifact='feature/x',
            observed_object=ObjectVersion(key='running/v1/feature/x', generation=producer.outputs[0].generation))]
        with pytest.raises(InvalidArtifact):
            valid_step(manifest, 'producer', m['feature'].bucket_client, 'running/v1')

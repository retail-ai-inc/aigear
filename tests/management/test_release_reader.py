import json
from types import SimpleNamespace

import pytest

from aigear import Assets
from aigear.common.asset_runtime import workflow_step
from aigear.common.schema.bucket_schema import StoragePermissionDenied
from aigear.management.asset import AssetManagement
from aigear.management.asset_errors import IntegrityError, OperationIncomplete
from aigear.management.release import read_release


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(gcp=SimpleNamespace(gcp_project_id='project', logging=False,
        bucket=SimpleNamespace(on=False, train_bucket=SimpleNamespace(bucket_name='train'))))
    feature = AssetManagement('v1', 'feature', project_id='project', bucket_name='train', bucket_on=False)
    model = AssetManagement('v1', 'training', project_id='project', bucket_name='train', bucket_on=False)
    assets = Assets('v1', project_id='project', bucket_name='train', bucket_on=False, release_bucket_name='release')
    def publish(version, *, valid=True):
        with workflow_step('v1', 'training', config):
            feature.get_local_path('scaler.json').write_bytes(json.dumps({'version': version}).encode()); feature.upload('scaler.json')
            model.get_local_path('model.json').write_bytes(json.dumps({'version': version}).encode() if valid else b'invalid-json'); model.upload('model.json')
        assets.publish(extra_files=['feature/scaler.json'])
    publish(1)
    return assets, publish


class Consumer:
    def __init__(self, bucket):
        self.bucket, self.current = bucket, None

    def reload(self):
        bundle = read_release(self.bucket, 'v1')
        candidate = (json.loads(bundle.files['model.json']), json.loads(bundle.files['feature/scaler.json']))
        self.current = candidate


def test_reader_returns_complete_immutable_group_and_consumer_switches_once(project):
    assets, publish = project
    consumer = Consumer(assets._release_bucket)
    consumer.reload()
    assert consumer.current == ({'version': 1}, {'version': 1})
    before = consumer.current
    publish(2)
    consumer.reload()
    assert consumer.current == ({'version': 2}, {'version': 2}) and consumer.current is not before
    bundle = read_release(assets._release_bucket, 'v1')
    assert isinstance(bundle.manifest_generation, str)
    with pytest.raises(TypeError): bundle.files['model.json'] = b'other'


def test_manifest_change_during_read_retries_whole_group_without_mixing(project, monkeypatch):
    assets, publish = project
    bucket = assets._release_bucket
    consumer = Consumer(bucket); consumer.reload()
    original = bucket.get_metadata
    reads, changed = 0, False
    def concurrent_publication(key):
        nonlocal reads, changed
        if key == 'v1/manifest.json':
            reads += 1
            if reads == 2 and not changed:
                changed = True
                publish(2)
        return original(key)
    monkeypatch.setattr(bucket, 'get_metadata', concurrent_publication)
    consumer.reload()
    assert changed and consumer.current == ({'version': 2}, {'version': 2})


@pytest.mark.parametrize('failure', ['missing', 'checksum', 'pending'])
def test_failed_reload_preserves_old_group_and_new_consumer_unready(project, monkeypatch, failure):
    assets, publish = project
    bucket = assets._release_bucket
    consumer = Consumer(bucket); consumer.reload(); old = consumer.current
    if failure == 'missing':
        meta = bucket.get_metadata('v1/feature/scaler.json')
        bucket.delete(meta.name, if_generation_match=meta.generation)
    elif failure == 'checksum':
        original = bucket.read_bytes
        monkeypatch.setattr(bucket, 'read_bytes', lambda key, **kwargs: b'wrong' if key == 'v1/model.json' else original(key, **kwargs))
    else:
        monkeypatch.setattr(assets._bucket, 'copy_blob', lambda *a, **k: (_ for _ in ()).throw(OSError('candidate failed')))
        with pytest.raises(OperationIncomplete): publish(2)
    with pytest.raises(OperationIncomplete): consumer.reload()
    assert consumer.current is old
    fresh = Consumer(bucket)
    with pytest.raises(OperationIncomplete): fresh.reload()
    assert fresh.current is None


def test_decoder_failure_does_not_replace_old_memory_group(project):
    assets, publish = project
    consumer = Consumer(assets._release_bucket); consumer.reload(); old = consumer.current
    publish(2, valid=False)
    with pytest.raises(json.JSONDecodeError): consumer.reload()
    assert consumer.current is old


def test_missing_members_have_bounded_attempts_and_all_reads_are_versioned(project, monkeypatch):
    assets, publish = project
    bucket = assets._release_bucket
    meta = bucket.get_metadata('v1/feature/scaler.json'); bucket.delete(meta.name, if_generation_match=meta.generation)
    original = bucket.read_bytes
    reads = []
    def tracked(key, *, generation=None):
        assert generation is not None
        reads.append(key)
        return original(key, generation=generation)
    monkeypatch.setattr(bucket, 'read_bytes', tracked)
    with pytest.raises(OperationIncomplete): read_release(bucket, 'v1', max_attempts=2)
    assert reads.count('v1/feature/scaler.json') == 2


def test_unknown_schema_rejected_without_fallback(project):
    assets, publish = project
    bucket = assets._release_bucket
    value = bucket.read_json('v1/manifest.json'); value['schema_version'] = 99
    bucket.write_json('v1/manifest.json', value)
    with pytest.raises(IntegrityError): read_release(bucket, 'v1')


def test_permissions_propagate_with_original_cause(project, monkeypatch):
    assets, publish = project
    cause = PermissionError('denied')
    def denied(key):
        raise StoragePermissionDenied('Release read permission denied', phase='metadata', cause=cause)
    monkeypatch.setattr(assets._release_bucket, 'get_metadata', denied)
    with pytest.raises(StoragePermissionDenied) as failure: read_release(assets._release_bucket, 'v1')
    assert failure.value.cause is cause


@pytest.mark.parametrize('attempts', [True, False, 0, -1, '3', 1.5])
def test_invalid_attempt_budget_rejected_before_io(attempts):
    with pytest.raises(ValueError): read_release(None, 'v1', max_attempts=attempts)

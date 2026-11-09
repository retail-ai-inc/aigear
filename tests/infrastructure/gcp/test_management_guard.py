import copy
import importlib.util
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aigear.management.manifest import WorkspaceManifest

SOURCE = Path(__file__).parents[3] / 'src/aigear/infrastructure/gcp/function/management_guard.py'


@pytest.fixture
def supervisor(monkeypatch):
    spec = importlib.util.spec_from_file_location('vm_management_guard', SOURCE)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    identity = dict(run_id='run-a', owner_token='owner', step_attempt_token='attempt',
                    step_name='model_service', train_bucket='train', pipeline_version='v1')
    manifest = dict(schema_version=1, manifest_type='workspace', pipeline_version='v1', revision=1,
                    updated_at='2026-11-09T00:00:00Z', steps={}, execution=dict(run_id='run-a',
                    run_started_at='2026-11-09T00:00:00Z', owner_token='owner', step_attempt_token='attempt',
                    current_step='model_service', committed_steps=[], status='waiting'))
    state = dict(manifest=manifest, generation='9007199254740993', requests=[])
    def transport(request, timeout):
        state['requests'].append(request)
        query = parse_qs(urlsplit(request.full_url).query)
        if 'metadata.google.internal' in request.full_url:
            value = {'access_token': 'ephemeral-token'}
        elif request.get_method() == 'POST':
            assert query['ifGenerationMatch'] == [state['generation']]
            state['manifest'] = json.loads(request.data)
            WorkspaceManifest.model_validate(state['manifest'])
            state['generation'] = str(int(state['generation']) + 1)
            value = {}
        elif query.get('alt') == ['media']:
            assert query['generation'] == query['ifGenerationMatch'] == [state['generation']]
            value = state['manifest']
        else:
            value = {'generation': state['generation']}
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(guard, 'urlopen', transport)
    return guard, identity, state


def test_native_management_claim_and_commit_use_exact_generations(supervisor):
    guard, identity, state = supervisor
    worker = guard.guard('claim', identity)
    assert state['manifest']['execution']['worker_token'] == worker
    assert state['manifest']['pending_step']['writes'] == []
    with pytest.raises(RuntimeError):
        guard.guard('claim', identity)
    with pytest.raises(RuntimeError):
        guard.guard('commit', identity, 'another-worker')
    guard.guard('commit', identity, worker)
    assert state['manifest']['execution']['committed_steps'] == ['model_service']
    assert state['manifest']['execution']['status'] == 'waiting'
    assert state['manifest']['pending_step'] is None
    with pytest.raises(RuntimeError):
        guard.guard('claim', identity)


def test_native_management_rejects_old_attempt_before_write(supervisor):
    guard, identity, state = supervisor
    before = copy.deepcopy(state['manifest'])
    with pytest.raises(RuntimeError):
        guard.guard('claim', {**identity, 'step_attempt_token': 'obsolete'})
    assert state['manifest'] == before
    assert not any(request.get_method() == 'POST' for request in state['requests'])


def test_native_management_cannot_reuse_old_artifact_outputs(supervisor):
    guard, identity, state = supervisor
    state['manifest']['steps']['model_service'] = {'outputs': [{'path': 'training/model'}]}
    with pytest.raises(RuntimeError):
        guard.guard('claim', identity)
    assert state['generation'] == '9007199254740993'

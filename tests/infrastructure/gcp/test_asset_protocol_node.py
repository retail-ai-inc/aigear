import json
import os
from pathlib import Path
import subprocess
import sys

from aigear.common.asset_context import RunOwnership
from aigear.db.bucket import LocalGCSMock
from aigear.management.manifest import WorkspaceManifest

HERE = Path(__file__).parent


def test_node_behavior_suite():
    result = subprocess.run(['node', '--test', str(HERE / 'asset_protocol.test.mjs')],
                            capture_output=True, text=True, encoding='utf-8', timeout=60,
                            env={**os.environ, 'AIGEAR_TEST_PYTHON': sys.executable})
    assert result.returncode == 0, result.stdout + result.stderr


def test_python_node_cross_vm_wire_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ownership = RunOwnership(LocalGCSMock('project', 'train'), 'v1')
    def node(**request):
        result = subprocess.run(['node', str(HERE / 'asset_protocol_bridge.mjs')],
                                input=json.dumps(request), capture_output=True, text=True, encoding='utf-8', timeout=20)
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        manifest = WorkspaceManifest.model_validate(output['manifest'])
        _, generation = ownership.read()
        ownership.bucket.write_json(ownership.key, manifest.model_dump(mode='json'), if_generation_match=generation)
        return output['result']
    task = dict(pipeline_version='v1', run_id='run-a', run_started_at_utc='2026-11-09T00:00:00.000Z',
                step_name='prepare', train_bucket='train')
    manifest, _ = ownership.read()
    identity = node(action='dispatch', manifest=manifest.model_dump(mode='json'), task=task)
    for step in ['prepare', 'train']:
        if step == 'train':
            manifest, _ = ownership.read()
            identity = node(action='dispatch', manifest=manifest.model_dump(mode='json'),
                            task={**task, 'step_name': step}, previous=identity)
        with ownership.claim(identity['run_id'], identity['owner_token'], step, identity['step_attempt_token']) as context:
            context.commit()
    manifest, _ = ownership.read()
    node(action='terminal', manifest=manifest.model_dump(mode='json'), identity=identity, succeeded=True)
    assert ownership.read()[0].execution.status == 'succeeded'

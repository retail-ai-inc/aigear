"""Standalone VM-native deployment fence; uses only the image's Python stdlib.

The Cloud Function embeds this file into its startup script. It runs around
native kubectl commands, which cannot use the application container's context.
"""
import json
import sys
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4
from datetime import datetime, timezone


def guard(mode, identity, worker=None):
    request = Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                      headers={'Metadata-Flavor': 'Google'})
    with urlopen(request, timeout=30) as response:
        token = json.load(response)['access_token']
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    bucket = quote(identity['train_bucket'], safe='')
    key = 'running/' + identity['pipeline_version'] + '/manifest.json'
    resource = 'https://storage.googleapis.com/storage/v1/b/' + bucket + '/o/' + quote(key, safe='')
    def read(url):
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            return json.load(response)
    meta = read(resource)
    manifest = read(resource + '?' + urlencode({'alt': 'media', 'generation': meta['generation'],
                                               'ifGenerationMatch': meta['generation']}))
    execution = manifest['execution']
    if manifest['schema_version'] != 1 or manifest['pipeline_version'] != identity['pipeline_version'] or (
        execution['run_id'], execution['owner_token'], execution['current_step'], execution['step_attempt_token']
    ) != (identity['run_id'], identity['owner_token'], identity['step_name'], identity['step_attempt_token']):
        raise RuntimeError('Deployment attempt no longer owns the workspace')
    if manifest.get('pending_operation') or manifest['steps'].get(identity['step_name'], {}).get('outputs'):
        raise RuntimeError('Deployment cannot reuse artifact output or pending work')
    if mode == 'claim':
        if execution['status'] != 'waiting' or execution.get('worker_token') or manifest.get('pending_step') or identity['step_name'] in execution['committed_steps']:
            raise RuntimeError('Deployment attempt has already been claimed')
        worker = uuid4().hex
        execution.update(status='running', worker_token=worker)
        manifest['pending_step'] = dict(run_id=identity['run_id'], step_name=identity['step_name'],
            step_attempt_token=identity['step_attempt_token'], inputs=[], outputs=[], writes=[], writes_started=False)
    elif mode == 'commit':
        pending = manifest.get('pending_step')
        if execution['status'] != 'running' or execution.get('worker_token') != worker or not pending or pending['outputs'] or pending['inputs'] or pending['writes_started']:
            raise RuntimeError('Deployment worker cannot commit this attempt')
        execution['committed_steps'].append(identity['step_name'])
        execution.update(status='waiting', worker_token=None)
        manifest['pending_step'] = None
    else:
        raise ValueError('Unknown deployment transition')
    manifest['revision'] += 1
    manifest['updated_at'] = datetime.now(timezone.utc).isoformat()
    url = 'https://storage.googleapis.com/upload/storage/v1/b/' + bucket + '/o?' + urlencode({
        'uploadType': 'media', 'name': key, 'ifGenerationMatch': meta['generation']})
    with urlopen(Request(url, data=json.dumps(manifest).encode(), headers=headers, method='POST'), timeout=30):
        pass
    return worker


if __name__ == '__main__':
    print(guard(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None))

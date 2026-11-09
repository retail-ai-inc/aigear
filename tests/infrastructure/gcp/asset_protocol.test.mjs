import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { CloudRunProtocol, GCSManifestStore, ExecutionConflict, OperationIncomplete,
  dispatchManagedTask } from '../../../src/aigear/infrastructure/gcp/function/asset_protocol.js';

// Load the real index.js, replacing only unavailable cloud SDK transports.
registerHooks({ resolve(specifier, context, next) {
  const stubs = {
    googleapis: 'export const google = {};',
    '@google-cloud/functions-framework': 'export default {cloudEvent() {}};',
    '@google-cloud/logging': 'export class Logging {log() {return {entry(a,b) {return b}, async write() {}}}}',
  };
  if (specifier in stubs) return { url: `data:text/javascript,${encodeURIComponent(stubs[specifier])}`, shortCircuit: true };
  return next(specifier, context);
} });
const { buildStartupScript, processManagedMessage } = await import('../../../src/aigear/infrastructure/gcp/function/index.js');

const task = { pipeline_version: 'v1', run_id: 'run-a', run_started_at_utc: '2026-11-09T00:00:00.000Z',
  step_name: 'prepare', train_bucket: 'train', docker_image: 'test/image', vm_name: 'worker' };
class Store {
  constructor() { this.manifest = { schema_version: 1, manifest_type: 'workspace', pipeline_version: 'v1',
    revision: 0, updated_at: task.run_started_at_utc, steps: {}, execution: null }; this.generation = '0'; }
  async read() { return { manifest: structuredClone(this.manifest), generation: this.generation }; }
  async write(manifest, generation) {
    if (generation !== this.generation) throw new ExecutionConflict('CAS');
    this.generation = String(BigInt(this.generation) + 1n);
    this.manifest = structuredClone(manifest);
    this.manifest.revision += 1;
    return structuredClone(this.manifest);
  }
  commit() {
    this.manifest.execution.committed_steps.push(this.manifest.execution.current_step);
    this.manifest.execution.status = 'waiting';
    this.manifest.execution.worker_token = null;
    this.manifest.pending_step = null;
  }
}

test('duplicate dispatch reuses attempt/VM, running worker is never dispatched again', async () => {
  const store = new Store(), protocol = new CloudRunProtocol(store), calls = [];
  const create = async (identity, suffix) => calls.push([identity, suffix]);
  await dispatchManagedTask(protocol, task, null, create);
  await dispatchManagedTask(protocol, task, null, create);
  assert.deepEqual(calls[0], calls[1]);
  store.manifest.execution.status = 'running';
  await dispatchManagedTask(protocol, task, null, create);
  assert.equal(calls.length, 2);
});

test('same run cannot claim another step without committed predecessor identity', async () => {
  const store = new Store(), p = new CloudRunProtocol(store);
  const identity = await p.dispatch(task);
  await assert.rejects(p.dispatch({ ...task, step_name: 'train' }), ExecutionConflict);
  await assert.rejects(p.dispatch({ ...task, step_name: 'train' }, identity), ExecutionConflict);
  store.commit();
  const next = await p.dispatch({ ...task, step_name: 'train' }, identity);
  assert.notEqual(next.step_attempt_token, identity.step_attempt_token);
  assert.equal(next.owner_token, identity.owner_token);
  assert.deepEqual(await p.dispatch({ ...task, step_name: 'train' }, identity), next);
  assert.equal(await p.dispatch(task), null);
  await assert.rejects(p.terminal(identity, false, async () => {}), ExecutionConflict);
});

test('different runs conflict, replay watermark survives terminal', async () => {
  const store = new Store(), p = new CloudRunProtocol(store);
  const identity = await p.dispatch(task);
  await assert.rejects(p.dispatch({ ...task, run_id: 'run-b' }), ExecutionConflict);
  await p.terminal(identity, false, async () => {});
  for (const replay of [task, { ...task, run_id: 'run-b' }, { ...task, run_started_at_utc: '2027-01-01T00:00:00Z' }]) {
    await assert.rejects(p.dispatch(replay), ExecutionConflict);
  }
  const next = await p.dispatch({ ...task, run_id: 'run-b', run_started_at_utc: '2026-11-10T00:00:00Z' });
  await assert.rejects(p.terminal(identity, false, async () => {}), ExecutionConflict);
  assert.equal(store.manifest.execution.owner_token, next.owner_token);
});

for (const success of [true, false]) {
  test(`terminal ${success}: interrupted seal retains owner, retry and duplicate converge`, async () => {
    const store = new Store(), p = new CloudRunProtocol(store);
    const identity = await p.dispatch(task);
    if (success) store.commit();
    await assert.rejects(p.terminal(identity, success, async () => { throw new Error('seal interrupted'); }));
    assert.equal(store.manifest.execution.status, 'finalizing');
    await assert.rejects(p.dispatch({ ...task, run_id: 'run-b' }), ExecutionConflict);
    await p.terminal(identity, success, async () => {});
    const generation = store.generation;
    await p.terminal(identity, success, async () => { throw new Error('duplicate must not reseal'); });
    assert.equal(store.generation, generation);
    await assert.rejects(p.terminal(identity, !success, async () => {}), ExecutionConflict);
  });
}

test('pending writes and uncommitted success cannot be discarded', async () => {
  const store = new Store(), p = new CloudRunProtocol(store);
  const identity = await p.dispatch(task);
  await assert.rejects(p.terminal(identity, true, async () => {}), ExecutionConflict);
  store.manifest.pending_step = { writes_started: true };
  await assert.rejects(p.terminal(identity, false, async () => {}), OperationIncomplete);
  assert.equal(store.manifest.pending_step.writes_started, true);
  await p.terminal(identity, false, async () => {}, async () => { store.manifest.pending_step = null; });
  assert.equal(store.manifest.execution.status, 'failed');
});

test('GCS uses fixed generation reads and conditional uploads without number conversion', async () => {
  const store = new Store();
  const calls = [], generation = '9223372036854775807';
  const google = { storage: () => ({ objects: {
    async get(args) { calls.push(args); return { data: args.alt ? store.manifest : { generation } }; },
    async insert(args) { calls.push(args); },
  } }) };
  const gcs = new GCSManifestStore(google, {}, 'train', 'v1');
  const read = await gcs.read();
  await gcs.write(read.manifest, read.generation);
  assert.deepEqual(calls[1], { bucket: 'train', object: 'running/v1/manifest.json', alt: 'media',
    generation, ifGenerationMatch: generation });
  assert.equal(calls[2].ifGenerationMatch, generation);
  assert.equal(JSON.parse(calls[2].media.body).revision, 1);
});

test('GCS distinguishes missing manifest, fixed-read disappearance, permissions and CAS', async () => {
  let phase = 'missing';
  const google = { storage: () => ({ objects: {
    async get(args) {
      if (phase === 'missing' || args.alt) throw Object.assign(new Error('missing'), { code: 404 });
      if (phase === 'permission') throw Object.assign(new Error('denied'), { code: 403 });
      return { data: { generation: '9' } };
    },
    async insert() { throw Object.assign(new Error('CAS'), { code: 412 }); },
  } }) };
  const gcs = new GCSManifestStore(google, {}, 'train', 'v1');
  assert.equal((await gcs.read()).generation, '0');
  phase = 'fixed';
  await assert.rejects(gcs.read(), { code: 404 });
  phase = 'permission';
  await assert.rejects(gcs.read(), { code: 403 });
  await assert.rejects(gcs.write(new Store().manifest, '0'), e => e instanceof ExecutionConflict && e.cause.code === 412);
});

test('concurrent dispatch CAS allows one reservation', async () => {
  const store = new Store(), p = new CloudRunProtocol(store);
  const results = await Promise.allSettled([p.dispatch(task), p.dispatch(task)]);
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
  assert.equal(results.filter(r => r.status === 'rejected').length, 1);
});

test('real managed route dispatches, hands off committed step and finalizes', async () => {
  const store = new Store(), created = [], options = { store, seal: async () => {},
    createVM: async (task, suffix) => { created.push(task); return suffix; } };
  const schedulerTask = { ...task, run_started_at_utc: '2026-11-09T00:00:00+00:00' };
  await processManagedMessage([schedulerTask, { ...schedulerTask, step_name: 'train' }], {}, options);
  const first = created[0];
  store.commit();
  await processManagedMessage({ done: true, asset_context: first, next_tasks: [{ ...schedulerTask, step_name: 'train' }] }, {}, options);
  store.commit();
  await processManagedMessage({ done: true, asset_context: created[1], next_tasks: [] }, {}, options);
  assert.equal(store.manifest.execution.status, 'succeeded');
  assert.deepEqual(store.manifest.execution.committed_steps, ['prepare', 'train']);
});

test('known VM failure finalizes, ambiguous network failure retains reservation for retry', async () => {
  for (const definitelyNotStarted of [true, false]) {
    const store = new Store();
    const options = { store, seal: async () => {}, createVM: async () => {
      throw Object.assign(new Error('insert failed'), { definitelyNotStarted });
    } };
    if (definitelyNotStarted) await processManagedMessage([task], {}, options);
    else await assert.rejects(processManagedMessage([task], {}, options));
    assert.equal(store.manifest.execution.status, definitelyNotStarted ? 'failed' : 'waiting');
  }
});

test('startup script passes capabilities, structured handoff and errors without logging tokens', () => {
  const assetContext = { ...task, owner_token: "owner-'quoted", step_attempt_token: 'attempt' };
  const script = buildStartupScript({ dockerImage: 'test/image', gpuFlag: '', pipelineCommand: 'run',
    yamlPathInImage: '', nextMessage: '[]', topicName: 'topic', gkeCluster: '', gkeZone: '',
    runId: task.run_id, runStartedAtUtc: task.run_started_at_utc, pipelineVersion: 'v1',
    projectName: 'test', stepName: 'prepare', assetContext });
  assert.ok(script.includes("AIGEAR_OWNER_TOKEN='owner-'\\''quoted'"));
  assert.ok(script.includes("AIGEAR_STEP_ATTEMPT_TOKEN='attempt'"));
  assert.ok(script.includes('"next_tasks":[]'));
  assert.ok(script.includes('"asset_context":'));
  assert.ok(!script.includes('echo "$error_payload"'));
  assert.ok(script.includes('"error":true'));
});

test('managed route requires seal and homogeneous queue before any mutation', async () => {
  const store = new Store();
  await assert.rejects(processManagedMessage([task], {}, { store }));
  await assert.rejects(processManagedMessage([task, { ...task, train_bucket: 'other' }], {}, { store, seal: async () => {} }));
  assert.equal(store.generation, '0');
});

test('generated Bash executes success, step/startup failure, and retains VM on publish failure', () => {
  const bash = process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : '/bin/bash';
  assert.ok(existsSync(bash), 'Bash is required to verify the startup protocol');
  const directory = mkdtempSync(path.join(tmpdir(), 'aigear-script-'));
  try {
    const script = buildStartupScript({ dockerImage: 'test/image', gpuFlag: '', pipelineCommand: 'run',
      yamlPathInImage: '', nextMessage: '[]', topicName: 'topic', gkeCluster: '', gkeZone: '',
      runId: task.run_id, runStartedAtUtc: task.run_started_at_utc, pipelineVersion: 'v1',
      projectName: 'test', stepName: 'prepare', assetContext: { ...task, owner_token: 'owner', step_attempt_token: 'attempt' } });
    const syntax = spawnSync(bash, ['-n'], { input: script, encoding: 'utf8' });
    assert.equal(syntax.status, 0, syntax.stderr);
    // All network, Docker, metadata and delete calls are intercepted as shell functions.
    const prelude = `
gcloud() {
  if [ "$1 $2 $3" = "pubsub topics publish" ]; then
    echo "PUBSUB:$6"
    [ "$FAIL_PUBLISH" = 0 ] || return 1
  elif [ "$1 $2 $3" = "compute instances delete" ]; then echo DELETED; fi
}
docker() { [ "$1" != "$FAIL_STAGE" ] || { echo simulated_error; return 7; }; }
curl() { echo projects/test/zones/zone-a; }
sleep() { :; }
hostname() { echo worker.test; }
python3() { "$AIGEAR_TEST_PYTHON" "$@"; }
`;
    for (const fail of ['', 'pull', 'run']) {
      for (const publishFailure of [false, true]) {
        const result = spawnSync(bash, ['--noprofile', '--norc'], { input: prelude + script,
          encoding: 'utf8', cwd: directory, env: { ...process.env, FAIL_STAGE: fail,
            FAIL_PUBLISH: publishFailure ? '1' : '0', AIGEAR_TEST_PYTHON: process.env.AIGEAR_TEST_PYTHON || 'python' } });
        const line = result.stdout.split(/\r?\n/).find(line => line.startsWith('PUBSUB:'));
        assert.ok(line, result.stderr + result.stdout);
        const payload = JSON.parse(line.slice(7));
        assert.equal(payload.asset_context.owner_token, 'owner');
        assert.equal(payload.asset_context.step_attempt_token, 'attempt');
        assert.equal(!!payload.error, !!fail);
        if (fail) assert.equal(payload.command_exit_code, 7);
        else assert.deepEqual(payload.next_tasks, []);
        assert.equal(result.stdout.includes('DELETED'), !publishFailure);
        assert.equal(result.status === 0, !fail && !publishFailure);
      }
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('terminal persistence failure leaves finalizing state and repeats only idempotent seal', async () => {
  const store = new Store(), p = new CloudRunProtocol(store);
  const identity = await p.dispatch(task);
  const original = store.write.bind(store);
  store.write = async (manifest, generation) => {
    if (manifest.execution.status === 'failed') throw new Error('release interrupted');
    return original(manifest, generation);
  };
  await assert.rejects(p.terminal(identity, false, async () => {}));
  assert.equal(store.manifest.execution.status, 'finalizing');
  store.write = original;
  await p.terminal(identity, false, async () => {});
  assert.equal(store.manifest.execution.status, 'failed');
});

test('unfinished checkpoint operation prevents release after seal callback', async () => {
  const store = new Store(), p = new CloudRunProtocol(store);
  const identity = await p.dispatch(task);
  await assert.rejects(p.terminal(identity, false, async () => {
    store.manifest.pending_operation = { operation_id: 'seal-incomplete' };
  }), OperationIncomplete);
  assert.equal(store.manifest.execution.status, 'finalizing');
  await p.terminal(identity, false, async () => { store.manifest.pending_operation = null; });
  assert.equal(store.manifest.execution.status, 'failed');
});

test('unknown execution status cannot be interpreted as an unoccupied workspace', async () => {
  const manifest = new Store().manifest;
  manifest.execution = { status: 'unknown' };
  const gcs = new GCSManifestStore({ storage: () => ({ objects: {
    async get(args) { return { data: args.alt ? manifest : { generation: '1' } }; },
  } }) }, {}, 'train', 'v1');
  await assert.rejects(gcs.read(), ExecutionConflict);
});

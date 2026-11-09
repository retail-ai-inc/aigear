// Internal PR-04 protocol. PR-05 wires this to the deployed event handler.
// All generations remain strings: GCS counters can exceed Number.MAX_SAFE_INTEGER.
import crypto from 'node:crypto';

export class ExecutionConflict extends Error {}
export class OperationIncomplete extends Error {}
const occupied = new Set(['running', 'waiting', 'finalizing']);
const clone = value => structuredClone(value);

function safeId(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value)
      || /[. ]$/.test(value) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(value)
      || ['running', 'assets', 'trash', 'backup', '_aigear', 'manifest.json'].includes(value.toLowerCase())) {
    throw new ExecutionConflict('Invalid pipeline/run identity');
  }
  return value;
}

function owned(manifest, identity) {
  const e = manifest.execution;
  if (!e || e.run_id !== identity.run_id || e.owner_token !== identity.owner_token
      || e.current_step !== identity.step_name || e.step_attempt_token !== identity.step_attempt_token) {
    throw new ExecutionConflict('Run or attempt does not match');
  }
  return e;
}

export function identityOf(task, execution) {
  return { run_id: execution.run_id, run_started_at_utc: execution.run_started_at,
    pipeline_version: task.pipeline_version, step_name: execution.current_step,
    owner_token: execution.owner_token, step_attempt_token: execution.step_attempt_token,
    train_bucket: task.train_bucket };
}

export class GCSManifestStore {
  constructor(google, auth, bucket, version) {
    if (typeof bucket !== 'string' || !bucket || /[/?#\\]/.test(bucket)) throw new ExecutionConflict('Invalid bucket');
    this.storage = google.storage({ version: 'v1', auth });
    this.bucket = bucket;
    this.version = safeId(version);
    this.key = `running/${version}/manifest.json`;
  }

  async read() {
    let meta;
    try {
      ({ data: meta } = await this.storage.objects.get({ bucket: this.bucket, object: this.key }));
    } catch (err) {
      if ((err.response?.status ?? err.code) !== 404) throw err;
      return { manifest: { schema_version: 1, manifest_type: 'workspace', pipeline_version: this.version,
        revision: 0, updated_at: new Date().toISOString(), steps: {}, pending_operation: null,
        pending_step: null, execution: null }, generation: '0' };
    }
    const { data } = await this.storage.objects.get({ bucket: this.bucket, object: this.key,
      alt: 'media', generation: String(meta.generation), ifGenerationMatch: String(meta.generation) });
    const manifest = typeof data === 'string' ? JSON.parse(data) : data;
    if (manifest.schema_version !== 1 || manifest.manifest_type !== 'workspace'
        || manifest.pipeline_version !== this.version || !Number.isSafeInteger(manifest.revision)
        || manifest.revision < 0 || !manifest.steps || Array.isArray(manifest.steps)
        || !Array.isArray(manifest.execution?.committed_steps ?? [])) {
      throw new ExecutionConflict('Invalid workspace manifest');
    }
    const e = manifest.execution;
    if (e && (!['running', 'waiting', 'finalizing', 'succeeded', 'failed'].includes(e.status)
        || typeof e.owner_token !== 'string' || !e.owner_token || !e.run_id
        || Number.isNaN(new Date(e.run_started_at).getTime())
        || !Array.isArray(e.committed_steps) || e.committed_steps.some(step => typeof step !== 'string' || !step)
        || new Set(e.committed_steps).size !== e.committed_steps.length
        || (e.current_step == null) !== (e.step_attempt_token == null))) {
      throw new ExecutionConflict('Invalid execution manifest');
    }
    return { manifest, generation: String(meta.generation) };
  }

  async write(manifest, generation) {
    const next = clone(manifest);
    next.revision += 1;
    next.updated_at = new Date().toISOString();
    try {
      await this.storage.objects.insert({ bucket: this.bucket, name: this.key,
        ifGenerationMatch: String(generation), requestBody: { contentType: 'application/json' },
        media: { mimeType: 'application/json', body: JSON.stringify(next) } });
    } catch (err) {
      if ((err.response?.status ?? err.code) === 412) {
        throw new ExecutionConflict('Workspace changed concurrently', { cause: err });
      }
      throw err;
    }
    return next;
  }
}

export async function sealUnregistered(store, manifest) {
  let pageToken;
  do {
    const { data } = await store.storage.objects.list({ bucket: store.bucket,
      prefix: `assets/${store.version}/`, pageToken });
    for (const meta of data.items || []) {
      if (!meta.name.endsWith('/manifest.json')) continue;
      const { data: value } = await store.storage.objects.get({ bucket: store.bucket, object: meta.name,
        alt: 'media', generation: String(meta.generation), ifGenerationMatch: String(meta.generation) });
      const snapshot = typeof value === 'string' ? JSON.parse(value) : value;
      if (snapshot.run_id === manifest.execution.run_id) throw new OperationIncomplete('Checkpoint sealing integration is required');
    }
    pageToken = data.nextPageToken;
  } while (pageToken);
}

export async function reconcileStoppedStep(store, identity, confirmStopped) {
  if (!await confirmStopped()) throw new ExecutionConflict('Worker is not confirmed stopped');
  let { manifest, generation } = await store.read();
  const e = owned(manifest, identity);
  const pending = manifest.pending_step;
  if (!pending) return;
  if (pending.run_id !== e.run_id || pending.step_name !== e.current_step
      || pending.step_attempt_token !== e.step_attempt_token) throw new ExecutionConflict('Pending attempt changed');
  const objects = store.storage.objects;
  const key = path => `running/${store.version}/${path}`;
  for (const intent of pending.writes || []) {
    let meta;
    try { ({ data: meta } = await objects.get({ bucket: store.bucket, object: key(intent.path) })); }
    catch (err) { if ((err.response?.status ?? err.code) === 404 && intent.expected_generation === '0') continue; throw err; }
    if (meta.metadata?.aigear_write_id === intent.write_id) {
      pending.outputs = pending.outputs.filter(o => o.path !== intent.path);
      pending.outputs.push({ path: intent.path, generation: String(meta.generation), size: Number(meta.size), crc32c: meta.crc32c });
    } else if (String(meta.generation) !== intent.expected_generation) throw new ExecutionConflict('Interrupted output changed');
  }
  pending.writes = [];
  await store.write(manifest, generation);
  ({ manifest, generation } = await store.read());
  owned(manifest, identity);
  for (const output of manifest.pending_step.outputs) {
    try { await objects.delete({ bucket: store.bucket, object: key(output.path), ifGenerationMatch: output.generation }); }
    catch (err) { if ((err.response?.status ?? err.code) !== 404) throw err; }
  }
  const affected = new Set([e.current_step]);
  let changed;
  do {
    changed = false;
    for (const [name, step] of Object.entries(manifest.steps)) {
      if (!affected.has(name) && (step.inputs || []).some(input => input.kind === 'managed' && affected.has(input.step_name))) {
        affected.add(name); changed = true;
      }
    }
  } while (changed);
  for (const name of affected) if (manifest.steps[name]) manifest.steps[name].status = 'stale';
  if (manifest.steps[e.current_step]) manifest.steps[e.current_step].status = 'failed';
  else manifest.steps[e.current_step] = { status: 'failed', inputs: [], outputs: [] };
  manifest.pending_step = null;
  manifest.execution.status = 'waiting';
  manifest.execution.terminal_status = 'failed';
  await store.write(manifest, generation);
}

export class CloudRunProtocol {
  constructor(store) { this.store = store; }

  async dispatch(task, previous = null) {
    safeId(task.run_id);
    safeId(task.pipeline_version);
    if (typeof task.step_name !== 'string' || !task.step_name) throw new ExecutionConflict('Missing step');
    const started = new Date(task.run_started_at_utc);
    if (Number.isNaN(started.getTime())) throw new ExecutionConflict('Invalid run start');
    const { manifest, generation } = await this.store.read();
    if (manifest.pipeline_version !== task.pipeline_version) throw new ExecutionConflict('Pipeline mismatch');
    let e = manifest.execution;
    if (!e || !occupied.has(e.status)) {
      if (previous || manifest.pending_operation || manifest.pending_step
          || task.run_id === manifest.latest_run_id
          || (manifest.latest_run_started_at && started <= new Date(manifest.latest_run_started_at))) {
        throw new ExecutionConflict('Run rejected by replay watermark or pending work');
      }
      manifest.latest_run_id = manifest.last_run_id = task.run_id;
      manifest.latest_run_started_at = manifest.last_run_started_at = started.toISOString();
      e = manifest.execution = { run_id: task.run_id, run_started_at: started.toISOString(),
        owner_token: crypto.randomUUID(), status: 'waiting', current_step: null,
        step_attempt_token: null, committed_steps: [], worker_token: null, terminal_status: null };
    } else {
      if (e.run_id !== task.run_id || new Date(e.run_started_at).getTime() !== started.getTime()) {
        throw new ExecutionConflict('Workspace is occupied by another run');
      }
      if (e.committed_steps.includes(task.step_name)) return null;
      if (previous) {
        if (e.current_step === task.step_name) {
          if (e.owner_token !== previous.owner_token || e.previous_step_name !== previous.step_name
              || e.previous_step_attempt_token !== previous.step_attempt_token || e.run_id !== previous.run_id) {
            throw new ExecutionConflict('Previous attempt does not match handoff');
          }
        } else owned(manifest, previous);
        if (!e.committed_steps.includes(previous.step_name) || manifest.pending_step) {
          throw new ExecutionConflict('Previous step has not committed');
        }
      } else if (e.current_step !== task.step_name) {
        throw new ExecutionConflict('Continuation requires previous attempt identity');
      }
      if (e.status === 'running' || e.worker_token || e.terminal_status === 'failed') return null;
      if (e.status !== 'waiting') throw new ExecutionConflict('Run is finalizing');
      if (e.current_step === task.step_name) return identityOf(task, e);
    }
    if (manifest.pending_operation || manifest.pending_step) throw new ExecutionConflict('Workspace has pending work');
    e.previous_step_name = previous?.step_name ?? null;
    e.previous_step_attempt_token = previous?.step_attempt_token ?? null;
    e.current_step = task.step_name;
    e.step_attempt_token = crypto.randomUUID();
    e.worker_token = null;
    const next = await this.store.write(manifest, generation);
    return identityOf(task, next.execution);
  }

  async terminal(identity, succeeded, seal, reconcileFailure) {
    let { manifest, generation } = await this.store.read();
    let e = owned(manifest, identity);
    const outcome = succeeded ? 'succeeded' : 'failed';
    if (['succeeded', 'failed'].includes(e.status)) {
      if (e.status !== outcome) throw new ExecutionConflict('Terminal outcome changed');
      return manifest;
    }
    if (e.terminal_status && e.terminal_status !== outcome) throw new ExecutionConflict('Terminal outcome changed');
    if (succeeded && (!e.committed_steps.includes(identity.step_name) || manifest.pending_step)) {
      throw new ExecutionConflict('Success requires committed step');
    }
    if (manifest.pending_step) {
      // PR-05 must reconcile outputs after confirming the container has stopped.
      // Never discard pending output versions just to make a terminal succeed.
      if (!reconcileFailure) throw new OperationIncomplete('Failure requires step reconciliation');
      await reconcileFailure(identity);
      return this.terminal(identity, succeeded, seal);
    }
    if (e.status !== 'finalizing') {
      e.status = 'finalizing';
      e.terminal_status = outcome;
      manifest = await this.store.write(manifest, generation);
    }
    await seal(manifest); // mandatory, idempotent PR-06 boundary; failures retain ownership
    ({ manifest, generation } = await this.store.read());
    e = owned(manifest, identity);
    if (e.status === outcome) return manifest;
    if (e.status !== 'finalizing' || e.terminal_status !== outcome) throw new ExecutionConflict('Finalization changed');
    if (manifest.pending_operation || manifest.pending_step) throw new OperationIncomplete('Pending work prevents release');
    e.status = outcome;
    return this.store.write(manifest, generation);
  }
}

export async function dispatchManagedTask(protocol, task, previous, createVM) {
  const identity = await protocol.dispatch(task, previous);
  if (!identity) return null;
  // Deterministic per attempt, across function retries and VM zones.
  const vmSuffix = crypto.createHash('sha256').update(identity.step_attempt_token).digest('hex').slice(0, 12);
  // Recheck immediately before VM creation; worker claim provides the final CAS fence.
  const { manifest } = await protocol.store.read();
  const e = owned(manifest, identity);
  if (e.status !== 'waiting') return null;
  return createVM({ ...task, ...identity }, vmSuffix);
}

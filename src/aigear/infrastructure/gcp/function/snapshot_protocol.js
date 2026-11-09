// Cloud terminal support for the same fixed registration protocol as Python.
import { ExecutionConflict, OperationIncomplete } from './asset_protocol.js';

const json = value => typeof value === 'string' ? JSON.parse(value) : value;
const stamp = value => new Date(value).toISOString();
function snapshotPrefix(source) {
  const date = new Date(source.execution.run_started_at);
  // Python snapshot_directory uses six microsecond digits; JS timestamps have milliseconds.
  const original = source.execution.run_started_at;
  const fraction = (original.match(/\.(\d+)/)?.[1] || '').padEnd(6, '0').slice(0, 6);
  const directory = stamp(date).slice(0, 19).replace(/[-:]/g, '') + fraction + 'Z';
  return `assets/${source.pipeline_version}/${source.execution.run_id}_${directory}`;
}
function sameStep(left, right) {
  const normalize = step => ({ ...step, outputs: step.outputs.map(({ generation, ...output }) => output).sort((a,b) => a.path.localeCompare(b.path)) });
  // Python JSON fields retain the same ordering; compare explicit identity fields.
  const a = normalize(left), b = normalize(right);
  return ['status', 'producer_run_id', 'producer_step_revision', 'completed_at', 'inputs', 'outputs'].every(key =>
    JSON.stringify(a[key]) === JSON.stringify(b[key]));
}
async function readAt(store, key) {
  const { data: meta } = await store.storage.objects.get({ bucket: store.bucket, object: key });
  const { data } = await store.storage.objects.get({ bucket: store.bucket, object: key, alt: 'media',
    generation: String(meta.generation), ifGenerationMatch: String(meta.generation) });
  return { value: json(data), generation: String(meta.generation) };
}
async function writeAt(store, key, value, generation) {
  const { data } = await store.storage.objects.insert({ bucket: store.bucket, name: key, ifGenerationMatch: generation,
    requestBody: { contentType: 'application/json' }, media: { mimeType: 'application/json', body: JSON.stringify(value) } });
  if (!data?.generation) throw new OperationIncomplete('Snapshot write did not return a generation');
  return String(data.generation);
}
async function verify(store, prefix, output) {
  const { data: meta } = await store.storage.objects.get({ bucket: store.bucket, object: `${prefix}/${output.path}` });
  if (String(meta.generation) !== output.generation || Number(meta.size) !== output.size || meta.crc32c !== output.crc32c) {
    throw new ExecutionConflict('Snapshot/source object changed');
  }
  return meta;
}
async function validateMembers(store, manifest, prefix, workspace = false) {
  const checked = new Set(), visiting = new Set();
  async function visit(name) {
    if (checked.has(name)) return;
    const step = manifest.steps[name];
    if (!step || step.status !== 'succeeded' || !step.outputs.length || visiting.has(name)) throw new ExecutionConflict('Invalid artifact lineage');
    visiting.add(name);
    for (const output of step.outputs) await verify(store, prefix, output);
    for (const input of step.inputs) {
      if (input.kind !== 'managed') continue;
      await visit(input.step_name);
      const producer = manifest.steps[input.step_name], output = producer.outputs.find(o => o.path === input.artifact);
      if (!output || producer.producer_run_id !== input.run_id || producer.producer_step_revision !== input.step_revision
          || (workspace && output.generation !== input.observed_object.generation)) throw new ExecutionConflict('Input production changed');
    }
    visiting.delete(name); checked.add(name);
  }
  for (const [name, step] of Object.entries(manifest.steps)) if (step.outputs.length) await visit(name);
}
async function persist(store, operation) {
  const { manifest, generation } = await store.read();
  if (manifest.pending_operation?.operation_id !== operation.operation_id) throw new ExecutionConflict('Registration owner changed');
  manifest.pending_operation = structuredClone(operation);
  await store.write(manifest, generation);
}
async function clear(store, operation) {
  const { manifest, generation } = await store.read();
  if (!manifest.pending_operation) return;
  if (manifest.pending_operation.operation_id !== operation.operation_id) throw new ExecutionConflict('Registration owner changed');
  manifest.pending_operation = null;
  await store.write(manifest, generation);
}

async function abortRegistration(store, operation) {
  operation.phase = 'cleaning';
  await persist(store, operation);
  const key = operation.expected_targets.at(-1).key, previous = operation.previous_targets[0]?.manifest;
  let read;
  try { read = await readAt(store, key); }
  catch (err) {
    if ((err.response?.status ?? err.code) === 404 && !previous) { await clear(store, operation); return; }
    throw err;
  }
  const target = read.value;
  if (!target.pending_operation && previous && target.run_id === previous.run_id
      && target.registered_at === previous.registered_at && Object.keys(target.steps).length === Object.keys(previous.steps).length
      && Object.entries(previous.steps).every(([name, step]) => target.steps[name] && sameStep(step, target.steps[name]))) {
    await clear(store, operation); return;
  }
  if (target.pending_operation?.operation_id !== operation.operation_id) throw new ExecutionConflict('Cannot clean changed snapshot');
  for (const expected of operation.expected_targets.slice(0, -1)) {
    let meta;
    try { ({ data: meta } = await store.storage.objects.get({ bucket: store.bucket, object: expected.key })); }
    catch (err) { if ((err.response?.status ?? err.code) === 404) continue; throw err; }
    const progress = operation.progress.find(item => item.target.key === expected.key);
    if (meta.metadata?.aigear_operation !== operation.operation_id || (progress && String(meta.generation) !== progress.target.generation)) {
      throw new ExecutionConflict('Cannot clean externally changed candidate');
    }
    await store.storage.objects.delete({ bucket: store.bucket, object: expected.key, ifGenerationMatch: String(meta.generation) });
  }
  if (previous) {
    const restored = structuredClone(previous);
    restored.revision = target.revision + 1; restored.updated_at = new Date().toISOString();
    await writeAt(store, key, restored, read.generation);
  } else await store.storage.objects.delete({ bucket: store.bucket, object: key, ifGenerationMatch: read.generation });
  await clear(store, operation);
}

export async function completeRegistration(store, operation) {
  if (operation.phase === 'cleaning') {
    await abortRegistration(store, operation);
    throw new ExecutionConflict('Registration was aborted after a source conflict');
  }
  try { return await continueRegistration(store, operation); }
  catch (error) {
    try { await validateMembers(store, operation.source.manifest, `running/${store.version}`, true); }
    catch (sourceError) {
      if (sourceError instanceof ExecutionConflict || (sourceError.response?.status ?? sourceError.code) === 404) {
        await abortRegistration(store, operation);
        throw new ExecutionConflict('Fixed registration source changed; owned candidates were removed', { cause: sourceError });
      }
    }
    throw error;
  }
}

async function continueRegistration(store, operation) {
  const source = operation.source.manifest, prefix = snapshotPrefix(source), key = `${prefix}/manifest.json`;
  const selected = Object.fromEntries(Object.entries(source.steps).filter(([,step]) => step.outputs.length));
  const previous = operation.previous_targets[0]?.manifest;
  let target, generation = '0';
  try { const read = await readAt(store, key); target = read.value; generation = read.generation; }
  catch (err) { if ((err.response?.status ?? err.code) !== 404) throw err; }
  if (target?.committed && !target.pending_operation && target.source_revision === source.revision) {
    if (Object.keys(target.steps).length !== Object.keys(selected).length
        || Object.entries(selected).some(([name, step]) => !target.steps[name] || !sameStep(step, target.steps[name]))) {
      throw new ExecutionConflict('Committed registration differs from fixed source');
    }
    await validateMembers(store, target, prefix);
    await clear(store, operation);
    return;
  }
  if (!target?.pending_operation) {
    if (generation !== (operation.expected_targets.at(-1).generation || '0')) throw new ExecutionConflict('Snapshot manifest changed');
    target = { schema_version: 1, manifest_type: 'snapshot', pipeline_version: source.pipeline_version,
      revision: previous ? previous.revision + 1 : 0, updated_at: new Date().toISOString(),
      run_id: source.execution.run_id, run_started_at: source.execution.run_started_at,
      registered_at: previous?.registered_at || operation.started_at,
      included_steps: previous?.included_steps || [], steps: previous?.steps || {}, sealed: false,
      source_revision: source.revision, location: 'assets', committed: !!previous, pending_operation: operation };
    generation = await writeAt(store, key, target, generation);
  } else if (target.pending_operation.operation_id !== operation.operation_id) throw new ExecutionConflict('Snapshot operation changed');
  operation.phase = 'copying';
  await persist(store, operation);
  const steps = structuredClone(selected), sourcePrefix = `running/${source.pipeline_version}`;
  for (const [name, step] of Object.entries(steps)) {
    if (previous?.steps[name]) { step.outputs = structuredClone(previous.steps[name].outputs); continue; }
    for (const output of step.outputs) {
      const sourceGeneration = output.generation, destination = `${prefix}/${output.path}`;
      const sourceMeta = await verify(store, sourcePrefix, output);
      let meta;
      try { ({ data: meta } = await store.storage.objects.get({ bucket: store.bucket, object: destination })); }
      catch (err) { if ((err.response?.status ?? err.code) !== 404) throw err; }
      const progress = operation.progress.find(item => item.target.key === destination);
      if (progress) {
        if (!meta || String(meta.generation) !== progress.target.generation) throw new ExecutionConflict('Registered copy changed');
      } else {
        const recovered = meta && meta.metadata?.aigear_operation === operation.operation_id
          && meta.metadata?.aigear_source_generation === sourceGeneration
          && Number(meta.size) === output.size && meta.crc32c === output.crc32c;
        if (!recovered) {
          const expected = operation.expected_targets.find(item => item.key === destination).generation || '0';
          if (String(meta?.generation || '0') !== expected) throw new ExecutionConflict('Registration destination changed');
          let rewriteToken;
          do {
            const { data } = await store.storage.objects.rewrite({
              sourceBucket: store.bucket, sourceObject: `${sourcePrefix}/${output.path}`,
              destinationBucket: store.bucket, destinationObject: destination,
              ifSourceGenerationMatch: sourceGeneration, ifSourceMetagenerationMatch: String(sourceMeta.metageneration),
              ifGenerationMatch: expected, rewriteToken,
              requestBody: { contentType: sourceMeta.contentType || 'application/octet-stream',
                metadata: { aigear_operation: operation.operation_id, aigear_source_generation: sourceGeneration } },
            });
            rewriteToken = data.done ? undefined : data.rewriteToken;
            if (data.done) meta = data.resource;
            else if (!rewriteToken) throw new OperationIncomplete('Rewrite did not provide continuation token');
          } while (rewriteToken);
        }
        if (Number(meta.size) !== output.size || meta.crc32c !== output.crc32c) throw new ExecutionConflict('Snapshot copy checksum differs');
        operation.progress.push({ source: { key: `${sourcePrefix}/${output.path}`, generation: sourceGeneration },
          target: { key: destination, generation: String(meta.generation) }, size: output.size, crc32c: output.crc32c,
          source_deleted: false, custom_time_set: false });
        await persist(store, operation);
      }
      output.generation = String(meta.generation);
    }
  }
  await validateMembers(store, source, sourcePrefix, true);
  target.steps = steps; target.included_steps = Object.keys(steps);
  target.pending_operation = null; target.committed = true; target.sealed = true;
  target.revision += 1; target.updated_at = new Date().toISOString();
  await validateMembers(store, target, prefix);
  await writeAt(store, key, target, generation);
  await clear(store, operation);
}

export async function sealSnapshots(store, manifest) {
  const current = (await store.read()).manifest;
  if (current.pending_operation) {
    if (current.pending_operation.kind !== 'register') throw new OperationIncomplete('Pending operation prevents sealing');
    await completeRegistration(store, current.pending_operation);
  }
  let pageToken;
  do {
    const { data } = await store.storage.objects.list({ bucket: store.bucket, prefix: `assets/${store.version}/`, pageToken });
    for (const meta of data.items || []) {
      if (!meta.name.endsWith('/manifest.json')) continue;
      const { value, generation } = await readAt(store, meta.name);
      if (value.run_id !== manifest.execution.run_id) continue;
      if (value.schema_version !== 1 || value.manifest_type !== 'snapshot' || value.pipeline_version !== store.version) throw new ExecutionConflict('Invalid snapshot manifest');
      if (!value.committed || value.pending_operation) throw new OperationIncomplete('Snapshot registration is incomplete');
      await validateMembers(store, value, meta.name.slice(0, -'/manifest.json'.length));
      if (!value.sealed) {
        value.sealed = true; value.revision += 1; value.updated_at = new Date().toISOString();
        await writeAt(store, meta.name, value, generation);
      }
    }
    pageToken = data.nextPageToken;
  } while (pageToken);
}

import { sealSnapshots } from '../../../src/aigear/infrastructure/gcp/function/snapshot_protocol.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
let manifest = request.manifest, generation = '1', sequence = 100n, faulted = false, copies = 0;
const objects = request.objects;
const fail = (name) => { if (!faulted && request.fault === name) { faulted = true; throw new Error('response lost: ' + name); } };
const missing = () => { throw Object.assign(new Error('missing'), { code: 404 }); };
const store = { bucket: 'train', version: 'v1',
  async read() { return { manifest: structuredClone(manifest), generation }; },
  async write(value, expected) {
    if (expected !== generation) throw Object.assign(new Error('CAS'), { code: 412 });
    generation = String(BigInt(generation) + 1n);
    manifest = structuredClone(value); manifest.revision += 1; return manifest;
  },
  storage: { objects: {
    async get(args) {
      const item = objects[args.object] || missing();
      if (args.generation && args.generation !== item.generation) throw new Error('source generation changed');
      return { data: args.alt ? JSON.parse(Buffer.from(item.body_b64, 'base64').toString()) : structuredClone(item) };
    },
    async insert(args) {
      const old = objects[args.name];
      if ((old?.generation || '0') !== args.ifGenerationMatch) throw new Error('target generation changed');
      const body = Buffer.from(args.media.body);
      const item = objects[args.name] = { generation: String(++sequence), metageneration: '1',
        body_b64: body.toString('base64'), size: String(body.length), metadata: {} };
      const value = JSON.parse(args.media.body);
      if (value.committed && value.sealed) fail('commit_after');
      return { data: item };
    },
    async rewrite(args) {
      const source = objects[args.sourceObject] || missing(), target = objects[args.destinationObject];
      if (source.generation !== args.ifSourceGenerationMatch || source.metageneration !== args.ifSourceMetagenerationMatch
          || (target?.generation || '0') !== args.ifGenerationMatch) throw new Error('rewrite CAS');
      copies += 1;
      const item = objects[args.destinationObject] = { ...source, generation: String(++sequence), metageneration: '1',
        metadata: args.requestBody.metadata };
      delete item.customTime;
      fail('copy_after');
      return { data: { done: true, resource: item } };
    },
    async list(args) { return { data: { items: Object.entries(objects).filter(([key]) => key.startsWith(args.prefix))
      .map(([name, value]) => ({ name, generation: value.generation })) } }; },
  } },
};
let interrupted = false;
try { await sealSnapshots(store, manifest); }
catch (error) { if (!request.fault) throw error; interrupted = true; await sealSnapshots(store, manifest); }
await sealSnapshots(store, manifest); // an identical terminal retry must be harmless
process.stdout.write(JSON.stringify({ manifest, objects, copies, interrupted }));

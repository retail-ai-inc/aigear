// Real JS transition invoked by Python to check the shared wire contract.
import { CloudRunProtocol } from '../../../src/aigear/infrastructure/gcp/function/asset_protocol.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
let manifest = request.manifest;
const store = {
  async read() { return { manifest: structuredClone(manifest), generation: '1' }; },
  async write(next) { manifest = structuredClone(next); manifest.revision += 1; return manifest; },
};
const protocol = new CloudRunProtocol(store);
const result = request.action === 'terminal'
  ? await protocol.terminal(request.identity, request.succeeded, async () => {})
  : await protocol.dispatch(request.task, request.previous);
process.stdout.write(JSON.stringify({ manifest, result }));

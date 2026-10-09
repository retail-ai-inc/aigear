import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';

const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const logs = [];
const publications = [];
const inserts = [];
let handler;
const google = {
  auth: { GoogleAuth: class { async getClient() { return {}; } } },
  pubsub: () => ({ projects: { topics: { publish: async input => {
    publications.push(JSON.parse(Buffer.from(input.requestBody.messages[0].data, 'base64').toString()));
  } } } }),
  compute: () => ({ instances: {
    get: async () => { throw Object.assign(new Error('NOT_FOUND'), { code: 404 }); },
    insert: async input => {
      inserts.push(input);
      if (request.vmError) throw new Error(request.vmError);
      return { data: { name: 'operation', status: 'DONE' } };
    },
  } }),
};
class Logging {
  log() { return {
    entry: (metadata, payload) => ({ ...payload, severity: metadata.severity }),
    write: async entry => { logs.push(entry); },
  }; }
}
const context = vm.createContext({
  Buffer, Date, setTimeout,
  console: { log() {}, error() {} },
  fetch: async () => ({ ok: true, text: async () => 'runtime@example.com' }),
});
const source = fs.readFileSync(process.argv[2], 'utf8') + '\nexport { buildStartupScript, buildFatalErrorMessagePrefix, buildPipelineCommand, writeCancelledStepLogs };';
const module = new vm.SourceTextModule(source, { context });
const imports = {
  'node:crypto': { default: crypto },
  googleapis: { google },
  '@google-cloud/functions-framework': { default: { cloudEvent: (_name, fn) => { handler = fn; } } },
  '@google-cloud/logging': { Logging },
};
await module.link(specifier => {
  const exports = imports[specifier];
  if (!exports) throw new Error(`Unexpected import: ${specifier}`);
  return new vm.SyntheticModule(Object.keys(exports), function () {
    for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
  }, { context });
});
await module.evaluate();
let result;
if (request.operation === 'handler') {
  await handler({ time: '2026-10-09T00:00:00Z', data: { message: {
    data: Buffer.from(JSON.stringify(request.payload)).toString('base64'),
    publishTime: '2026-10-09T00:00:00Z',
  } } });
} else {
  result = await module.namespace[request.operation](...request.args);
}
process.stdout.write(JSON.stringify({ result, logs, publications, inserts }));

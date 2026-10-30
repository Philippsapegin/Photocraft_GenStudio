import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

let extension, graph = { unsaved: 'first' }, fail = false, now = 1000;
const events = {}, posted = [], listeners = {};
const app = { registerExtension: e => extension = e, graphToPrompt: async () => {
    if (fail) throw Error('broken graph');
    return { output: graph };
} };
const api = { addEventListener: (id, fn) => events[id] = fn,
    fetchApi: async (path, options) => posted.push({ path, body: JSON.parse(options.body) }) };
const document = { visibilityState: 'visible', addEventListener: (id, fn) => listeners[id] = fn };
const context = vm.createContext({ document, window: { addEventListener: (id, fn) => listeners[id] = fn },
    Date: { now: () => now }, console });
const source = await readFile(new URL('../../comfyui/GenGate/web/active_workflow.js', import.meta.url), 'utf8');
const module = new vm.SourceTextModule(source, { context });
await module.link(async name => {
    const key = name.endsWith('app.js') ? 'app' : 'api';
    return new vm.SyntheticModule([key], function () { this.setExport(key, key === 'app' ? app : api); }, { context });
});
await module.evaluate();
extension.setup();
assert.equal(posted.length, 0);
const snapshot = events['gengate.workflow.request'];
const nonce = 'a'.repeat(32);
await snapshot({ detail: { nonce } });
assert.equal(posted[0].path, '/gengate/active-workflow');
assert.equal(posted[0].body.graph.unsaved, 'first');
graph = { unsaved: 'second' }; now = 2000; listeners.pointerdown();
await snapshot({ detail: { nonce } });
assert.equal(posted[1].body.graph.unsaved, 'second');
assert.equal(posted[1].body.activeAt, 2000);
document.visibilityState = 'hidden'; now = 3000; listeners.visibilitychange();
fail = true;
await snapshot({ detail: { nonce } });
assert.equal(posted[2].body.activeAt, 2000);
assert.match(posted[2].body.error, /broken graph/);
await snapshot({ detail: { nonce: 'stale' } });
assert.equal(posted.length, 3);

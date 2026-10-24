/** Synthetic DOM and loopback only; no CDP or live page acceptance. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash, webcrypto } from 'node:crypto';
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { startCursorPageChannel, createBootstrapLoader, createCursorPageRelay, makeRequest,
  validateRequest, runTaskContext, runCursorPageExample, main, CSP_REMINDER } from '../../examples/cursor_page_channel/cursor_page_host.mjs';
import { PAGE_ORIGIN, LIMITS, canonical, createPageAgentSource, executePageOperation } from '../../examples/cursor_page_channel/cursor_page_agent.mjs';
import { localTransport } from '../../adapters/generic/local_transport.mjs';
import * as online from '../../adapters/generic/online_host.mjs';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const session = 'synthetic-session-0001';
const temp = t => {
  const directory = mkdtempSync(join(tmpdir(), 'web-agent-channel-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  return directory;
};
async function channelFixture(t, options = {}) {
  const channel = await startCursorPageChannel({ responseTimeoutMs: 1500, ...options });
  t.after(() => channel.close());
  const headers = { Origin: PAGE_ORIGIN, Authorization: `Bearer ${channel.token}`,
    'X-Web-Agent-Session': session, 'Content-Type': 'application/json' };
  const http = (path, options = {}) => fetch(`http://127.0.0.1:${channel.port}${path}`,
    { ...options, headers: { ...headers, ...options.headers } });
  const connect = async () => {
    const poll = http('/next');
    await channel.waitForConnection();
    return { poll };
  };
  const post = value => http('/result', { method: 'POST', body: JSON.stringify(value) });
  return { channel, headers, http, connect, post };
}
const resultFor = (request, value = true) => ({ request_sha256: request.request_sha256, ok: true, value });

test('exact Origin, bearer token, method and validated preflight are required', async t => {
  const f = await channelFixture(t);
  for (const headers of [{ Origin: '' }, { Origin: 'null' }, { Origin: PAGE_ORIGIN + '/' },
    { Authorization: '' }, { Authorization: 'Bearer wrong' }]) {
    assert.equal((await f.http('/next', { headers })).status, 403);
  }
  const absent = await fetch(`http://127.0.0.1:${f.channel.port}/next`, { headers: { Authorization: f.headers.Authorization } });
  assert.equal(absent.status, 403);
  assert.equal((await f.http('/next', { method: 'POST' })).status, 405);
  const preflight = { 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'authorization, x-web-agent-session',
    'Access-Control-Request-Private-Network': 'true', Authorization: '' };
  const response = await f.http('/next', { method: 'OPTIONS', headers: preflight });
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('access-control-allow-origin'), PAGE_ORIGIN);
  assert.equal(response.headers.get('access-control-allow-private-network'), 'true');
  assert.match(response.headers.get('access-control-allow-headers'), /X-Web-Agent-Session/);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  for (const headers of [{}, { ...preflight, Origin: '' }, { ...preflight, 'Access-Control-Request-Method': 'DELETE' },
    { ...preflight, 'Access-Control-Request-Headers': 'authorization, x-arbitrary' }]) {
    assert.equal((await f.http('/next', { method: 'OPTIONS', headers })).status, 403);
  }
  assert.equal((await f.http('/unknown', { method: 'OPTIONS', headers: preflight })).status, 404);
});

test('random bootstrap is token-bound, no-store, single use and never written to disk', async t => {
  const a = await channelFixture(t), b = await channelFixture(t);
  assert.match(a.channel.token, /^[a-f0-9]{64}$/);
  assert.match(a.channel.bootstrapPath, /^\/bootstrap\/[a-f0-9]{48}$/);
  assert.notEqual(a.channel.bootstrapPath, b.channel.bootstrapPath);
  assert.notEqual(a.channel.token, b.channel.token);
  assert.equal((await a.http(a.channel.bootstrapPath, { headers: { Authorization: b.headers.Authorization } })).status, 403);
  assert.equal((await a.http(b.channel.bootstrapPath)).status, 404);
  const first = await a.http(a.channel.bootstrapPath);
  assert.equal(first.status, 200);
  assert.equal(first.headers.get('cache-control'), 'no-store');
  assert.match(await first.text(), /__webAgentCursorPageAgentV1/);
  assert.equal((await a.http(a.channel.bootstrapPath)).status, 410);
  const loader = createBootstrapLoader(a.channel);
  assert.ok(loader.length < 1000);
  assert.doesNotThrow(() => new Function(loader));
});

test('bootstrap rejects special characters, wrong token lengths and invalid ports', () => {
  const valid = { port: 12345, token: 'a'.repeat(64), bootstrapPath: '/bootstrap/' + 'b'.repeat(48) };
  for (const change of [{ port: '12345' }, { port: 0 }, { port: 65536 }, { token: 'a'.repeat(63) },
    { token: 'a'.repeat(64) + '";throw 1' }, { bootstrapPath: valid.bootstrapPath + '?x="' },
    { bootstrapPath: valid.bootstrapPath + '\n' }, { token: valid.token + '\n' },
    { bootstrapPath: '/bootstrap/\n' }, { token: null }]) assert.throws(() => createBootstrapLoader({ ...valid, ...change }), /INVALID/);
  assert.throws(() => createPageAgentSource({ ...valid, token: '"\\\n' }), /INVALID/);
  assert.throws(() => createPageAgentSource({ ...valid, port: '1);' }), /INVALID/);
});

test('session binding, canonical hashes, one delivery and duplicate result refusal', async t => {
  const f = await channelFixture(t);
  const { poll } = await f.connect();
  const request = makeRequest(1, 'url');
  assert.deepEqual(validateRequest(JSON.parse(JSON.stringify(request))), request);
  assert.throws(() => validateRequest({ ...request, op: 'evaluate' }), /HASH_MISMATCH/);
  assert.throws(() => makeRequest(1, 'network.fetch'), /INVALID/);
  const operation = f.channel.dispatch(request);
  assert.throws(() => f.channel.dispatch(makeRequest(2, 'url')), /PENDING/);
  const response = await poll;
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual(await response.json(), request);
  assert.equal((await f.http('/next', { headers: { 'X-Web-Agent-Session': 'synthetic-session-0002' } })).status, 409);
  assert.equal((await f.post({ ...resultFor(request), request_sha256: '0'.repeat(64) })).status, 409);
  assert.equal((await f.post({ request_sha256: request.request_sha256, ok: true })).status, 400);
  assert.equal((await f.post(resultFor(request, 'synthetic'))).status, 200);
  assert.equal((await operation).value, 'synthetic');
  assert.equal((await f.post(resultFor(request))).status, 409);
  assert.throws(() => f.channel.dispatch(request), /REPLAY/);
  assert.equal(makeRequest(2, 'evaluate', { b: 1, a: 2 }).request_sha256,
    makeRequest(2, 'evaluate', { a: 2, b: 1 }).request_sha256);
});

test('body, next and value limits reject oversized material without settling pending action', async t => {
  const f = await channelFixture(t);
  const { poll } = await f.connect(), request = makeRequest(1, 'url');
  const pending = f.channel.dispatch(request);
  pending.catch(() => {});
  await poll;
  assert.equal((await f.http('/result', { method: 'POST', body: 'x'.repeat(LIMITS.body + 1) })).status, 413);
  assert.equal((await f.post(resultFor(request, { large: 'x'.repeat(LIMITS.value) }))).status, 413);
  assert.throws(() => makeRequest(2, 'locator.fill', { value: 'x'.repeat(LIMITS.next) }), /NEXT_TOO_LARGE/);
  assert.equal((await f.post(resultFor(request, null))).status, 200);
  assert.equal((await pending).value, null);
});

test('response timeout and lost POST never redeliver or produce a retry request', async t => {
  const f = await channelFixture(t, { responseTimeoutMs: 100 });
  const { poll } = await f.connect();
  const directory = temp(t);
  const tab = createCursorPageRelay({ evidenceDirectory: directory, viewId: 'tab', browserId: 'browser', pageChannel: f.channel });
  const operation = tab.playwright.locator('button').click();
  const rejected = assert.rejects(operation, /RESPONSE_TIMEOUT/);
  assert.equal((await poll).status, 200); // Simulate the action happening; discard its POST.
  const duplicate = await f.http('/next');
  assert.notEqual(duplicate.status, 200);
  await rejected;
  await assert.rejects(tab.playwright.locator('button').click(), /RELAY_STOPPED/);
  const relay = join(directory, readdirSync(directory)[0]);
  const records = readdirSync(relay);
  assert.deepEqual(records, ['1.request.json']);
  const text = readFileSync(join(relay, records[0]), 'utf8');
  assert.ok(!text.includes(f.channel.token) && !text.includes(f.channel.bootstrapPath));
});

test('close settles connection waiters, result promises and long polls', async t => {
  const a = await channelFixture(t);
  const waiting = assert.rejects(a.channel.waitForConnection(), /CLOSED/);
  await a.channel.close(); await waiting;
  const b = await channelFixture(t);
  const { poll } = await b.connect();
  const pending = assert.rejects(b.channel.dispatch(makeRequest(1, 'url')), /CLOSED/);
  await poll;
  const next = b.http('/next');
  // Wait until the server owns the long poll, then close and flush its 204.
  await new Promise(resolve => setTimeout(resolve, 30));
  await b.channel.close();
  await pending;
  assert.equal((await next).status, 204);
});

test('timeouts have upper bounds and connection failure precedes receipt or relay creation', async t => {
  for (const options of [{ connectTimeoutMs: 300001 }, { responseTimeoutMs: 45001 }, { responseTimeoutMs: 0 }]) {
    await assert.rejects(startCursorPageChannel(options), /INVALID/);
  }
  const directory = temp(t), lines = [];
  const args = cliArguments(directory).concat(['--connect-timeout-ms', '20']);
  await assert.rejects(main(args, line => lines.push(JSON.parse(line))), /CONNECT_TIMEOUT/);
  assert.deepEqual(readdirSync(directory), []);
  assert.deepEqual(lines.map(line => line.status), ['PAGE_CHANNEL_READY', 'CSP_CLEANUP_REQUIRED']);
  assert.deepEqual(lines[0].csp_cleanup_required, CSP_REMINDER);
  assert.equal(Object.hasOwn(lines[1], 'bootstrap'), false);
});

test('lifecycle always closes on connection, business and output failures', async () => {
  for (const fail of ['connect', 'business', 'emit', 'start']) {
    const events = [], lines = [];
    const channel = { port: 12345, token: 'a'.repeat(64), bootstrapPath: '/bootstrap/' + 'b'.repeat(48),
      waitForConnection: async () => { events.push('connect'); if (fail === 'connect') throw Error('synthetic'); },
      close: async () => events.push('close') };
    await assert.rejects(runCursorPageExample({
      startChannel: async () => { if (fail === 'start') throw Error('synthetic'); return channel; },
      operation: async () => { events.push('business'); throw Error('synthetic'); },
      emit: line => { const value = JSON.parse(line); lines.push(value); if (fail === 'emit' && value.bootstrap) throw Error('synthetic'); },
    }), /synthetic/);
    assert.equal(events.includes('close'), fail !== 'start');
    assert.equal(lines.at(-1).status, 'CSP_CLEANUP_REQUIRED');
  }
});

test('follow preserves original tab/run and cumulative budget; recover never calls offload', async () => {
  for (const mode of ['offload', 'recover']) {
    let time = 0, sends = 0, recoveries = 0;
    const tab = {}, run = {}, budgets = [];
    const perform = async options => {
      assert.equal(options.tab, tab); assert.equal(options.run, run);
      budgets.push(options.budgetMs); time += options.budgetMs;
      return { status: 'HOST_TIMEOUT', doNotResend: true };
    };
    await runTaskContext({ mode, taskOptions: { tab, run }, budgetMs: 45, followMs: 100, now: () => time,
      online: { offloadTask: options => { sends++; return perform(options); },
        recoverTask: options => { recoveries++; return perform(options); } } });
    assert.equal(sends, mode === 'offload' ? 1 : 0);
    assert.equal(recoveries, mode === 'offload' ? 2 : 3);
    assert.deepEqual(budgets, [45, 45, 10]);
  }
  for (const outcome of ['SUBMISSION_UNKNOWN', 'TASK_DEADLINE', 'PREPARED_NOT_SENT', 'COLLECTED', 'PAGE_ERROR']
    .map(status => ({ status, doNotResend: true })).concat([{ status: 'HOST_TIMEOUT', doNotResend: false },
      { status: 'HOST_TIMEOUT', doNotResend: true, error: 'synthetic' }])) {
    assert.equal(await runTaskContext({ online: { offloadTask: async () => outcome,
      recoverTask: () => assert.fail('must stop') }, mode: 'offload', followMs: 100, taskOptions: {} }), outcome);
  }
  for (const followMs of [0, -1, 900001, NaN, 1.5]) await assert.rejects(runTaskContext({ mode: 'offload', followMs }), /FOLLOW_INVALID/);
});

class EventFixture { constructor(type, options) { this.type = type; Object.assign(this, options); } }
function editor({ paste = true, alter = lines => lines, blank = 'DIV' } = {}) {
  let lines = [''];
  const commands = [];
  const element = { isContentEditable: true, focus() {},
    get childNodes() { return alter([...lines]).map((textContent, index) => ({ textContent,
      nodeType: index ? 1 : 3, tagName: textContent ? 'DIV' : blank })); },
    get innerText() { assert.fail('must read top-level nodes'); },
    dispatchEvent(event) { if (paste && event.type === 'paste') lines = event.clipboardData.getData('text/plain').split('\n'); },
  };
  class Transfer { setData(type, value) { this[type] = value; } getData(type) { return this[type]; } }
  const environment = { document: { querySelectorAll: () => [element],
    createRange: () => ({ selectNodeContents() {} }), execCommand(name, _ui, value) {
      commands.push(name);
      if (name === 'delete') lines = [''];
      else if (name === 'insertText') lines[lines.length - 1] += value;
      else if (name === 'insertParagraph') lines.push('');
      else return false;
      return true;
    } }, getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
    window: { DataTransfer: Transfer, ClipboardEvent: EventFixture } };
  return { environment, element, commands };
}
const fillRequest = value => ({ op: 'locator.fill', params: { selector: '[contenteditable]', value } });
const lineCases = ['a\n\nb\n  c', '```diff\n context\n\n-  old\n+  new\n```', '| name | value |\n|---|---|\n| a | b |',
  'a\nb\n', '\nz', 'a\tb\n\n\nc'];
for (const [index, value] of lineCases.entries()) {
  for (const paste of [true, false]) test(`top-level line fidelity case ${index}, paste=${paste}`, async () => {
    const f = editor({ paste });
    assert.equal(await executePageOperation(fillRequest(value), f.environment), true);
    assert.deepEqual(f.element.childNodes.map(node => node.textContent), value.split('\n'));
    assert.equal(f.commands.length === 0, paste);
  });
  for (const [label, alter] of [['extra', lines => [...lines, '']], ['missing', lines => lines.slice(0, -1)],
    ['whitespace', lines => lines.map(line => line + ' ')]]) test(`readback rejects ${label}, case ${index}`, async () => {
    const f = editor({ alter });
    await assert.rejects(executePageOperation(fillRequest(value), f.environment), /FILL_VERIFICATION_FAILED/);
  });
}

test('standalone BR lines and value CRLF normalization; event mutation refuses fill', async () => {
  for (const paste of [true, false]) {
    const f = editor({ blank: 'BR', paste });
    await executePageOperation(fillRequest('a\r\n\r\nb\r\n'), f.environment);
    assert.equal(f.element.childNodes[1].tagName, 'BR');
    assert.deepEqual(f.element.childNodes.map(node => node.textContent), ['a', '', 'b', '']);
  }
  for (const mutate of [false, true]) {
    const element = { value: '', focus() {}, get childNodes() { assert.fail('value input'); },
      dispatchEvent(event) { assert.equal(event.data, 'a\nb\nc'); if (mutate) this.value += ' '; } };
    const operation = executePageOperation(fillRequest('a\r\nb\rc'), {
      document: { querySelectorAll: () => [element] }, window: { InputEvent: EventFixture } });
    if (mutate) await assert.rejects(operation, /FILL_VERIFICATION_FAILED/);
    else assert.equal(await operation, true);
  }
});

test('button uses full pointer sequence; menu uses click; unknown/ambiguous actions fail', async () => {
  const events = [], button = { innerText: 'synthetic', getAttribute: () => null, matches: () => true,
    dispatchEvent: event => events.push([event.type, event.buttons]), click: () => events.push(['click']) };
  const environment = { document: { querySelectorAll: () => [button] },
    window: { PointerEvent: EventFixture, MouseEvent: EventFixture } };
  await executePageOperation({ op: 'locator.click', params: { selector: 'button' } }, environment);
  assert.deepEqual(events, [['pointerdown', 1], ['mousedown', 1], ['pointerup', 0], ['mouseup', 0], ['click']]);
  events.length = 0;
  await executePageOperation({ op: 'role.click', params: { descriptor: { role: 'menuitem' } } }, environment);
  assert.deepEqual(events, [['click']]);
  await assert.rejects(executePageOperation({ op: 'network.fetch' }, environment), /REFUSED/);
  environment.document.querySelectorAll = () => [button, button];
  await assert.rejects(executePageOperation({ op: 'locator.click', params: { selector: 'button' } }, environment), /AMBIGUOUS/);
});

function realm(fetch, overrides = {}) {
  const frame = {};
  return { location: { origin: PAGE_ORIGIN, href: PAGE_ORIGIN + '/' }, top: frame, self: frame,
    document: { readyState: 'complete' }, crypto: webcrypto, TextEncoder, Uint8Array, AbortSignal, fetch, ...overrides };
}
const source = () => createPageAgentSource({ port: 12345, token: 'a'.repeat(64) });
const nextResponse = request => ({ ok: true, status: 200, text: async () => JSON.stringify(request) });
test('serialized executor runs in fresh vm and top-frame/origin/active markers gate injection', async () => {
  for (const overrides of [{ location: { origin: 'null' } }, { top: {}, self: {} },
    { __webAgentCursorPageAgentV1: { state: 'running' } }]) {
    await runInNewContext(source(), realm(() => assert.fail('must not fetch'), overrides));
  }
  const f = editor();
  const context = realm(async () => { throw Error('stop'); }, { ...f.environment,
    __webAgentCursorPageAgentV1: { state: 'failed', session: 'old' } });
  assert.equal(await runInNewContext(`(${executePageOperation.toString()})(${JSON.stringify(fillRequest('a\n\nb'))})`, context), true);
  await runInNewContext(source(), context);
  assert.notEqual(context.__webAgentCursorPageAgentV1.session, 'old');
  assert.equal(context.__webAgentCursorPageAgentV1.state, 'failed');
});

test('page verifies hash before action; lost result POST and repeated sequence do not replay', async () => {
  for (const scenario of ['bad-hash', 'lost-post', 'duplicate', 'oversized-value', 'unknown-op']) {
    let actions = 0, gets = 0, posts = 0;
    const request = makeRequest(1, 'locator.click', { selector: 'button' });
    if (scenario === 'bad-hash') request.request_sha256 = '0'.repeat(64);
    if (scenario === 'unknown-op') {
      request.op = 'network.fetch';
      const { request_sha256, ...unsigned } = request;
      request.request_sha256 = createHash('sha256').update(JSON.stringify(canonical(unsigned))).digest('hex');
    }
    const button = { getAttribute: () => null, matches: () => true, dispatchEvent() {}, click: () => actions++ };
    const context = realm(async (url, options) => {
      if (url.endsWith('/next')) { gets++; assert.ok(gets < 3); return nextResponse(request); }
      posts++;
      if (scenario === 'lost-post') throw Error('synthetic lost response');
      if (scenario === 'oversized-value') assert.equal(JSON.parse(options.body).error, 'PAGE_VALUE_TOO_LARGE');
      return { ok: true, json: async () => ({ accepted: true }) };
    }, { document: { readyState: 'complete', querySelectorAll: () => [button] },
      window: { PointerEvent: EventFixture, MouseEvent: EventFixture } });
    if (scenario === 'oversized-value') {
      Object.assign(request, makeRequest(1, 'evaluate', { expression: `"x".repeat(${LIMITS.value})` }));
    }
    await runInNewContext(source(), context);
    assert.equal(context.__webAgentCursorPageAgentV1.state, 'failed');
    assert.equal(actions, ['lost-post', 'duplicate'].includes(scenario) ? 1 : 0);
    assert.equal(posts, ['bad-hash', 'unknown-op'].includes(scenario) ? 0 : 1);
    assert.equal(gets, scenario === 'duplicate' ? 2 : 1);
  }
});

function cliArguments(directory) {
  return Object.entries({ mode: 'offload', 'web-agent-root': ROOT, 'evidence-directory': directory,
    ledger: join(directory, 'ledger'), receipt: join(directory, 'prepare.json'), 'task-file': join(directory, 'task.txt'),
    repository: 'example/repo', 'source-sha': 'a'.repeat(40), 'source-ref': 'main', 'browser-id': 'browser', 'tab-id': 'tab',
    model: 'Observed model', effort: 'Observed effort', 'ui-profile': 'chinese-ordinary-chat', orchestration: 'synthetic', kind: 'review',
  }).flatMap(([key, value]) => ['--' + key, value]);
}

test('CLI refuses in-checkout evidence and emits cleanup even on argument errors', async () => {
  const lines = [];
  await assert.rejects(main([], line => lines.push(JSON.parse(line))), /REQUIRED/);
  assert.equal(lines[0].status, 'CSP_CLEANUP_REQUIRED');
  await assert.rejects(main(cliArguments(ROOT), () => {}), /EVIDENCE_INSIDE_SOURCE/);
});

test('CLI packs through the existing module after connection and keeps credentials off disk', async t => {
  const directory = temp(t), spec = join(directory, 'pack-spec.json');
  writeFileSync(spec, JSON.stringify({ kind: 'review', goal: 'Synthetic goal', question: 'Synthetic question', sources: [] }));
  const args = cliArguments(directory), position = args.indexOf('--task-file');
  args.splice(position, 2, '--pack-spec', spec, '--source-root', directory);
  let connection, secret, bootstrapPath;
  const output = [];
  const emit = line => {
    const value = JSON.parse(line); output.push(value);
    if (value.status !== 'PAGE_CHANNEL_READY') return;
    connection = (async () => {
      let base, authorization;
      await assert.rejects(runInNewContext(value.bootstrap, { AbortSignal, fetch: async (url, options) => {
        const parsed = new URL(url);
        assert.equal(parsed.hostname, '127.0.0.1');
        base = parsed.origin; bootstrapPath = parsed.pathname;
        authorization = options.headers.Authorization; secret = authorization.slice(7);
        return { ok: false };
      } }), /BOOTSTRAP_REFUSED/);
      const headers = { Origin: PAGE_ORIGIN, Authorization: authorization, 'X-Web-Agent-Session': session,
        'Content-Type': 'application/json' };
      const request = await (await fetch(base + '/next', { headers })).json();
      assert.equal(request.op, 'role.click');
      await fetch(base + '/result', { method: 'POST', headers,
        body: JSON.stringify({ request_sha256: request.request_sha256, ok: false, error: 'SYNTHETIC_STOP' }) });
    })();
  };
  await assert.rejects(main(args, emit), /SYNTHETIC_STOP/);
  await connection;
  assert.match(readFileSync(join(directory, 'packed-task.txt'), 'utf8'), /Synthetic goal/);
  assert.equal(existsSync(join(directory, 'prepare.json')), false);
  const files = readdirSync(directory, { recursive: true }).filter(name => name.endsWith('.json') || name.endsWith('.txt'));
  for (const name of files) {
    const text = readFileSync(join(directory, name), 'utf8');
    assert.ok(!text.includes(secret) && !text.includes(bootstrapPath));
  }
  assert.equal(output.at(-1).status, 'CSP_CLEANUP_REQUIRED');
});

test('graceful interrupt closes a waiting host and emits the CSP reminder', async () => {
  const lines = [];
  await assert.rejects(runCursorPageExample({ startChannel: () => startCursorPageChannel(),
    operation: () => assert.fail('must not start task'), emit: line => {
      const value = JSON.parse(line); lines.push(value);
      if (value.status === 'PAGE_CHANNEL_READY') queueMicrotask(() => process.emit('SIGINT'));
    } }), /CLOSED/);
  assert.equal(lines.at(-1).status, 'CSP_CLEANUP_REQUIRED');
});

test('real ledger claim precedes relay fill/click; lost click result stays unknown and recovery cannot send', async t => {
  const directory = temp(t), taskFile = join(directory, 'task.txt');
  writeFileSync(taskFile, 'Synthetic read-only review.');
  const run = localTransport({ root: ROOT, evidenceDirectory: directory, ledger: join(directory, 'ledger'),
    receiptPath: join(directory, 'prepare.json') });
  const operations = [];
  const responses = [true, true, [{ index: 0 }], 'true', 'Observed model', true, 'Observed effort', true,
    { url: PAGE_ORIGIN + '/', chat: true, work: true, ready: true, login: false, selection_lines: ['Observed effort'] }];
  let clicks = 0;
  const pageChannel = { dispatch: async record => {
    operations.push(record.op);
    let value;
    if (record.seq <= responses.length) value = responses[record.seq - 1];
    else if (record.op === 'evaluate') value = responses.at(-1);
    else {
      const status = (await run('status', ['--receipt', run.receiptPath])).value;
      assert.equal(status.send_attempts, 1);
      if (record.op === 'locator.click') { clicks++; throw Error('PAGE_RESPONSE_TIMEOUT'); }
      value = true;
    }
    return resultFor(record, value);
  } };
  const tab = createCursorPageRelay({ evidenceDirectory: directory, viewId: 'tab', browserId: 'browser', pageChannel });
  const outcome = await runTaskContext({ online, mode: 'offload', followMs: 10000, taskOptions: {
    tab, run, evidenceDirectory: directory, repository: 'example/repo', sourceSha: 'a'.repeat(40), sourceRef: 'main',
    taskFile, orchestration: 'synthetic', browserId: 'browser', requestedModel: 'Observed model',
    requestedEffort: 'Observed effort', observe: online.observeNewChat, ...online.CHINESE_ORDINARY_CHAT,
  } });
  assert.equal(outcome.status, 'SUBMISSION_UNKNOWN'); assert.equal(outcome.doNotResend, true);
  assert.equal(clicks, 1);
  assert.equal(operations.filter(op => op === 'locator.fill').length, 1);
  const count = operations.length;
  const recovered = await online.recoverTask({ run, tab, budgetMs: 50 });
  assert.equal(recovered.doNotResend, true);
  assert.equal(operations.length, count);
  assert.equal((await run('status', ['--receipt', run.receiptPath])).value.send_attempts, 1);
  assert.ok(existsSync(run.receiptPath));
});

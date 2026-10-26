/** Synthetic browser regressions; not live browser acceptance. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { runInNewContext } from 'node:vm';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { copyFinalResponse, createLosslessAdapter, createLosslessCollection, readCopyControl } from '../../adapters/generic/lossless_response.mjs';

const text = '\n  中文 🧵 e\u0301\r\n```json\n{\n\t"path": "C:\\\\tmp\\\\a",\n  "escaped": "\\\\n\\\\t", "zero": 0\n}\n```\n\n```python\n\tif True:\n\t\tprint("a  b")  \n```\n';
const taskId = '00000000-0000-4000-8000-000000000001';
const request = { taskId, nonce: 'a'.repeat(32), requestSha256: 'b'.repeat(64) };
const binding = { task_id: taskId, nonce: request.nonce, request_sha256: request.requestSha256,
  browserId: 'iab', tabId: 'tab1', state: 'SUBMITTED', url: 'https://chatgpt.com/c/synthetic' };
const expected = { url: binding.url, status: 'FINAL_CANDIDATE', turnId: 'turn-1', responseIds: 'reply-1',
  fingerprint: 'abc', contentLength: 100 };
const initial = [{ presentationStyle: 'attachment', entries: [
  { mimeType: 'text/plain', text: '私人剪贴板' }, { mimeType: 'image/png', base64: 'AQIDBA==' }] }];
const clone = value => structuredClone(value);
function fixture(options = {}) {
  let clipboard = options.empty ? [] : clone(initial), clicks = 0, probes = 0;
  const events = [];
  const readPage = function readChatGPTPage() {};
  const tab = { id: binding.tabId, url: async () => options.url ?? binding.url,
    getAXState: async () => { events.push('ax'); },
    clipboard: {
      read: async () => { events.push('clipboard-read'); return clone(clipboard); },
      write: async items => {
        events.push('clipboard-restore');
        if (items.length === 0) throw Error('CUA rejects empty items');
        if (options.restoreFails) throw Error('restore failure');
        clipboard = clone(items);
      },
      writeText: async value => { events.push('sentinel'); clipboard = [{ entries: [{ mimeType: 'text/plain', text: value }] }]; },
    },
    playwright: {
      evaluate: async (fn, arg) => {
        if (fn === readCopyControl) { probes++; return { ok: !options.ambiguous, label: options.changedLabel && probes > 1 ? 'Copy' : (options.label ?? '复制') }; }
        assert.equal(fn, readPage); assert.equal(arg.includeText, false);
        if (options.concurrent && clicks) clipboard = [{ entries: [{ mimeType: 'text/plain', text: '用户后来复制的内容' }] }];
        return { ...expected, ...(options.changed && clicks ? { responseIds: 'reply-2' } : {}),
          ...(options.running ? { status: 'RUNNING' } : {}) };
      },
      locator: selector => {
        assert.equal(selector, `[data-turn-key="turn-1"] button[aria-label="${options.label ?? '复制'}"]:not([data-content-search-unit-key] button)`);
        return { click: async () => {
            clicks++; events.push('copy');
            if (options.delay) await new Promise(resolve => setTimeout(resolve, options.delay));
            if (options.clickFails) throw Error('click failure');
            if (!options.stale) clipboard = [{ entries: [{ mimeType: 'text/plain',
              ...(options.base64 ? { base64: Buffer.from(text).toString('base64') } : { text }) }] }];
        } };
      },
    },
  };
  return { tab, readPage, events, get clipboard() { return clipboard; }, get clicks() { return clicks; }, get probes() { return probes; } };
}
const copy = (f, overrides = {}) => copyFinalResponse({ tab: f.tab, readPage: f.readPage, request, binding, expected, ...overrides });

test('preserves exact markdown bytes, Unicode and CRLF; restores all clipboard formats', async () => {
  for (const base64 of [false, true]) {
    const f = fixture({ base64 }); const result = await copy(f);
    assert.equal(result.text, text);
    assert.equal(result.sha256, createHash('sha256').update(text).digest('hex'));
    assert.equal(result.clipboardRestoration, 'restored');
    assert.deepEqual(f.clipboard, initial); assert.equal(f.clicks, 1); assert.equal(f.probes, 2);
  }
});
test('empty original clipboard is cleared using empty text, without invalid write([])', async () => {
  const f = fixture({ empty: true });
  const result = await copy(f);
  assert.equal(result.text, text);
  assert.equal(result.clipboardRestoration, 'cleared_empty_clipboard');
  assert.deepEqual(f.clipboard, [{ entries: [{ mimeType: 'text/plain', text: '' }] }]);
  assert.equal(f.events.includes('clipboard-restore'), false);
});
test('Cursor page-local clipboard capture reports system restoration as not applicable', async () => {
  const f = fixture({ empty: true });
  f.tab.clipboard.systemRestorationApplicable = false;
  const result = await copy(f);
  assert.equal(result.text, text);
  assert.equal(result.clipboardRestoration, 'not_applicable_cursor_relay');
  assert.equal(f.events.includes('clipboard-restore'), false);
});
test('rejects identity, tab, conversation, active generation and ambiguous copy controls before copying', async () => {
  for (const key of ['taskId', 'nonce', 'requestSha256']) {
    const f = fixture(); await assert.rejects(copy(f, { request: { ...request, [key]: 'wrong' } }), /BINDING_MISMATCH/);
    assert.equal(f.clicks, 0);
  }
  const mismatch = fixture();
  await assert.rejects(copy(mismatch, { binding: { ...binding, tabId: 'other' } }), /BINDING_MISMATCH/);
  for (const [options, error] of [[{ url: binding.url + '-other' }, /CONVERSATION_MISMATCH/],
    [{ running: true }, /RESPONSE_CHANGED/], [{ ambiguous: true }, /COPY_CONTROL_AMBIGUOUS/]]) {
    const f = fixture(options); await assert.rejects(copy(f), error); assert.equal(f.clicks, 0);
    assert.deepEqual(f.clipboard, initial);
  }
});
test('stale clipboard, click failure or changed final reply never falls back to rendered text', async () => {
  for (const [options, error] of [[{ stale: true }, /COPY_DID_NOT_UPDATE/],
    [{ clickFails: true }, /click failure/], [{ changed: true }, /RESPONSE_CHANGED/]]) {
    const f = fixture(options); await assert.rejects(copy(f), error); assert.deepEqual(f.clipboard, initial);
  }
});
test('concurrent clipboard update survives cleanup; restore errors surface', async () => {
  const f = fixture({ concurrent: true }); const result = await copy(f);
  assert.equal(result.clipboardRestoration, 'skipped_concurrent_change');
  assert.equal(f.clipboard[0].entries[0].text, '用户后来复制的内容');
  await assert.rejects(copy(fixture({ restoreFails: true })), /RESTORE_FAILED/);
});
test('proxy intercepts only actual upstream reader includeText and demands exact single-use proof', async () => {
  const f = fixture(); const adapter = createLosslessAdapter({ ...f, getBinding: async () => binding });
  await adapter.tab.playwright.evaluate(f.readPage, { ...request, includeText: false });
  assert.equal(f.clicks, 0);
  const result = await adapter.tab.playwright.evaluate(f.readPage, { ...request, includeText: true });
  const collected = { ...result, status: 'PAGE_COMPLETE' };
  for (const key of ['text', 'task_id', 'nonce', 'request_sha256', 'browserId', 'tabId', 'turnId', 'responseIds', 'fingerprint', 'contentLength', 'url']) {
    assert.throws(() => adapter.assertCollection({ ...collected, [key]: 'wrong' }), /LOSSLESS/);
  }
  adapter.assertCollection(collected);
  assert.throws(() => adapter.assertCollection(collected), /PROOF_REQUIRED/);
  await adapter.drain();
});
test('deadline waits for unfinished UI cleanup and cannot produce a later collection proof', async () => {
  const f = fixture({ delay: 50 });
  const adapter = createLosslessAdapter({ ...f, getBinding: async () => binding });
  const pending = adapter.tab.playwright.evaluate(f.readPage, { ...request, includeText: true }, { timeoutMs: 15 });
  const handled = assert.rejects(pending, /LOSSLESS_TIMEOUT|LOSSLESS_CANCELLED/);
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(f.clicks, 1);
  await adapter.drain(); await handled;
  assert.throws(() => adapter.assertCollection({ ...expected, status: 'PAGE_COMPLETE', text }), /PROOF_REQUIRED/);
});
function domFixture({ buttons = [{ label: '复制' }], responseIds = 'reply-1', states = ['complete'], newer = false,
  duplicate = false, hidden = false } = {}) {
  const present = { closest: () => null, getClientRects: () => [1] };
  const reply = { ...present, getAttribute: key => key === 'data-content-search-unit-key' ? 'x:assistant' : responseIds };
  const controls = buttons.map(({ label, inContent, disabled, ariaDisabled, invisible, hidden }) => ({
    disabled, closest: selector => selector === '[data-content-search-unit-key]' ? (inContent ? reply : null)
      : hidden ? {} : null,
    getClientRects: () => invisible ? [] : [1],
    getAttribute: key => key === 'aria-label' ? label : key === 'aria-disabled' ? ariaDisabled : null,
  }));
  const turn = { ...present, closest: () => hidden ? {} : null, getAttribute: () => 'turn-1', querySelectorAll: selector => {
    if (selector === '[data-content-search-unit-key]') return [reply];
    if (selector === '[data-talvt-turn-state]') return states.map(state => ({ getAttribute: () => state }));
    assert.equal(selector, 'button[aria-label]');
    return controls;
  } };
  const turns = [turn, ...(duplicate ? [turn] : []), ...(newer ? [{ ...turn, getAttribute: () => 'newer' }] : [])];
  return { querySelectorAll: selector => { assert.equal(selector, '[data-turn-key]'); return turns; } };
}
const probeInRealm = options => JSON.parse(JSON.stringify(runInNewContext(
  `(${readCopyControl.toString()})(${JSON.stringify(expected)})`, { document: domFixture(options) })));

test('serialized copy probe excludes content controls and supports exact Chinese and English labels', () => {
  for (const label of ['复制', 'Copy']) {
    assert.deepEqual(probeInRealm({ buttons: [{ label, inContent: true }, { label }] }), { ok: true, label });
    assert.deepEqual(probeInRealm({ buttons: [{ label: ' ' + label + ' ' }] }), { ok: true, label });
    for (const buttons of [[{ label }, { label }], [{ label, inContent: true }],
      [{ label: '复制' }, { label: 'Copy' }], [{ label: 'Copy code' }], [{ label: '复制消息' }],
      [{ label: 'copy' }], [], [{ label, disabled: true }], [{ label, ariaDisabled: 'true' }],
      [{ label, invisible: true }], [{ label, hidden: true }]]) {
      assert.deepEqual(probeInRealm({ buttons }), { ok: false, label: null });
    }
  }
});
test('serialized copy probe rejects newer/duplicate/hidden turns, wrong response and non-complete states', () => {
  for (const options of [{ newer: true }, { duplicate: true }, { hidden: true }, { responseIds: 'reply-2' },
    { states: [] }, { states: ['complete', 'complete'] }, { states: ['running'] }]) {
    assert.deepEqual(probeInRealm(options), { ok: false, label: null });
  }
});
test('failure diagnostics identify the stage without copying error text or response contents', async () => {
  const f = fixture();
  const adapter = createLosslessAdapter({ ...f, getBinding: async () => { throw Error('private native error text'); } });
  await assert.rejects(adapter.tab.playwright.evaluate(f.readPage, { ...request, includeText: true }));
  assert.equal(adapter.lastFailure.stage, 'read_ledger_binding');
  assert.equal(adapter.lastFailure.code, 'CUA_OPERATION_FAILED');
  assert.equal(JSON.stringify(adapter.lastFailure).includes('private'), false);
  assert.equal(f.clicks, 0);
  await adapter.drain();
});

test('copy uses the exact locator once, and refuses changed or untrusted labels before clicking', async () => {
  for (const label of ['复制', 'Copy']) {
    const f = fixture({ label });
    f.tab.playwright.getByRole = () => assert.fail('role lookup must not be used');
    await copy(f);
    assert.equal(f.clicks, 1);
  }
  for (const options of [{ changedLabel: true }, { label: 'Copy code' }, { label: '复制"' }]) {
    const f = fixture(options);
    await assert.rejects(copy(f), /COPY_CONTROL_AMBIGUOUS/);
    assert.equal(f.clicks, 0);
    assert.deepEqual(f.clipboard, initial);
  }
});

function collectionFixture(t, reportOverride) {
  const directory = mkdtempSync(join(tmpdir(), 'web-agent-lossless-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const calls = [], f = fixture();
  const run = async (...args) => {
    calls.push(args);
    if (args[0] !== 'status') return { code: 0, value: 'forwarded' };
    if (reportOverride instanceof Error) throw reportOverride;
    return reportOverride ?? { code: 0, value: { ...binding, session_url: binding.url } };
  };
  run.receiptPath = join(directory, 'prepare.json');
  writeFileSync(run.receiptPath, JSON.stringify({ receipt: 'synthetic', prompt: 'Synthetic prompt',
    task_id: taskId, request_sha256: request.requestSha256,
    browser_target: { browser_context: binding.browserId, tab_id: binding.tabId } }));
  return { ...f, calls, run, collection: createLosslessCollection({ ...f, run }) };
}

test('collection entry guards original transport with matching single-use proof and preserves receiptPath', async t => {
  const f = collectionFixture(t), c = f.collection;
  assert.equal(c.run.receiptPath, f.run.receiptPath);
  await assert.rejects(c.run('collect-browser', [], { result: {} }), /PROOF_REQUIRED/);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(await c.run('other', ['synthetic'], { observation: {} }), { code: 0, value: 'forwarded' });
  assert.deepEqual(f.calls[0], ['other', ['synthetic'], { observation: {} }]);
  const result = await c.tab.playwright.evaluate(f.readPage, { ...request, includeText: true });
  assert.deepEqual(f.calls[1], ['status', ['--receipt', f.run.receiptPath]]);
  const files = { result: { ...result, status: 'PAGE_COMPLETE' } };
  await c.run('collect-browser', ['--original-receipt', c.run.receiptPath], files);
  assert.strictEqual(f.calls[2][2], files);
  await assert.rejects(c.run('collect-browser', [], files), /PROOF_REQUIRED/);
  assert.equal(f.calls.length, 3);
  assert.equal(c.lastFailure, null);
  await c.drain();
});

test('collection entry rejects failed status and mismatched original task or request hash', async t => {
  for (const report of [{ code: 1 }, { code: 0, value: null }, Error('Synthetic status failure'),
    { code: 0, value: { ...binding, task_id: 'wrong' } },
    { code: 0, value: { ...binding, request_sha256: 'wrong' } }]) {
    const f = collectionFixture(t, report);
    await assert.rejects(f.collection.tab.playwright.evaluate(f.readPage, { ...request, includeText: true }),
      /ORIGINAL_OFFLOAD_TASK_REQUIRED/);
    assert.equal(f.collection.lastFailure.stage, 'read_ledger_binding');
    assert.equal(f.collection.lastFailure.code, 'ORIGINAL_OFFLOAD_TASK_REQUIRED');
    assert.equal(f.events.length, 0);
    await f.collection.drain();
  }
});

test('singleton clipboard guard and cancellation prevent overlapping copy actions', async () => {
  const f = fixture(), other = fixture();
  let release, entered;
  const started = new Promise(resolve => { entered = resolve; });
  f.tab.getAXState = () => { entered(); return new Promise(resolve => { release = resolve; }); };
  const pending = copy(f);
  await started;
  await assert.rejects(copy(other), /CLIPBOARD_BUSY/);
  assert.equal(other.clicks, 0);
  release(); await pending;
  const controller = new AbortController(); controller.abort();
  await assert.rejects(copy(other, { signal: controller.signal }), /CANCELLED/);
  assert.equal(other.clicks, 0);
  for (const timeoutMs of [0, -1, NaN, 10001]) await assert.rejects(copy(other, { timeoutMs }), /TIMEOUT_INVALID/);
  await copy(other);
  assert.equal(other.clicks, 1);
});

test('clipboard sentinel verification and unique plain text prevent stale or ambiguous collection', async () => {
  const f = fixture();
  f.tab.clipboard.writeText = async value => { assert.match(value, /^web-agent-lossless-/); };
  await assert.rejects(copy(f), /CONCURRENT_CHANGE/);
  assert.equal(f.clicks, 0);
  for (const entries of [[], [{ mimeType: 'text/plain', text }, { mimeType: 'text/plain', text }],
    [{ mimeType: 'text/plain' }]]) {
    const g = fixture();
    const read = g.tab.clipboard.read;
    g.tab.clipboard.read = async () => g.clicks ? [{ entries }] : read();
    await assert.rejects(copy(g), /CLIPBOARD_TEXT_AMBIGUOUS|CLIPBOARD_TEXT_MISSING/);
    assert.equal(g.clicks, 1);
  }
});

test('restoration failure retains the primary failure and structured diagnostics', async () => {
  const f = fixture({ stale: true, restoreFails: true });
  await assert.rejects(copy(f), error => {
    assert.equal(error.message, 'LOSSLESS_COPY_DID_NOT_UPDATE');
    assert.equal(error.losslessDiagnostic.primaryCode, error.message);
    assert.equal(error.losslessDiagnostic.restoreStage, 'restore_saved_items');
    assert.equal(error.losslessDiagnostic.savedItemCount, 1);
    assert.equal(JSON.stringify(error.losslessDiagnostic).includes('restore failure'), false);
    return true;
  });
});

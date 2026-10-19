import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { chooseBrowser, connectCua, CompletionTracker, readChatGPTPage } from '../../adapters/generic/browser_host.mjs';

const url = 'https://chatgpt.com/c/synthetic';
const request = { taskId: '11111111-1111-1111-1111-111111111111', nonce: 'a'.repeat(32), requestSha256: 'b'.repeat(64) };
const inventory = [{ id: 'chrome', type: 'extension' }, { id: 'inside', type: 'iab' }];
const final = { url, status: 'FINAL_CANDIDATE', turnId: 'turn', responseIds: 'reply', fingerprint: 'abc', contentLength: 100 };
function host(snapshot = final, browsers = inventory) {
  const calls = [];
  return { calls, async listBrowsers() { return browsers; }, async listTabs(o) { calls.push(o.browser); return [{ id: 'tab', url }]; },
    async getTab(id, o) { calls.push(o.browser); return { id, playwright: { evaluate: async () => typeof snapshot === 'function' ? snapshot() : snapshot } }; },
    async createBrowserTab() { throw Error('unexpected creation'); } };
}
test('built-in wins over first external browser', () => assert.equal(chooseBrowser(inventory).id, 'inside'));
test('explicit external browser is honored', () => assert.equal(chooseBrowser(inventory, { browserId: 'chrome' }).id, 'chrome'));
test('no silent external fallback', () => assert.throws(() => chooseBrowser(inventory.slice(0, 1)), /BUILT_IN_UNAVAILABLE/));
test('fallback needs a reason', () => assert.throws(() => chooseBrowser(inventory.slice(0, 1), { allowExternal: true }), /BUILT_IN_UNAVAILABLE/));
test('explicit reason allows sole fallback', () => assert.equal(chooseBrowser(inventory.slice(0, 1), { allowExternal: true, fallbackReason: 'authorized fallback' }).id, 'chrome'));
test('ambiguous built-in fails', () => assert.throws(() => chooseBrowser([...inventory, { id: 'other', type: 'iab' }]), /AMBIGUOUS/));
test('MCP apps are not browsers for this purpose', () => assert.throws(() => chooseBrowser([{ id: 'apps', type: 'mcpapps' }]), /UNAVAILABLE/));
test('duplicate provider IDs fail', () => assert.throws(() => chooseBrowser([...inventory, inventory[0]]), /INVALID_BROWSER_INVENTORY/));
test('actual routing passes selected ID to both calls', async () => { const h = host(); await connectCua(h, { url }); assert.deepEqual(h.calls, ['inside', 'inside']); });
test('tab resume requires provider identity', async () => await assert.rejects(connectCua(host(), { url, tabId: 'tab' }), /BOUND_BROWSER_REQUIRED/));
test('wrong origin rejected before browser use', async () => await assert.rejects(connectCua(host(), { url: 'https://example.com/c/test' }), /INVALID_CONVERSATION/));
test('provider failure never retries Chrome', async () => { const h = host(); h.getTab = async () => { throw Error('gone'); }; await assert.rejects(connectCua(h, { url })); assert.deepEqual(h.calls, ['inside']); });
test('duplicate conversation tabs require explicit selection', async () => { const h = host(); h.listTabs = async () => [{ id: 'a', url }, { id: 'b', url }]; await assert.rejects(connectCua(h, { url }), /AMBIGUOUS_TAB/); });
test('missing bound tab cannot be recreated', async () => { const h = host(); h.listTabs = async () => []; await assert.rejects(connectCua(h, { url, browserId: 'inside', tabId: 'missing', createIfMissing: true }), /BOUND_TAB_NOT_FOUND/); });
test('explicit creation opens only selected built-in', async () => { const h = host(); h.listTabs = async () => []; h.createBrowserTab = async (id, target, options) => { assert.equal(id, 'inside'); assert.equal(target, url); assert.deepEqual(options, { visible: false }); return { id: 'new', playwright: { evaluate: async () => final } }; }; assert.equal((await connectCua(h, { url, createIfMissing: true })).binding.tabId, 'new'); });
test('changed URL is not completion', async () => { const s = await connectCua(host({ ...final, url: 'https://chatgpt.com/g/other/c/synthetic' }), { url }); assert.equal((await s.observe(request)).status, 'TARGET_CHANGED'); });
test('invalid identity fails before reading', async () => { const s = await connectCua(host(() => { throw Error('must not read'); }), { url }); await assert.rejects(s.observe({ ...request, taskId: '-'.repeat(36) }), /INVALID_REQUEST/); });
test('two stable observations required', () => { const t = new CompletionTracker(); assert.equal(t.update(final, 1).status, 'SETTLING'); assert.equal(t.update(final, 2001).status, 'PAGE_COMPLETE'); });
test('changing output resets quiet window', () => { const t = new CompletionTracker(); t.update(final, 1); assert.equal(t.update({ ...final, fingerprint: 'changed' }, 2001).status, 'SETTLING'); });
test('running resets candidate', () => { const t = new CompletionTracker(); t.update(final, 1); t.update({ status: 'RUNNING' }, 2001); assert.equal(t.update(final, 4001).status, 'SETTLING'); });
test('malformed final cannot complete', () => { const t = new CompletionTracker(); assert.equal(t.update({ status: 'FINAL_CANDIDATE' }, 1).status, 'UNKNOWN'); });
test('nonmonotonic time rejected', () => { const t = new CompletionTracker(); t.update(final, 1); assert.throws(() => t.update(final, 1), /MONOTONIC/); });
test('expired deadline makes zero reads', async () => { const s = await connectCua(host(() => { throw Error('read'); }), { url }); const r = await s.wait(request, { deadlineAt: Date.now() - 1 }); assert.equal(r.status, 'TASK_DEADLINE'); assert.equal(r.checks, 0); });
test('hung read bounded and no resend', async () => { const s = await connectCua(host(() => new Promise(() => {})), { url }); const r = await s.wait(request, { budgetMs: 25, deadlineAt: Date.now() + 10000 }); assert.equal(r.status, 'HOST_TIMEOUT'); assert.equal(r.doNotResend, true); });
test('cancelled wait', async () => { const s = await connectCua(host(), { url }); const c = new AbortController(); c.abort(); assert.equal((await s.wait(request, { deadlineAt: Date.now() + 10000, signal: c.signal })).status, 'CANCELLED'); });
test('deadline caps a hung provider read', async () => { const s = await connectCua(host(() => new Promise(() => {})), { url }); const r = await s.wait(request, { budgetMs: 10000, deadlineAt: Date.now() + 30 }); assert.equal(r.status, 'TASK_DEADLINE'); });
test('stable completion never grants delivery or approval', async () => { const s = await connectCua(host(), { url }); const r = await s.wait(request, { quietMs: 1000, budgetMs: 3000, deadlineAt: Date.now() + 10000 }); assert.equal(r.status, 'PAGE_COMPLETE'); assert.equal(r.reviewApproved, false); assert.equal(r.deliveryVerified, false); assert.equal(r.sendAuthorized, false); });

// DOM-contract fixtures execute the actual page-reader function, not a second classifier.
function element(attrs = {}, text = '', selectors = {}) {
  return { textContent: text, innerText: text, innerHTML: text, getAttribute: k => attrs[k] ?? null,
    closest: () => null, getClientRects: () => [1], querySelectorAll: s => selectors[s] ?? [] };
}
function readFixture({ state = 'complete', stop = false, alert = false, login = false, wrong = false, newer = false, empty = false, unsupported = false, attachment = false, staleHome = false, includeText = false } = {}) {
  const controls = [];
  if (stop) controls.push(element({ 'aria-label': '停止生成' }));
  if (login) controls.push(element({ 'aria-label': 'Log in' }));
  const user = element({ 'data-content-search-unit-key': '0:user' }, `task_id: ${wrong ? 'wrong' : request.taskId}\nnonce: ${request.nonce}\nrequest_sha256: ${request.requestSha256}`);
  const reply = element({ 'data-content-search-unit-key': '1:assistant', 'data-chatgpt-search-message-ids': 'reply' }, empty || attachment ? '' : 'Answer', { button: attachment ? [element({ 'aria-label': '下载文件' })] : [] });
  const turn = element({ 'data-turn-key': 'turn' }, '', { '[data-content-search-unit-key]': [user, reply], '[data-talvt-turn-state]': [element({ 'data-talvt-turn-state': state })] });
  const turns = unsupported ? [] : newer ? [turn, element()] : [turn];
  const main = element({}, '', { 'button,[role="button"]': controls, '[data-turn-key]': turns, '[role="alert"]': alert ? [element({}, 'Error')] : [] });
  turn.closest = s => s === 'main' ? main : null;
  return vm.runInNewContext(`(${readChatGPTPage.toString()})(request)`, { document: { querySelector: () => staleHome ? element() : main, querySelectorAll: s => s === '[data-turn-key]' ? turns : [] }, location: { href: url }, request: { ...request, includeText } });
}
for (const [name, input, expected] of [
  ['final DOM', {}, 'FINAL_CANDIDATE'], ['streaming', { state: 'streaming' }, 'RUNNING'],
  ['stop overrides complete', { stop: true }, 'RUNNING'], ['error overrides complete', { alert: true }, 'PAGE_ERROR'],
  ['interrupted', { state: 'interrupted' }, 'PAGE_ERROR'], ['login', { login: true }, 'LOGIN_REQUIRED'],
  ['wrong request', { wrong: true }, 'UNKNOWN'], ['newer turn', { newer: true }, 'TARGET_CHANGED'],
  ['empty reply', { empty: true }, 'UNKNOWN'], ['unknown layout', { unsupported: true }, 'UNKNOWN'],
  ['attachment-only', { attachment: true }, 'FINAL_CANDIDATE'], ['unrecognized state', { state: 'future-state' }, 'UNKNOWN'],
  ['stale home main does not hide current conversation', { staleHome: true }, 'FINAL_CANDIDATE'],
  ['current in-progress state is running', { state: 'in_progress' }, 'RUNNING'],
]) test(name, () => assert.equal(readFixture(input).status, expected));

test('polling does not expose body; explicit collection does', () => {
  assert.equal(readFixture().text, undefined);
  assert.equal(readFixture({ includeText: true }).text, 'Answer');
});

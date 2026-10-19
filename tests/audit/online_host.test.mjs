import test from 'node:test';
import assert from 'node:assert/strict';
import { sendOnce } from '../../adapters/generic/online_host.mjs';
import { localTransport, prepareOnline, loadPrepared, readCollected } from '../../adapters/generic/local_transport.mjs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readChatGPTPage } from '../../adapters/generic/browser_host.mjs';
import { offloadTask, recoverTask, sendPrepared, waitAndCollect, readOnlyNewChat, CHINESE_ORDINARY_CHAT } from '../../adapters/generic/online_host.mjs';

function fixture() {
  const events = [];
  const target = { tab_id: 'tab', mode: 'CHAT', model_label: 'Observed', effort_label: 'Observed' };
  const tab = { id: 'tab', playwright: { locator: selector => ({
    fill: async text => events.push(['fill', text]), click: async () => events.push(['click', selector]),
  }) } };
  const prepared = { task_id: 'synthetic', prompt: 'immutable prompt', browser_target: target };
  const observe = async () => ({ ...target, composer_ready: true, logged_in: true, selection_unambiguous: true });
  const run = async command => {
    events.push([command]);
    return { code: 0, value: command === 'prompt' ? prepared.prompt :
      { claimed: true, task_id: prepared.task_id, state: 'PREPARED' } };
  };
  run.receiptPath = 'synthetic-receipt';
  return { tab, prepared, run, observe, composer: 'observed-composer', sendButton: 'observed-send', events };
}

test('durable claim precedes fill and exactly one send', async () => {
  const f = fixture(); await sendOnce(f);
  assert.deepEqual(f.events.map(x => x[0]), ['status', 'prompt', 'claim', 'fill', 'click']);
});
test('refused claim never touches composer', async () => {
  const f = fixture(); const original = f.run;
  f.run = async command => command === 'claim' ? { code: 2, value: { claimed: false } } : original(command);
  f.run.receiptPath = 'synthetic-receipt';
  await assert.rejects(sendOnce(f), /SEND_NOT_CLAIMED/); assert.deepEqual(f.events.map(x => x[0]), ['status', 'prompt']);
});
test('page change after claim records uncertainty and never sends', async () => {
  const f = fixture(); let calls = 0; const original = f.observe;
  f.observe = async () => ({ ...await original(), mode: ++calls === 1 ? 'CHAT' : 'WORK' });
  await assert.rejects(sendOnce(f), /TARGET_CHANGED/);
  assert.deepEqual(f.events.map(x => x[0]), ['status', 'prompt', 'claim', 'mark-unknown']);
});
test('lost click response never retries', async () => {
  const f = fixture(); let clicks = 0;
  f.tab.playwright.locator = () => ({fill: async () => {}, click: async () => { clicks++; throw Error('lost'); }});
  await assert.rejects(sendOnce(f), /lost/); assert.equal(clicks, 1);
  assert.equal(f.events.at(-1)[0], 'mark-unknown');
});

test('tampered cached prompt is refused before claim', async () => {
  const f = fixture(); const original = f.run;
  f.run = async command => command === 'prompt' ? { code: 0, value: 'different immutable text' } : original(command);
  f.run.receiptPath = 'synthetic-receipt';
  await assert.rejects(sendOnce(f), /IMMUTABLE_PROMPT_MISMATCH/);
  assert.deepEqual(f.events.map(x => x[0]), ['status']);
});

function pythonLauncher() {
  for (const [python, pythonArgs] of [['py', ['-3.12']], ['python3', []], ['python', []]]) {
    const probe = spawnSync(python, [...pythonArgs, '-c', 'import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)'], { encoding: 'utf8' });
    if (probe.status === 0) return { python, pythonArgs };
  }
  throw Error('PYTHON_3_12_REQUIRED');
}

test('recheck runs after the claim and the menu observation is not repeated', async () => {
  const f = fixture();
  let observes = 0;
  const original = f.observe;
  f.observe = async () => { observes += 1; return original(); };
  f.recheck = async () => ({ ...await original(), composer_ready: true, logged_in: true, selection_unambiguous: true });
  await sendOnce(f);
  assert.equal(observes, 1);
  assert.deepEqual(f.events.map(x => x[0]), ['status', 'prompt', 'claim', 'fill', 'click']);
});

test('local transport prepares one original receipt and preserves exact UTF-8 prompt', async t => {
  const evidenceDirectory = mkdtempSync(join(tmpdir(), 'web-agent-transport-'));
  t.after(() => rmSync(evidenceDirectory, { recursive: true }));
  const taskFile = join(evidenceDirectory, 'task.txt');
  writeFileSync(taskFile, '合成任务：审查零值处理。\r\n', 'utf8');
  const ledger = join(evidenceDirectory, 'ledger');
  const receiptPath = join(evidenceDirectory, 'prepare.json');
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const run = localTransport({ root, ledger, evidenceDirectory, receiptPath, ...pythonLauncher() });
  const observation = { mode: 'CHAT', project_id: null, conversation_id: 'NEW', browser_context: 'synthetic',
    tab_id: 'synthetic', model_label: 'Observed model', effort_label: 'Observed effort',
    selection_evidence: { mode: 'CHAT', observed_at: Date.now()/1000, logged_in: true,
      permission_confirmed: true, selection_unambiguous: true, locked: false, evidence_ref: 'synthetic',
      control_kind: 'menu', steps: [{ model_label: 'Observed model', effort_label: 'Observed effort' }] } };
  const args = { run, observation, evidenceDirectory, repository: 'example/repo', sourceSha: 'a'.repeat(40),
    sourceRef: 'main', taskFile, orchestration: 'synthetic' };
  const prepared = await prepareOnline(args);
  assert.equal(loadPrepared(receiptPath).task_id, prepared.task_id);
  assert.equal((await run('prompt', ['--task', prepared.task_id])).value, prepared.prompt);
  await assert.rejects(prepareOnline(args), /RECEIPT_EXISTS_USE_RECOVERY/);
  assert.equal(Object.keys(JSON.parse(readFileSync(join(ledger, 'ledger.json'), 'utf8')).tasks).length, 1);
});

test('result read checks identity and hash, limits context and never sends', async () => {
  const result = { status: 'PAGE_COMPLETE', task_id: 'synthetic', nonce: 'nonce', request_sha256: 'hash',
    url: 'https://chatgpt.com/c/synthetic', text: 'A useful result' };
  result.sha256 = createHash('sha256').update(result.text).digest('hex');
  const task = { task_id: result.task_id, nonce: result.nonce, request_sha256: result.request_sha256,
    session_url: result.url, state: 'RESULT_SEEN', send_attempts: 1, browser_result: result };
  const calls = [];
  const run = async (command, args) => { calls.push([command, args]); return { code: 0, value: task }; };
  run.receiptPath = 'original-receipt';
  const excerpt = await readCollected(run, { maxCharacters: 4 });
  assert.equal(excerpt.text, 'A us'); assert.equal(excerpt.truncated, true);
  assert.deepEqual(calls, [['status', ['--receipt', 'original-receipt']]]);
  result.text = 'tampered';
  await assert.rejects(readCollected(run), /HASH_MISMATCH/);
  result.nonce = 'other';
  await assert.rejects(readCollected(run), /NOT_AVAILABLE/);
});

const TASK = '11111111-1111-1111-1111-111111111111';
const NONCE = 'a'.repeat(32);
const HASH = 'b'.repeat(64);
const FINAL_URL = 'https://chatgpt.com/c/synthetic';
const conversation = suffix => 'https://chatgpt.com/' + 'c/' + suffix;

function statusValue(overrides = {}) {
  return { task_id: TASK, state: 'SUBMISSION_UNKNOWN', nonce: NONCE, request_sha256: HASH,
    deadline_at: Date.now() / 1000 + 3600, send_attempts: 1, ...overrides };
}
function preparedTarget() {
  return { task_id: TASK, prompt: 'prompt', request_sha256: HASH, browser_target: {
    mode: 'CHAT', project_id: null, conversation_id: 'NEW', browser_context: 'browser', tab_id: 'tab',
    model_label: 'Observed', effort_label: 'Pro' } };
}
function finalSnap(url = FINAL_URL, extra = {}) {
  return { url, status: 'FINAL_CANDIDATE', turnId: 'turn', responseIds: 'reply', fingerprint: 'abc',
    contentLength: 20, text: 'Collected answer', ...extra };
}
function scriptedRun(status = statusValue(), collect = { code: 0, value: { delivery_integrity: 'BROWSER_OBSERVED', review_approved: false } }) {
  const commands = [];
  const run = async (command, args) => {
    commands.push([command, args]);
    if (command === 'status') return { code: 0, value: status };
    if (command === 'mark-submitted') return { code: 0, value: { state: 'SUBMITTED' } };
    if (command === 'collect-browser') return collect;
    throw Error(command);
  };
  run.receiptPath = 'original-receipt';
  return { run, commands };
}
function scriptedTab({ url, evaluate }) {
  return { id: 'tab', url, playwright: { evaluate } };
}
const quick = { budgetMs: 8000, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000 };

test('collection waits for a stable conversation and stores that URL once', async () => {
  let reads = 0;
  const tab = scriptedTab({
    url: async () => (reads++ < 1 ? conversation('temporary') : FINAL_URL),
    evaluate: async () => reads < 3
      ? finalSnap(conversation('other'))
      : finalSnap(FINAL_URL),
  });
  const { run, commands } = scriptedRun();
  const outcome = await waitAndCollect({ tab, prepared: preparedTarget(), run, ...quick });
  assert.equal(outcome.status, 'COLLECTED');
  assert.equal(outcome.doNotResend, true);
  assert.deepEqual(commands.map(item => item[0]), ['status', 'mark-submitted', 'collect-browser']);
  assert.equal(commands[1][1][3], FINAL_URL);
  assert.equal(commands.some(item => item[0] === 'claim'), false);
});

test('login, missing tab, home page and expired deadline do not resend or mark submitted', async () => {
  const cases = [
    ['LOGIN_REQUIRED', scriptedTab({ url: async () => FINAL_URL, evaluate: async () => ({ url: FINAL_URL, status: 'LOGIN_REQUIRED' }) }), statusValue()],
    ['TAB_GONE', scriptedTab({ url: async () => { throw Error('gone'); }, evaluate: async () => { throw Error('unread'); } }), statusValue()],
    ['HOST_TIMEOUT', scriptedTab({ url: async () => 'https://chatgpt.com/', evaluate: async () => { throw Error('unread'); } }), statusValue()],
    ['TASK_DEADLINE', scriptedTab({ url: async () => { throw Error('unread'); }, evaluate: async () => { throw Error('unread'); } }), statusValue({ deadline_at: Date.now() / 1000 - 5 })],
  ];
  for (const [expected, tab, status] of cases) {
    const { run, commands } = scriptedRun(status);
    const outcome = await waitAndCollect({ tab, prepared: preparedTarget(), run, budgetMs: expected === 'HOST_TIMEOUT' ? 40 : 8000, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000 });
    assert.equal(outcome.status, expected, expected);
    assert.equal(outcome.doNotResend, true);
    assert.deepEqual(commands.map(item => item[0]), ['status']);
  }
});

test('a changed body or rejected identity is not stored and does not authorize another send', async () => {
  let reads = 0;
  const changed = scriptedTab({
    url: async () => FINAL_URL,
    evaluate: async () => finalSnap(FINAL_URL, ++reads >= 3 ? { fingerprint: 'changed', text: 'other body' } : {}),
  });
  const first = scriptedRun();
  const mismatch = await waitAndCollect({ tab: changed, prepared: preparedTarget(), run: first.run, ...quick });
  assert.equal(mismatch.status, 'RESPONSE_CHANGED');
  assert.equal(mismatch.doNotResend, true);
  assert.deepEqual(first.commands.map(item => item[0]), ['status']);
  const refused = scriptedRun(statusValue(), { code: 3, value: { error: 'browser_result_binding_mismatch' } });
  const outcome = await waitAndCollect({
    tab: scriptedTab({ url: async () => FINAL_URL, evaluate: async () => finalSnap() }),
    prepared: preparedTarget(), run: refused.run, ...quick,
  });
  assert.equal(outcome.status, 'COLLECTION_REFUSED');
  assert.equal(outcome.doNotResend, true);
  assert.equal(outcome.error, 'browser_result_binding_mismatch');
});

test('submitted recovery refuses a different conversation without collecting', async () => {
  const { run, commands } = scriptedRun(statusValue({ state: 'SUBMITTED', session_url: FINAL_URL }));
  const outcome = await waitAndCollect({
    tab: scriptedTab({ url: async () => conversation('other'), evaluate: async () => finalSnap(conversation('other')) }),
    prepared: preparedTarget(), run, ...quick,
  });
  assert.equal(outcome.status, 'SUBMITTED_SESSION_MISMATCH');
  assert.equal(outcome.doNotResend, true);
  assert.deepEqual(commands.map(item => item[0]), ['status']);
});

test('read-only recheck reports the page without opening a model menu', async () => {
  let evaluations = 0;
  const tab = { id: 'tab', playwright: { evaluate: async () => { evaluations += 1; return { url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false }; } } };
  const fresh = await readOnlyNewChat(tab, 'browser', { tab_id: 'tab', browser_context: 'browser', model_label: 'Observed', effort_label: 'Pro' });
  assert.equal(fresh.model_label, 'Observed');
  assert.equal(evaluations, 1);
  assert.equal(CHINESE_ORDINARY_CHAT.composer.includes('textbox'), true);
  tab.playwright.evaluate = async () => ({ url: 'https://chatgpt.com/', chat: true, work: true, ready: false, login: true });
  await assert.rejects(readOnlyNewChat(tab, 'browser', { tab_id: 'tab', browser_context: 'browser', model_label: 'Observed', effort_label: 'Pro' }), /PAGE_NOT_READY/);
});

function hostObservation(tabId, browserId) {
  const now = Date.now() / 1000;
  return { mode: 'CHAT', project_id: null, conversation_id: 'NEW', browser_context: browserId, tab_id: tabId,
    model_label: 'Observed model', effort_label: 'Observed effort', observed_at: now, logged_in: true,
    composer_ready: true, selection_unambiguous: true, page_url: 'https://chatgpt.com/',
    mode_evidence: 'synthetic', project_evidence: 'synthetic', conversation_evidence: 'synthetic',
    selection_evidence: { mode: 'CHAT', observed_at: now, logged_in: true, permission_confirmed: true,
      selection_unambiguous: true, locked: false, evidence_ref: 'synthetic', control_kind: 'menu',
      steps: [{ model_label: 'Observed model', effort_label: 'Observed effort' }] } };
}

test('offloadTask returns one bounded reply and recovery repeats its hash', { timeout: 60000 }, async t => {
  const evidenceDirectory = mkdtempSync(join(tmpdir(), 'web-agent-offload-'));
  t.after(() => rmSync(evidenceDirectory, { recursive: true, force: true }));
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const taskFile = join(evidenceDirectory, 'task.txt');
  const answer = 'The expression subtracts. Handle an empty input.';
  writeFileSync(taskFile, 'Review this synthetic expression: a-b.\n', 'utf8');
  const ledger = join(evidenceDirectory, 'ledger');
  const receiptPath = join(evidenceDirectory, 'prepare.json');
  const launcher = pythonLauncher();
  const run = localTransport({ root, ledger, evidenceDirectory, receiptPath, ...launcher });
  assert.throws(() => localTransport({ root, ledger: join(root, 'var', 'ledger'), evidenceDirectory: join(root, 'var'), receiptPath: join(root, 'var', 'prepare.json') }), /EVIDENCE_INSIDE_SOURCE/);
  assert.throws(() => localTransport({ root, ledger: join(evidenceDirectory, '..', 'outside'), evidenceDirectory, receiptPath }), /LEDGER_MUST_BE_INSIDE_EVIDENCE/);
  const browserId = 'synthetic-browser';
  const observation = hostObservation('synthetic-tab', browserId);
  let clicks = 0;
  const tab = { id: 'synthetic-tab', url: async () => FINAL_URL, playwright: {
    evaluate: async fn => fn === readChatGPTPage
      ? { url: FINAL_URL, status: 'FINAL_CANDIDATE', turnId: 'turn', responseIds: 'reply', fingerprint: 'abc', contentLength: answer.length, text: answer }
      : { url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false },
    locator: () => ({ fill: async () => {}, click: async () => { clicks += 1; } }),
  } };
  const result = await offloadTask({ tab, browserId, run, evidenceDirectory, repository: 'example/repo',
    sourceSha: 'a'.repeat(40), sourceRef: 'main', taskFile, orchestration: 'synthetic-offload',
    requestedModel: observation.model_label, requestedEffort: observation.effort_label,
    observe: async () => observation, composer: CHINESE_ORDINARY_CHAT.composer, sendButton: CHINESE_ORDINARY_CHAT.sendButton,
    budgetMs: 8000, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000 });
  assert.equal(result.status, 'COLLECTED', JSON.stringify(result));
  assert.equal(clicks, 1);
  assert.equal(result.doNotResend, true);
  assert.equal(result.review_approved, false);
  assert.equal(result.cost_claim, 'NOT_OBSERVABLE');
  assert.equal(result.delivery, 'browser_response');
  assert.equal(result.text, answer);
  assert.equal(result.truncated, false);
  const stored = JSON.parse(readFileSync(join(ledger, 'ledger.json'), 'utf8')).tasks[result.task_id];
  assert.equal(stored.send_attempts, 1);
  assert.equal(stored.state, 'RESULT_SEEN');
  const again = await recoverTask({ run });
  assert.equal(again.recovered, true);
  assert.equal(again.sha256, result.sha256);
  assert.equal(again.text, answer);
  assert.equal(clicks, 1);
  await assert.rejects(() => offloadTask({ tab, browserId, run, evidenceDirectory, repository: 'example/repo',
    sourceSha: 'a'.repeat(40), sourceRef: 'main', taskFile, orchestration: 'second',
    requestedModel: observation.model_label, requestedEffort: observation.effort_label,
    observe: async () => observation, composer: 'box', sendButton: 'send' }), /RECEIPT_EXISTS_USE_RECOVERY/);
  const foreign = await run('prepare', ['--task-type', 'RESEARCH', '--repository', 'other/repo',
    '--source-sha', 'b'.repeat(40), '--source-ref', 'main', '--orchestration', 'foreign']);
  assert.equal(foreign.value.error, 'ledger_repository_mismatch');
  assert.notEqual(foreign.code, 0);
  assert.equal(foreign.value.do_not_resend, true);
  assert.equal(foreign.value.ledger_mutated, false);
  assert.equal(Object.keys(JSON.parse(readFileSync(join(ledger, 'ledger.json'), 'utf8')).tasks).length, 1);
  assert.equal(clicks, 1);
});

test('an unsent prepare is not sent by recovery, and a lost click is not repeated', { timeout: 60000 }, async t => {
  const evidenceDirectory = mkdtempSync(join(tmpdir(), 'web-agent-recover-'));
  t.after(() => rmSync(evidenceDirectory, { recursive: true, force: true }));
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const taskFile = join(evidenceDirectory, 'task.txt');
  writeFileSync(taskFile, 'Synthetic read-only note.\n', 'utf8');
  const run = localTransport({ root, ledger: join(evidenceDirectory, 'ledger'), evidenceDirectory,
    receiptPath: join(evidenceDirectory, 'prepare.json'), ...pythonLauncher() });
  const browserId = 'synthetic-browser';
  const observation = hostObservation('synthetic-tab', browserId);
  await prepareOnline({ run, observation, evidenceDirectory, repository: 'example/repo', sourceSha: 'a'.repeat(40),
    sourceRef: 'main', taskFile, orchestration: 'prepared-only' });
  const pending = await recoverTask({ run });
  assert.equal(pending.status, 'PREPARED_NOT_SENT');
  assert.equal(pending.doNotResend, false);
  let clicks = 0;
  const tab = { id: 'synthetic-tab', playwright: {
    evaluate: async () => ({ url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false }),
    locator: () => ({ fill: async () => {}, click: async () => { clicks += 1; throw Error('lost click'); } }),
  } };
  const sent = await sendPrepared({ tab, browserId, run, observe: async () => observation, composer: 'box', sendButton: 'send' });
  assert.equal(sent.status, 'SUBMISSION_UNKNOWN');
  assert.equal(sent.doNotResend, true);
  assert.equal(clicks, 1);
  const blocked = await sendPrepared({ tab, browserId, run, observe: async () => observation, composer: 'box', sendButton: 'send' });
  assert.equal(blocked.status, 'SEND_NOT_AVAILABLE');
  assert.equal(clicks, 1);
  const recovered = await recoverTask({ tab: { id: 'synthetic-tab', url: async () => { throw Error('gone'); } }, run, budgetMs: 200, intervalMs: 5, urlQuietMs: 0 });
  assert.equal(recovered.status, 'TAB_GONE');
  assert.equal(recovered.doNotResend, true);
  assert.equal(clicks, 1);
});

test('a model that is not actually selected never prepares or sends', async t => {
  const evidenceDirectory = mkdtempSync(join(tmpdir(), 'web-agent-model-'));
  t.after(() => rmSync(evidenceDirectory, { recursive: true, force: true }));
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const receiptPath = join(evidenceDirectory, 'prepare.json');
  const run = localTransport({ root, ledger: join(evidenceDirectory, 'ledger'), evidenceDirectory, receiptPath, ...pythonLauncher() });
  const observation = hostObservation('synthetic-tab', 'synthetic-browser');
  await assert.rejects(offloadTask({ tab: { id: 'synthetic-tab' }, browserId: 'synthetic-browser', run,
    evidenceDirectory, repository: 'example/repo', sourceSha: 'a'.repeat(40), sourceRef: 'main',
    taskFile: join(evidenceDirectory, 'missing.txt'), orchestration: 'wrong-model',
    requestedModel: 'GPT-6', requestedEffort: 'Pro', observe: async () => observation,
    composer: 'box', sendButton: 'send' }), /REQUESTED_SELECTION_NOT_VISIBLE/);
  assert.equal(existsSync(receiptPath), false);
  assert.equal(existsSync(join(evidenceDirectory, 'ledger', 'ledger.json')), false);
});

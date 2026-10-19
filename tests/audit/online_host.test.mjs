import test from 'node:test';
import assert from 'node:assert/strict';
import { sendOnce } from '../../adapters/generic/online_host.mjs';
import { localTransport, pathRelation, prepareOnline, loadPrepared, readCollected } from '../../adapters/generic/local_transport.mjs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync, mkdirSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, win32 as win32Path } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readChatGPTPage } from '../../adapters/generic/browser_host.mjs';
import { offloadTask, recoverTask, sendPrepared, waitAndCollect, readOnlyNewChat, observeNewChat, selectionLinesFromTexts, CHINESE_ORDINARY_CHAT } from '../../adapters/generic/online_host.mjs';
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

test('read-only recheck reads the closed control and does not reuse cached labels', async () => {
  let evaluations = 0;
  let clicks = 0;
  const ready = lines => ({ url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false, selection_lines: lines });
  const tab = { id: 'tab', playwright: {
    evaluate: async () => { evaluations += 1; return ready(['Live model', 'Live effort']); },
    getByRole: () => { clicks += 1; return { click: async () => { clicks += 1; } }; },
  } };
  const binding = { tab_id: 'tab', browser_context: 'browser', model_label: 'Cached model', effort_label: 'Cached effort' };
  const fresh = await readOnlyNewChat(tab, 'browser', binding);
  assert.equal(fresh.model_label, 'Live model');
  assert.equal(fresh.effort_label, 'Live effort');
  assert.equal(evaluations, 1);
  assert.equal(clicks, 0);
  assert.equal(CHINESE_ORDINARY_CHAT.composer.includes('textbox'), true);
  tab.playwright.evaluate = async () => ready(['选择 ChatGPT 模型']);
  await assert.rejects(readOnlyNewChat(tab, 'browser', binding), /SELECTION_NOT_OBSERVABLE/);
  tab.playwright.evaluate = async () => ({ url: 'https://chatgpt.com/', chat: true, work: true, ready: false, login: true, selection_lines: ['Live model', 'Live effort'] });
  await assert.rejects(readOnlyNewChat(tab, 'browser', binding), /PAGE_NOT_READY/);
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
      : { url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false,
        selection_lines: [observation.model_label, observation.effort_label] },
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
    evaluate: async () => ({ url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false,
      selection_lines: [observation.model_label, observation.effort_label] }),
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

test('closed-control lines keep the model and effort and drop the instruction', () => {
  assert.deepEqual(selectionLinesFromTexts('选择 ChatGPT 模型', ['GPT-6', 'Pro'], ''), ['GPT-6', 'Pro']);
  assert.deepEqual(selectionLinesFromTexts('选择 ChatGPT 模型', ['选择 ChatGPT 模型'], 'GPT-6\nPro'), ['GPT-6', 'Pro']);
  assert.deepEqual(selectionLinesFromTexts('选择 ChatGPT 模型', [], '选择 ChatGPT 模型'), []);
  assert.deepEqual(selectionLinesFromTexts('选择 ChatGPT 模型', ['GPT-6 Pro'], ''), ['GPT-6 Pro']);
});

test('a changed closed-control selection after the claim does not type or click', async () => {
  const f = fixture();
  let writes = 0;
  f.tab.playwright.locator = () => ({ fill: async () => { writes += 1; }, click: async () => { writes += 1; } });
  f.tab.playwright.evaluate = async () => ({ url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false,
    selection_lines: ['Other model', 'Low'] });
  f.recheck = () => readOnlyNewChat(f.tab, 'browser', { tab_id: 'tab', browser_context: 'browser' });
  await assert.rejects(sendOnce(f), /TARGET_CHANGED/);
  assert.equal(writes, 0);
  assert.deepEqual(f.events.map(item => item[0]), ['status', 'prompt', 'claim', 'mark-unknown']);
});

test('menu observation refuses a closed control that does not show both labels', async () => {
  const clicks = [];
  const tabFor = lines => ({ id: 'tab', playwright: {
    getByRole: (role, options) => {
      clicks.push(role + ':' + (options?.name ?? ''));
      if (role === 'menuitemradio') return { all: async () => [{ getAttribute: async () => 'true', innerText: async () => 'GPT-6', click: async () => clicks.push('radio') }] };
      return { click: async () => clicks.push('click:' + options?.name), innerText: async () => 'Pro', press: async () => clicks.push('escape') };
    },
    evaluate: async () => ({ url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false, selection_lines: lines }),
  } });
  await assert.rejects(observeNewChat(tabFor(['GPT-6']), 'browser'), /SELECTION_NOT_OBSERVABLE/);
  assert.equal(clicks.includes('click:发送'), false);
  const observed = await observeNewChat(tabFor(['GPT-6', 'Pro']), 'browser');
  assert.equal(observed.model_label, 'GPT-6');
  assert.equal(observed.effort_label, 'Pro');
  assert.equal(clicks.includes('click:发送'), false);
});

test('a slow ledger read does not turn a missing tab into a host timeout', async () => {
  const run = async command => {
    assert.equal(command, 'status');
    await new Promise(resolve => setTimeout(resolve, 400));
    return { code: 0, value: statusValue() };
  };
  run.receiptPath = 'original-receipt';
  let reads = 0;
  const outcome = await waitAndCollect({
    tab: { id: 'tab', url: async () => { reads += 1; throw Error('gone'); } },
    prepared: preparedTarget(), run, budgetMs: 50, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000,
  });
  assert.equal(outcome.status, 'TAB_GONE');
  assert.equal(outcome.doNotResend, true);
  assert.equal(reads, 1);
});

test('a conversation URL read that never returns is not started again', async () => {
  let reads = 0;
  const { run, commands } = scriptedRun();
  const hung = await waitAndCollect({
    tab: { id: 'tab', url: () => { reads += 1; return new Promise(() => {}); } },
    prepared: preparedTarget(), run, budgetMs: 80, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000,
  });
  assert.equal(hung.status, 'HOST_TIMEOUT');
  assert.equal(hung.doNotResend, true);
  assert.equal(reads, 1);
  assert.deepEqual(commands.map(item => item[0]), ['status']);
  reads = 0;
  const deadline = scriptedRun(statusValue({ deadline_at: Date.now() / 1000 + 1.5 }));
  const expired = await waitAndCollect({
    tab: { id: 'tab', url: () => { reads += 1; return new Promise(() => {}); } },
    prepared: preparedTarget(), run: deadline.run, budgetMs: 5000, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000,
  });
  assert.equal(expired.status, 'TASK_DEADLINE');
  assert.equal(expired.doNotResend, true);
  assert.equal(reads, 1);
  const controller = new AbortController();
  controller.abort();
  reads = 0;
  const cancelled = await waitAndCollect({
    tab: { id: 'tab', url: () => { reads += 1; return FINAL_URL; } },
    prepared: preparedTarget(), run, budgetMs: 5000, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000,
    signal: controller.signal,
  });
  assert.equal(cancelled.status, 'CANCELLED');
  assert.equal(reads, 0);
});

test('the final page read stops at the remaining budget and does not collect', { timeout: 10000 }, async () => {
  let reads = 0;
  const { run, commands } = scriptedRun();
  const outcome = await waitAndCollect({
    tab: scriptedTab({
      url: async () => FINAL_URL,
      evaluate: async () => {
        reads += 1;
        if (reads < 3) return finalSnap();
        return new Promise(() => {});
      },
    }),
    prepared: preparedTarget(), run, budgetMs: 4000, intervalMs: 1000, urlQuietMs: 0, quietMs: 1000,
  });
  assert.equal(outcome.status, 'HOST_TIMEOUT');
  assert.equal(outcome.doNotResend, true);
  assert.equal(reads, 3);
  assert.deepEqual(commands.map(item => item[0]), ['status']);
});

test('a passed deadline does not start a closed-control read', async () => {
  let reads = 0;
  const tab = { id: 'tab', playwright: { evaluate: async () => { reads += 1; return {}; } } };
  await assert.rejects(readOnlyNewChat(tab, 'browser', { tab_id: 'tab', browser_context: 'browser' },
    { deadlineAt: Date.now() - 1000, budgetMs: 5000 }), /TASK_DEADLINE/);
  assert.equal(reads, 0);
});

test('evidence isolation rejects equal paths, prefixes, and links into the checkout', t => {
  const root = mkdtempSync(join(tmpdir(), 'web-agent-root-'));
  const evidence = mkdtempSync(join(tmpdir(), 'web-agent-evidence-'));
  const prefixed = root + '-evidence';
  mkdirSync(prefixed);
  t.after(() => {
    rmSync(root, { recursive: true, force: true });
    rmSync(evidence, { recursive: true, force: true });
    rmSync(prefixed, { recursive: true, force: true });
  });
  const open = (evidenceDirectory, ledger) => localTransport({
    root, evidenceDirectory, ledger, receiptPath: join(evidenceDirectory, 'prepare.json'),
  });
  assert.throws(() => open(root, join(root, 'ledger')), /EVIDENCE_INSIDE_SOURCE/);
  assert.throws(() => open(join(root, 'nested'), join(root, 'nested', 'ledger')), /EVIDENCE_INSIDE_SOURCE/);
  assert.throws(() => open(evidence, evidence), /LEDGER_MUST_BE_INSIDE_EVIDENCE/);
  assert.equal(pathRelation('C:\\Repo', 'C:\\Repo', win32Path), 'equal');
  assert.equal(pathRelation('C:\\Repo', 'C:\\Repo\\evidence', win32Path), 'inside');
  assert.equal(pathRelation('C:\\Repo', 'c:\\repo\\evidence', win32Path), 'inside');
  assert.equal(pathRelation('C:\\Repo', 'C:\\Repo-evidence', win32Path), 'outside');
  assert.equal(pathRelation('C:\\Repo', 'D:\\evidence', win32Path), 'outside');
  assert.equal(pathRelation('C:\\Repo', 'C:\\', win32Path), 'outside');
  assert.equal(pathRelation('C:\\repo', 'C:\\repo\\..hidden', win32Path), 'inside');
  assert.equal(pathRelation('/work/repo', '/work/repo/foo:bar'), 'inside');
  open(prefixed, join(prefixed, 'ledger'));
  open(evidence, join(evidence, 'ledger'));
  const hiddenRoot = join(evidence, 'hidden-root');
  symlinkSync(root, hiddenRoot, 'dir');
  assert.throws(() => open(hiddenRoot, join(hiddenRoot, 'ledger')), /EVIDENCE_INSIDE_SOURCE/);
  const linkedOut = join(root, 'linked-out');
  symlinkSync(evidence, linkedOut, 'dir');
  assert.throws(() => open(linkedOut, join(linkedOut, 'ledger')), /EVIDENCE_INSIDE_SOURCE/);
  mkdirSync(join(root, 'secret'));
  const linkedLedger = join(evidence, 'linked-ledger');
  symlinkSync(join(root, 'secret'), linkedLedger, 'dir');
  assert.throws(() => open(evidence, linkedLedger), /LEDGER_MUST_BE_INSIDE_EVIDENCE/);
  symlinkSync(join(root, 'missing-target'), join(evidence, 'broken-ledger'), 'dir');
  assert.throws(() => open(evidence, join(evidence, 'broken-ledger')), /LEDGER_PATH_UNRESOLVED/);
});

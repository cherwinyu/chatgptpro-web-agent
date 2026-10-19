import test from 'node:test';
import assert from 'node:assert/strict';
import { sendOnce } from '../../adapters/generic/online_host.mjs';
import { localTransport, prepareOnline, loadPrepared, readCollected } from '../../adapters/generic/local_transport.mjs';
import { createHash } from 'node:crypto';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

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

test('local transport prepares one original receipt and preserves exact UTF-8 prompt', async t => {
  const evidenceDirectory = mkdtempSync(join(tmpdir(), 'web-agent-transport-'));
  t.after(() => rmSync(evidenceDirectory, { recursive: true }));
  const taskFile = join(evidenceDirectory, 'task.txt');
  writeFileSync(taskFile, '合成任务：审查零值处理。\r\n', 'utf8');
  const ledger = join(evidenceDirectory, 'ledger');
  const receiptPath = join(evidenceDirectory, 'prepare.json');
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const run = localTransport({ root, ledger, evidenceDirectory, receiptPath });
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

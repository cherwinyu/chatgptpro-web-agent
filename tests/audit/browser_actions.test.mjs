import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { HostError } from '../../adapters/generic/browser_host.mjs';
import {
  CDP_OP_REFERENCE, PAGE_CHANNEL_BOUNDARY, asPlaywrightTab, assertBrowserBudget, awaitHostReady,
  createFileRelayActions, createMockActions, fillAndVerify, normalizeNewlines, validateDomReadCap,
} from '../../adapters/generic/browser_actions.mjs';
import { pageAdapterById } from '../../adapters/generic/page_adapter.mjs';
import { admitExecutor, transitionExecutor, EXECUTOR_REINJECTION } from '../../adapters/generic/executor_state.mjs';
import { listVisibleSelection, offloadTask, readOnlyNewChat, recoverTask, sendOnce } from '../../adapters/generic/online_host.mjs';

const MULTILINE = 'Title\n\n```gdscript\nfunc _ready():\n\tpass\n```\n\n  indented\n';

test('newline normalization keeps blank lines, indentation and fences', () => {
  assert.equal(normalizeNewlines('a\r\nb\r\n\r\nc'), 'a\nb\n\nc');
  assert.equal(normalizeNewlines(MULTILINE), MULTILINE);
  assert.notEqual(normalizeNewlines(MULTILINE), MULTILINE.replace(/\n/g, ''));
  assert.equal(normalizeNewlines('  indented\n\n\tcode\n'), '  indented\n\n\tcode\n');
});

test('dom read cap and browser budget keep their bounds', () => {
  assert.equal(validateDomReadCap(), 10000);
  assert.equal(validateDomReadCap(20000), 20000);
  assert.throws(() => validateDomReadCap(0), /DOM_READ_CAP_OUT_OF_RANGE/);
  assert.throws(() => validateDomReadCap(120001), /1 to 120000/);
  assert.throws(() => validateDomReadCap(10.5), /DOM_READ_CAP_OUT_OF_RANGE/);
  assert.equal(assertBrowserBudget(1), 1);
  assert.equal(assertBrowserBudget(45000), 45000);
  assert.throws(() => assertBrowserBudget(0), /BOUNDED_WAIT_REQUIRED: budgetMs must be an integer from 1 to 45000/);
  assert.throws(() => assertBrowserBudget(55000), /1 to 45000/);
  assert.throws(() => assertBrowserBudget(120000), /45000/);
});

test('paste fill verifies multiline text before send and a collapsed read-back does not click', async () => {
  const prompt = MULTILINE;
  const actions = createMockActions();
  const tab = asPlaywrightTab(actions, { id: 'tab' });
  const events = [];
  const run = async command => {
    events.push(command);
    return { code: 0, value: command === 'prompt' ? prompt : { claimed: true, task_id: 'synthetic', state: 'PREPARED' } };
  };
  run.receiptPath = 'synthetic-receipt';
  const target = { tab_id: 'tab', mode: 'CHAT', model_label: '6', effort_label: '6 Pro' };
  const sent = await sendOnce({
    tab, prepared: { task_id: 'synthetic', prompt, browser_target: target }, run,
    observe: async () => ({ ...target, composer_ready: true, logged_in: true, selection_unambiguous: true }),
    composer: 'box', sendButton: 'send',
  });
  assert.equal(sent.status, 'SEND_CLICKED');
  assert.equal(sent.doNotResend, true);
  assert.equal(actions.events.filter(item => item[0] === 'fill' && item[2] === 'paste').length, 1);
  assert.equal(actions.events.filter(item => item[0] === 'click').length, 1);
  assert.equal(events.includes('mark-unknown'), false);

  const collapsed = createMockActions({ collapseNewlines: true });
  const collapsedTab = asPlaywrightTab(collapsed, { id: 'tab' });
  const collapsedEvents = [];
  const collapsedRun = async command => {
    collapsedEvents.push(command);
    return { code: 0, value: command === 'prompt' ? prompt : { claimed: true, task_id: 'synthetic', state: 'PREPARED' } };
  };
  collapsedRun.receiptPath = 'synthetic-receipt';
  await assert.rejects(sendOnce({
    tab: collapsedTab, prepared: { task_id: 'synthetic', prompt, browser_target: target }, run: collapsedRun,
    observe: async () => ({ ...target, composer_ready: true, logged_in: true, selection_unambiguous: true }),
    composer: 'box', sendButton: 'send',
  }), /COMPOSER_TEXT_MISMATCH/);
  assert.equal(collapsed.events.some(item => item[0] === 'click'), false);
  assert.equal(collapsedEvents.at(-1), 'mark-unknown');
});

test('file relay refuses textContent, unknown ops and unverified send, and checks the read-back', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'web-agent-relay-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const actions = createFileRelayActions({ directory, timeoutMs: 1000 });
  await assert.rejects(actions.fill('box', MULTILINE, { method: 'textContent' }), /TEXTCONTENT_FILL_REFUSED/);
  await assert.rejects(actions._exchange('shutdown', {}), /RELAY_OP_REJECTED/);
  await assert.rejects(actions.click({ selector: 'send', x: 1, y: 2 }), /COORDINATE_CLICK_REFUSED/);
  assert.equal(readdirSync(directory).length, 0);
  await assert.rejects(actions.readComposer('box', { timeoutMs: 40 }), /HOST_TIMEOUT/);

  const prompt = MULTILINE;
  const answered = serve(directory, request => (
    request.op === 'readComposer' ? { value: prompt.replace(/\n/g, '\r\n') } : { value: null }
  ));
  await fillAndVerify(actions, 'box', prompt, { timeoutMs: 1000 });
  await actions.click({ selector: 'send', send: true });
  await answered.stop();
  assert.equal(answered.requests.some(item => item.op === 'fill' && item.payload.method === 'paste'), true);
  assert.equal(answered.requests.some(item => item.op === 'click' && item.payload.send === true && !('x' in item.payload)), true);

  const mismatchDir = mkdtempSync(join(tmpdir(), 'web-agent-relay-mismatch-'));
  t.after(() => rmSync(mismatchDir, { recursive: true, force: true }));
  const mismatch = createFileRelayActions({ directory: mismatchDir, timeoutMs: 1000 });
  const mismatchServer = serve(mismatchDir, request => (
    request.op === 'readComposer' ? { value: 'flat' } : { value: null }
  ));
  await assert.rejects(fillAndVerify(mismatch, 'box', prompt, { timeoutMs: 1000 }), /COMPOSER_TEXT_MISMATCH/);
  await assert.rejects(mismatch.click({ selector: 'send', send: true }), /COMPOSER_UNVERIFIED/);
  await mismatchServer.stop();
  assert.equal(mismatchServer.requests.some(item => item.op === 'click'), false);
});

test('visible selection labels come from the page and are not a fixed model name', async () => {
  const profile = pageAdapterById();
  assert.equal(profile.version, 1);
  assert.equal(profile.selectors.composer, profile.composer);
  assert.equal(profile.interaction.fill, 'paste-then-verify');
  assert.equal(CDP_OP_REFERENCE.socket, false);
  assert.equal(CDP_OP_REFERENCE.ops.fill.includes('textContent'), true);
  assert.throws(() => pageAdapterById('other-locale'), /PAGE_ADAPTER_NOT_FOUND/);
  const clicks = [];
  const tab = { id: 'tab', playwright: {
    getByRole(role, options) {
      clicks.push(`${role}:${options?.name ?? ''}`);
      if (role === profile.roles.modelOption) {
        return { all: async () => [{
          innerText: async () => '6',
          getAttribute: async () => 'true',
          click: async () => clicks.push('radio-click'),
        }] };
      }
      return {
        click: async () => clicks.push(`click:${options?.name ?? role}`),
        innerText: async () => '6 Pro',
        press: async () => clicks.push('escape'),
      };
    },
    evaluate: async () => ({ selection_lines: ['6 Pro'] }),
  } };
  const listed = await listVisibleSelection(tab, profile);
  assert.deepEqual(listed.model_options, [{ label: '6', checked: true }]);
  assert.equal(listed.effort_label, '6 Pro');
  assert.deepEqual(listed.closed_control_lines, ['6 Pro']);
  assert.equal(listed.send_authorized, false);
  assert.equal(clicks.includes('radio-click'), false);
  assert.equal(clicks.some(item => item.includes(profile.labels.send)), false);
});

test('invalid budget or cap fails before host ready, and ready gates timed work', async () => {
  const run = async () => { throw Error('LEDGER_SHOULD_NOT_RUN'); };
  run.receiptPath = 'unused';
  const common = {
    tab: { id: 'tab' }, browserId: 'browser', run,
    requestedModel: '6', requestedEffort: '6 Pro', composer: 'box', sendButton: 'send',
  };
  let hooked = false;
  await assert.rejects(offloadTask({
    ...common, budgetMs: 55000, whenHostReady: async () => { hooked = true; },
    observe: async () => { throw Error('OBSERVE_RAN'); },
  }), /BOUNDED_WAIT_REQUIRED: budgetMs must be an integer from 1 to 45000/);
  assert.equal(hooked, false);
  hooked = false;
  await assert.rejects(offloadTask({
    ...common, budgetMs: 1000, domReadCapMs: 0, whenHostReady: async () => { hooked = true; },
    observe: async () => { throw Error('OBSERVE_RAN'); },
  }), /DOM_READ_CAP_OUT_OF_RANGE/);
  assert.equal(hooked, false);

  let phase = 'early';
  let observed = false;
  await assert.rejects(offloadTask({
    ...common, budgetMs: 1000,
    whenHostReady: async () => { phase = 'ready'; },
    observe: async () => { observed = true; assert.equal(phase, 'ready'); throw new HostError('HOST_TIMEOUT'); },
  }), /HOST_TIMEOUT/);
  assert.equal(observed, true);

  observed = false;
  await assert.rejects(offloadTask({
    ...common, budgetMs: 1000,
    whenHostReady: async () => { throw Error('inject failed'); },
    observe: async () => { observed = true; throw Error('OBSERVE_RAN'); },
  }), /HOST_NOT_READY/);
  assert.equal(observed, false);

  let calls = 0;
  const recoverRun = async () => { calls += 1; };
  recoverRun.receiptPath = 'missing-receipt';
  await assert.rejects(recoverTask({
    run: recoverRun, budgetMs: 55000, whenHostReady: async () => { calls += 10; },
  }), /BOUNDED_WAIT_REQUIRED/);
  await assert.rejects(recoverTask({
    run: recoverRun, budgetMs: 1000, whenHostReady: async () => { throw Error('not yet'); },
  }), /HOST_NOT_READY/);
  assert.equal(calls, 0);
  await assert.rejects(awaitHostReady('ready'), /HOST_READY_HOOK_INVALID/);
});

test('a post-claim DOM cap timeout stays fail-closed and does not fill or click', async () => {
  const evidence = {
    mode: 'CHAT', project_id: null, conversation_id: 'NEW', browser_context: 'browser', tab_id: 'tab',
    model_label: '6', effort_label: '6 Pro', model_evidence_source: 'preclaim_menu', menu_model_observed: true,
    closed_control_confirms_model: false, concurrent_model_switch_ruled_out: false,
    observed_at: Date.now() / 1000, task_id: 'synthetic',
  };
  const events = [];
  const run = async command => {
    events.push(command);
    return { code: 0, value: command === 'prompt' ? 'prompt' : { claimed: true, task_id: 'synthetic', state: 'PREPARED' } };
  };
  run.receiptPath = 'synthetic-receipt';
  const tab = { id: 'tab', playwright: {
    evaluate: () => new Promise(() => {}),
    locator: () => ({ fill: async () => events.push('fill'), innerText: async () => 'prompt', click: async () => events.push('click') }),
  } };
  await assert.rejects(sendOnce({
    tab, prepared: { task_id: 'synthetic', prompt: 'prompt', browser_target: {
      mode: 'CHAT', project_id: null, conversation_id: 'NEW', browser_context: 'browser', tab_id: 'tab',
      model_label: '6', effort_label: '6 Pro',
    } }, run,
    observe: async () => evidence,
    recheck: () => readOnlyNewChat(tab, 'browser', evidence, {
      domReadCapMs: 40, deadlineAt: Date.now() + 5000, taskId: 'synthetic',
    }),
    composer: 'box', sendButton: 'send', domReadCapMs: 40,
  }), /HOST_TIMEOUT/);
  assert.equal(events.includes('fill'), false);
  assert.equal(events.includes('click'), false);
  assert.equal(events.includes('mark-unknown'), true);
});

test('executor replacement is allowed only after failed or stopped', () => {
  assert.equal(EXECUTOR_REINJECTION.replacementRequiresNewToken, true);
  assert.equal(EXECUTOR_REINJECTION.replacementRequiresNewSession, true);
  assert.equal(EXECUTOR_REINJECTION.reloadAfterClaimRefused, true);
  assert.deepEqual(admitExecutor(null), { admit: true, next: 'starting', replaced: false });
  assert.equal(admitExecutor('starting').admit, false);
  assert.equal(admitExecutor('running').reason, 'EXECUTOR_ALREADY_ACTIVE');
  assert.equal(admitExecutor('failed').replaced, true);
  assert.equal(admitExecutor('stopped').next, 'starting');
  assert.equal(admitExecutor('other').reason, 'EXECUTOR_STATE_INVALID');
  assert.equal(transitionExecutor('starting', 'run'), 'running');
  assert.equal(transitionExecutor('running', 'fail'), 'failed');
  assert.equal(transitionExecutor('running', 'stop'), 'stopped');
  assert.throws(() => transitionExecutor('failed', 'run'), /EXECUTOR_TRANSITION_REFUSED/);
  assert.throws(() => transitionExecutor('stopped', 'fail'), /EXECUTOR_TRANSITION_REFUSED/);
  assert.equal(PAGE_CHANNEL_BOUNDARY.cspBypass, 'task-duration-only');
  assert.equal(PAGE_CHANNEL_BOUNDARY.tokenFileMode, 0o600);
  assert.equal(PAGE_CHANNEL_BOUNDARY.invalidateTokenAfterSessionBind, true);
  assert.equal(PAGE_CHANNEL_BOUNDARY.restore.length >= 3, true);
});

function serve(directory, handler) {
  let stop = false;
  const requests = [];
  const loop = (async () => {
    while (!stop) {
      let names = [];
      try { names = readdirSync(directory); } catch { names = []; }
      for (const name of names.filter(item => item.endsWith('.request.json')).sort()) {
        const responseName = name.replace(/request\.json$/, 'response.json');
        const responsePath = join(directory, responseName);
        if (existsSync(responsePath)) continue;
        const request = JSON.parse(readFileSync(join(directory, name), 'utf8'));
        requests.push(request);
        const answered = handler(request);
        writeFileSync(responsePath, JSON.stringify({
          seq: request.seq, op: request.op, ok: answered.ok !== false, value: answered.value ?? null,
        }));
      }
      await new Promise(resolve => setTimeout(resolve, 5));
    }
  })();
  return { requests, stop: async () => { stop = true; await loop; } };
}

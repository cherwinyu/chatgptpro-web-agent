/** Host-side online orchestration. Browser operations use only the injected CUA tab.
 * The existing Python ledger owns durable claims; importing performs no I/O.
 *
 * A primary agent should call offloadTask once, then recoverTask if that process
 * stops. Those helpers compose the ledger, receipt and browser adapter. They do
 * not resend, replace a task, or move its deadline.
 */
import { BrowserSession, HostError, conversationUrl } from './browser_host.mjs';
import { loadPrepared, prepareOnline, readCollected } from './local_transport.mjs';

/** Selectors for the Chinese ordinary-Chat profile shipped with observeNewChat.
 * Pass them explicitly. Other locales must supply their own controls.
 */
export const CHINESE_ORDINARY_CHAT = Object.freeze({
  composer: '[role="textbox"][contenteditable="true"]',
  sendButton: 'button[aria-label="发送"]',
});

const TARGET_FIELDS = ['mode', 'project_id', 'conversation_id', 'browser_context', 'tab_id', 'model_label', 'effort_label'];

/** Read the standalone new-chat page without choosing a model. */
export function readStandaloneChat() {
  const buttons = [...document.querySelectorAll('button')];
  const chat = buttons.filter(e => e.textContent.trim() === '聊天');
  const work = buttons.filter(e => e.textContent.trim() === '工作');
  const editor = document.querySelector('[role="textbox"][contenteditable="true"]');
  return { url: location.href,
    chat: chat.length === 1 && chat[0].getAttribute('aria-pressed') === 'true',
    work: work.length === 1 && work[0].getAttribute('aria-pressed') === 'false',
    ready: !!editor && !editor.textContent.trim(),
    login: buttons.some(e => /^(登录|Log in|Sign in)$/.test(e.textContent.trim())) };
}

function pageReady(page) {
  return page.url === 'https://chatgpt.com/' && page.chat && page.work && page.ready && !page.login;
}

/** Current Chinese ordinary-Chat profile. Read the actual menu each time; never
 * infer a model from an effort label or a hidden backend identifier.
 * Call only after the host has confirmed a standalone, non-project new chat.
 * This opens the model menu once. Do not use it again after the send claim.
 */
export async function observeNewChat(tab, browserId) {
  const picker = tab.playwright.getByRole('button', { name: '选择 ChatGPT 模型', exact: true });
  await picker.click();
  await tab.playwright.getByRole('menuitem', { name: '选择模型', exact: true }).click();
  const models = await tab.playwright.getByRole('menuitemradio').all();
  let selected = null;
  for (const item of models) {
    if (await item.getAttribute('aria-checked') === 'true') {
      if (selected) throw Error('AMBIGUOUS_MODEL');
      selected = { item, text: (await item.innerText()).trim() };
    }
  }
  if (!selected) throw Error('MODEL_NOT_SELECTED');
  await selected.item.click();
  const effort = await tab.playwright.getByRole('menuitem', { name: '选择模型', exact: true }).innerText();
  await tab.playwright.getByRole('menuitem', { name: '强度', exact: true }).press('Escape');
  const page = await tab.playwright.evaluate(readStandaloneChat);
  if (!pageReady(page)) throw Error('STANDALONE_NEW_CHAT_NOT_READY');
  const now = Date.now() / 1000;
  return { mode: 'CHAT', project_id: null, conversation_id: 'NEW', browser_context: browserId,
    tab_id: tab.id, model_label: selected.text, effort_label: effort.trim(), observed_at: now,
    logged_in: true, composer_ready: true, selection_unambiguous: true, page_url: page.url,
    mode_evidence: 'Chat pressed=true; Work pressed=false', project_evidence: 'standalone home',
    conversation_evidence: 'new-chat home with empty composer',
    selection_evidence: { mode: 'CHAT', observed_at: now, logged_in: true, permission_confirmed: true,
      selection_unambiguous: true, locked: false, evidence_ref: 'live menu selection', control_kind: 'menu',
      steps: [{ model_label: selected.text, effort_label: effort.trim() }] } };
}

/** Post-claim page check. It does not open the model menu and does not click send.
 * Model and effort labels stay the values read before the claim.
 */
export async function readOnlyNewChat(tab, browserId, expected) {
  if (!expected || expected.tab_id !== tab.id || expected.browser_context !== browserId) throw Error('BOUND_TAB_MISMATCH');
  const page = await tab.playwright.evaluate(readStandaloneChat);
  if (!pageReady(page)) throw Error('PAGE_NOT_READY');
  return { mode: 'CHAT', project_id: null, conversation_id: 'NEW', browser_context: browserId, tab_id: tab.id,
    model_label: expected.model_label, effort_label: expected.effort_label,
    composer_ready: true, logged_in: true, selection_unambiguous: true, page_url: page.url };
}

export async function sendOnce({ tab, prepared, run, observe, recheck, composer, sendButton }) {
  const id = prepared.task_id;
  if (!id || !prepared.prompt || prepared.browser_target?.tab_id !== tab.id) throw Error('INVALID_PREPARE');
  if (!run.receiptPath) throw Error('ORIGINAL_RECEIPT_REQUIRED');
  if (!composer || (typeof sendButton !== 'string' && typeof sendButton !== 'function')) throw Error('SEND_CONTROLS_REQUIRED');
  const recovered = await run('status', ['--receipt', run.receiptPath]);
  if (recovered.code !== 0 || recovered.value.task_id !== id || recovered.value.state !== 'PREPARED') {
    throw Error('ORIGINAL_TASK_NOT_PREPARED');
  }
  const prompt = await run('prompt', ['--task', id]);
  if (prompt.code !== 0 || prompt.value !== prepared.prompt) throw Error('IMMUTABLE_PROMPT_MISMATCH');
  const observation = await observe();
  const claim = await run('claim', ['--task', id], { observation });
  if (claim.code !== 0 || claim.value.claimed !== true) throw Error('SEND_NOT_CLAIMED');
  // From this point every failure is uncertain. No retry, replacement or re-claim.
  try {
    const fresh = await (recheck ?? observe)();
    for (const [key, value] of Object.entries(prepared.browser_target)) {
      if (fresh[key] !== value) throw Error('TARGET_CHANGED_AFTER_CLAIM');
    }
    if (!fresh.composer_ready || !fresh.logged_in || !fresh.selection_unambiguous) throw Error('PAGE_NOT_READY');
    await tab.playwright.locator(composer).fill(prepared.prompt);
    const button = typeof sendButton === 'function' ? await sendButton(tab) : tab.playwright.locator(sendButton);
    await button.click();
    return { task_id: id, status: 'SEND_CLICKED', doNotResend: true };
  } catch (error) {
    await run('mark-unknown', ['--task', id, '--reason', 'online host failed after durable claim']);
    throw error;
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(Error('CANCELLED')); return; }
    const timer = setTimeout(finish, ms);
    const onAbort = () => finish(Error('CANCELLED'));
    function finish(error) {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      if (error) reject(error); else resolve();
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Wait until the same /c/<id> URL has stayed visible. A transient URL is not stored.
 * This never clicks send and never changes the task deadline.
 */
export async function waitForStableConversationUrl(tab, { budgetMs, deadlineAt, quietMs = 2000, intervalMs = 500, signal } = {}) {
  if (!Number.isFinite(budgetMs) || budgetMs <= 0 || !Number.isFinite(deadlineAt) || !Number.isFinite(quietMs) || quietMs < 0) {
    throw Error('INVALID_WAIT_BUDGET');
  }
  const end = performance.now() + budgetMs;
  let candidate = null;
  let since = 0;
  while (performance.now() < end && Date.now() < deadlineAt) {
    if (signal?.aborted) return { status: 'CANCELLED', doNotResend: true };
    let href;
    try { href = await tab.url(); }
    catch { return { status: 'TAB_GONE', doNotResend: true }; }
    let url = null;
    try { url = conversationUrl(href); } catch { url = null; }
    const now = performance.now();
    if (url !== candidate) { candidate = url; since = now; }
    if (url && now - since >= quietMs) return { status: 'STABLE_URL', url, doNotResend: true };
    const delay = Math.min(intervalMs, end - now, deadlineAt - Date.now());
    if (!(delay > 0)) break;
    try { await sleep(delay, signal); }
    catch { return { status: 'CANCELLED', doNotResend: true }; }
  }
  return { status: Date.now() >= deadlineAt ? 'TASK_DEADLINE' : 'HOST_TIMEOUT', doNotResend: true };
}

async function currentConversation(tab) {
  let href;
  try { href = await tab.url(); }
  catch { return { status: 'TAB_GONE' }; }
  try { return { status: 'URL', url: conversationUrl(href) }; }
  catch { return { status: 'NOT_CONVERSATION' }; }
}

/** Call after sendOnce, and again on recovery. Recovery never calls sendOnce.
 * Original receipt, tab and deadline remain mandatory. The session URL is recorded
 * only after the reply is stable on one conversation URL.
 */
export async function waitAndCollect({ tab, prepared, run, budgetMs = 55000, intervalMs = 30000,
  urlQuietMs = 2000, quietMs = 2000, signal } = {}) {
  const started = performance.now();
  const target = prepared.browser_target;
  if (!run.receiptPath) throw Error('ORIGINAL_RECEIPT_REQUIRED');
  const status = await run('status', ['--receipt', run.receiptPath]);
  if (status.code !== 0 || status.value.task_id !== prepared.task_id) throw Error('RECEIPT_RECOVERY_FAILED');
  const record = status.value;
  if (record.state === 'RESULT_SEEN') return { status: 'ALREADY_COLLECTED', doNotResend: true, task_id: prepared.task_id };
  if (!target || target.tab_id !== tab?.id) return { status: 'BOUND_TAB_MISMATCH', doNotResend: true, task_id: prepared.task_id };
  if (!['SUBMISSION_UNKNOWN', 'SUBMITTED'].includes(record.state)) {
    return { status: record.state === 'PREPARED' ? 'PREPARED_NOT_SENT' : record.state,
      doNotResend: record.send_attempts > 0, task_id: prepared.task_id };
  }
  if (typeof record.deadline_at !== 'number' || !Number.isFinite(record.deadline_at)) throw Error('ORIGINAL_DEADLINE_REQUIRED');
  const deadlineAt = record.deadline_at * 1000;
  const request = { taskId: prepared.task_id, nonce: record.nonce, requestSha256: record.request_sha256 };
  const remaining = () => budgetMs - (performance.now() - started);
  if (record.state === 'SUBMITTED' && !record.session_url) return { status: 'SUBMITTED_SESSION_MISMATCH', doNotResend: true };
  let locked = record.state === 'SUBMITTED' ? record.session_url : null;

  while (remaining() > 0 && Date.now() < deadlineAt) {
    const budget = remaining();
    if (!(budget > 0)) break;
    let url = locked;
    if (!url) {
      const stable = await waitForStableConversationUrl(tab, {
        budgetMs: budget, deadlineAt, quietMs: urlQuietMs, intervalMs: Math.min(intervalMs, 1000), signal,
      });
      if (stable.status !== 'STABLE_URL') return stable;
      url = stable.url;
    } else {
      const live = await currentConversation(tab);
      if (live.status === 'TAB_GONE') return { status: 'TAB_GONE', doNotResend: true };
      if (live.status !== 'URL' || live.url !== locked) return { status: 'SUBMITTED_SESSION_MISMATCH', doNotResend: true };
    }
    const session = new BrowserSession(tab, { id: target.browser_context, type: 'host' }, url);
    const waitBudget = remaining();
    if (!(waitBudget > 0) || Date.now() >= deadlineAt) break;
    const completion = await session.wait(request, {
      deadlineAt, budgetMs: waitBudget, intervalMs, quietMs, signal,
    });
    if (completion.status === 'TARGET_CHANGED' && !locked) {
      const live = await currentConversation(tab);
      if (live.status === 'TAB_GONE') return { status: 'TAB_GONE', doNotResend: true };
      if (live.status === 'URL' && live.url !== url) continue;
      return { ...completion, doNotResend: true };
    }
    if (completion.status !== 'PAGE_COMPLETE') return { ...completion, doNotResend: true };
    let final;
    try { final = await session.observe({ ...request, includeText: true }); }
    catch (error) {
      return { status: error instanceof HostError ? error.code : 'BROWSER_UNAVAILABLE', doNotResend: true };
    }
    if (final.status !== 'FINAL_CANDIDATE' || final.fingerprint !== completion.finalEvidence.fingerprint ||
        final.responseIds !== completion.finalEvidence.responseIds || !final.text?.trim()) {
      return { status: 'RESPONSE_CHANGED', doNotResend: true };
    }
    if (!locked) {
      const marked = await run('mark-submitted', ['--task', prepared.task_id, '--session-url', url,
        '--evidence', 'stable conversation URL and matching completed turn']);
      if (marked.code !== 0) {
        const again = await run('status', ['--receipt', run.receiptPath]);
        if (again.code !== 0 || again.value.state !== 'SUBMITTED' || again.value.session_url !== url) {
          throw Error('SUBMISSION_MARK_FAILED');
        }
      }
      locked = url;
    }
    const collected = await run('collect-browser', ['--original-receipt', run.receiptPath], {
      result: { ...final, status: 'PAGE_COMPLETE', task_id: prepared.task_id, nonce: request.nonce,
        request_sha256: prepared.request_sha256, browserId: target.browser_context, tabId: tab.id,
        url, observed_at: Date.now() / 1000 },
    });
    if (collected.code !== 0) return { status: 'COLLECTION_REFUSED', doNotResend: true, error: collected.value?.error };
    return { status: 'COLLECTED', doNotResend: true, ...collected.value };
  }
  return { status: Date.now() >= deadlineAt ? 'TASK_DEADLINE' : 'HOST_TIMEOUT', doNotResend: true };
}

function sameTarget(observed, expected) {
  return expected && TARGET_FIELDS.every(key => observed?.[key] === expected[key]);
}

/** Send the original prepared task once. Refuses when a claim already exists. */
export async function sendPrepared({ tab, browserId, run, observe = observeNewChat,
  composer, sendButton } = {}) {
  const prepared = loadPrepared(run.receiptPath);
  const status = await run('status', ['--receipt', run.receiptPath]);
  if (status.code !== 0 || status.value.task_id !== prepared.task_id) throw Error('RECEIPT_RECOVERY_FAILED');
  if (status.value.state !== 'PREPARED' || status.value.send_attempts !== 0) {
    return { status: 'SEND_NOT_AVAILABLE', doNotResend: true, state: status.value.state,
      send_attempts: status.value.send_attempts, task_id: prepared.task_id };
  }
  const observation = await observe(tab, browserId);
  if (!sameTarget(observation, prepared.browser_target)) throw Error('REQUESTED_SELECTION_NOT_VISIBLE');
  try {
    return await sendOnce({ tab, prepared, run, observe: async () => observation,
      recheck: () => readOnlyNewChat(tab, browserId, observation), composer, sendButton });
  } catch (error) {
    const after = await run('status', ['--receipt', run.receiptPath]);
    const attempts = after.value?.send_attempts ?? 1;
    return { status: attempts > 0 ? 'SUBMISSION_UNKNOWN' : 'NOT_SENT', doNotResend: attempts > 0,
      task_id: prepared.task_id, error: error.message };
  }
}

async function finishCollected(run, maxCharacters, extra = {}) {
  const reply = await readCollected(run, { maxCharacters });
  return { status: 'COLLECTED', doNotResend: true, delivery: 'browser_response',
    receipt_path: run.receiptPath, review_approved: false, cost_claim: 'NOT_OBSERVABLE', ...extra, ...reply };
}

/** Prepare, send once, wait, and return bounded reply text for the primary agent.
 * An existing receipt is never replaced; call recoverTask or sendPrepared instead.
 */
export async function offloadTask({ tab, browserId, run, evidenceDirectory, repository, sourceSha, sourceRef,
  taskFile, orchestration, requestedModel, requestedEffort, deadlineSeconds, budgetMs, intervalMs, urlQuietMs,
  quietMs, maxCharacters, composer, sendButton, observe = observeNewChat, signal } = {}) {
  if (!requestedModel || !requestedEffort) throw Error('MODEL_AND_EFFORT_REQUIRED');
  if (!composer || (typeof sendButton !== 'string' && typeof sendButton !== 'function')) throw Error('SEND_CONTROLS_REQUIRED');
  const observation = await observe(tab, browserId);
  if (observation.tab_id !== tab?.id || observation.browser_context !== browserId) throw Error('BOUND_TAB_MISMATCH');
  if (observation.model_label !== requestedModel || observation.effort_label !== requestedEffort) {
    throw Error('REQUESTED_SELECTION_NOT_VISIBLE');
  }
  const prepared = await prepareOnline({ run, observation, evidenceDirectory, repository, sourceSha, sourceRef,
    taskFile, orchestration, deadlineSeconds });
  let sent;
  try {
    sent = await sendOnce({ tab, prepared, run, observe: async () => observation,
      recheck: () => readOnlyNewChat(tab, browserId, observation), composer, sendButton });
  } catch (error) {
    const after = await run('status', ['--receipt', run.receiptPath]);
    const attempts = after.value?.send_attempts ?? 0;
    return { status: attempts > 0 ? 'SUBMISSION_UNKNOWN' : 'NOT_SENT', doNotResend: attempts > 0,
      task_id: prepared.task_id, receipt_path: run.receiptPath, error: error.message };
  }
  const outcome = await waitAndCollect({ tab, prepared, run, budgetMs, intervalMs, urlQuietMs, quietMs, signal });
  if (outcome.status === 'COLLECTED' || outcome.status === 'ALREADY_COLLECTED') return finishCollected(run, maxCharacters);
  return { ...outcome, status: outcome.status, doNotResend: true, task_id: prepared.task_id,
    receipt_path: run.receiptPath, send_status: sent.status };
}

/** Continue the original receipt. Never prepares, claims, or clicks send. */
export async function recoverTask({ tab, run, budgetMs, intervalMs, urlQuietMs, quietMs, maxCharacters, signal } = {}) {
  const prepared = loadPrepared(run.receiptPath);
  const status = await run('status', ['--receipt', run.receiptPath]);
  if (status.code !== 0 || status.value.task_id !== prepared.task_id) throw Error('RECEIPT_RECOVERY_FAILED');
  if (status.value.state === 'RESULT_SEEN') return finishCollected(run, maxCharacters, { recovered: true });
  if (status.value.state === 'PREPARED' && status.value.send_attempts === 0) {
    return { status: 'PREPARED_NOT_SENT', doNotResend: false, task_id: prepared.task_id, receipt_path: run.receiptPath };
  }
  if (!['SUBMISSION_UNKNOWN', 'SUBMITTED'].includes(status.value.state)) {
    return { status: status.value.state, doNotResend: true, task_id: prepared.task_id, receipt_path: run.receiptPath };
  }
  if (!tab || tab.id !== prepared.browser_target?.tab_id) {
    return { status: 'BOUND_TAB_MISMATCH', doNotResend: true, task_id: prepared.task_id, receipt_path: run.receiptPath };
  }
  const outcome = await waitAndCollect({ tab, prepared, run, budgetMs, intervalMs, urlQuietMs, quietMs, signal });
  if (outcome.status === 'COLLECTED' || outcome.status === 'ALREADY_COLLECTED') {
    return finishCollected(run, maxCharacters, { recovered: true });
  }
  return { ...outcome, doNotResend: true, task_id: prepared.task_id, receipt_path: run.receiptPath };
}

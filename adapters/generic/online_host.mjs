/** Host-side online orchestration. Browser operations use only the injected CUA tab.
 * The existing Python ledger owns durable claims; importing performs no I/O.
 */
import { BrowserSession, conversationUrl } from './browser_host.mjs';

/** Current Chinese ordinary-Chat profile. Read the actual menu each time; never
 * infer a model from an effort label or a hidden backend identifier.
 * Call only after the host has confirmed a standalone, non-project new chat.
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
  const page = await tab.playwright.evaluate(() => {
    const buttons = [...document.querySelectorAll('button')];
    const chat = buttons.filter(e => e.textContent.trim() === '聊天');
    const work = buttons.filter(e => e.textContent.trim() === '工作');
    const editor = document.querySelector('[role="textbox"][contenteditable="true"]');
    return { url: location.href,
      chat: chat.length === 1 && chat[0].getAttribute('aria-pressed') === 'true',
      work: work.length === 1 && work[0].getAttribute('aria-pressed') === 'false',
      ready: !!editor && !editor.textContent.trim(),
      login: buttons.some(e => /^(登录|Log in|Sign in)$/.test(e.textContent.trim())) };
  });
  if (page.url !== 'https://chatgpt.com/' || !page.chat || !page.work || !page.ready || page.login) {
    throw Error('STANDALONE_NEW_CHAT_NOT_READY');
  }
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

export async function sendOnce({ tab, prepared, run, observe, composer, sendButton }) {
  const id = prepared.task_id;
  if (!id || !prepared.prompt || prepared.browser_target?.tab_id !== tab.id) throw Error('INVALID_PREPARE');
  if (!run.receiptPath) throw Error('ORIGINAL_RECEIPT_REQUIRED');
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
    const fresh = await observe();
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

/** Call after submission navigation is visibly confirmed, and again on recovery.
 * Recovery never calls sendOnce. Original receipt and target remain mandatory.
 */
export async function waitAndCollect({ tab, prepared, run, budgetMs = 55000, intervalMs = 30000 }) {
  const target = prepared.browser_target;
  if (target.tab_id !== tab.id) throw Error('BOUND_TAB_MISMATCH');
  const status = await run('status', ['--receipt', run.receiptPath]);
  if (status.code !== 0 || status.value.task_id !== prepared.task_id) throw Error('RECEIPT_RECOVERY_FAILED');
  let url;
  try { url = conversationUrl(await tab.url()); }
  catch { return { status: 'SUBMISSION_UNKNOWN', doNotResend: true }; }
  const request = { taskId: prepared.task_id, nonce: status.value.nonce, requestSha256: status.value.request_sha256 };
  const session = new BrowserSession(tab, { id: target.browser_context, type: 'host' }, url);
  const first = await session.observe(request);
  if (!['RUNNING', 'FINAL_CANDIDATE'].includes(first.status)) return { ...first, doNotResend: true };
  if (status.value.state === 'SUBMISSION_UNKNOWN') {
    const marked = await run('mark-submitted', ['--task', prepared.task_id, '--session-url', url,
      '--evidence', 'bound submitted turn observed by online host']);
    if (marked.code !== 0) throw Error('SUBMISSION_MARK_FAILED');
  } else if (!['SUBMITTED', 'RESULT_SEEN'].includes(status.value.state) || status.value.session_url !== url) {
    throw Error('SUBMITTED_SESSION_MISMATCH');
  }
  const completion = await session.wait(request, { deadlineAt: status.value.deadline_at * 1000, budgetMs, intervalMs });
  if (completion.status !== 'PAGE_COMPLETE') return completion;
  const final = await session.observe({ ...request, includeText: true });
  if (final.status !== 'FINAL_CANDIDATE' || final.fingerprint !== completion.finalEvidence.fingerprint ||
      final.responseIds !== completion.finalEvidence.responseIds || !final.text?.trim()) throw Error('RESPONSE_CHANGED');
  const collected = await run('collect-browser', ['--original-receipt', run.receiptPath], {
    result: { ...final, status: 'PAGE_COMPLETE', task_id: prepared.task_id, nonce: request.nonce,
      request_sha256: prepared.request_sha256, browserId: target.browser_context, tabId: tab.id,
      observed_at: Date.now() / 1000 },
  });
  if (collected.code !== 0) throw Error('COLLECTION_REFUSED');
  return { status: 'COLLECTED', ...collected.value };
}

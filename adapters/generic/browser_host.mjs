/** Read-only browser adapter. No send, ledger, Git or model API operations.
 * CUA is injected by the host; importing this file performs no browser work.
 */
export class HostError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const fail = code => { throw new HostError(code); };
const label = value => typeof value === 'string' && value.trim().length > 0;
const finite = value => typeof value === 'number' && Number.isFinite(value);

export function conversationUrl(value) {
  let u;
  try { u = new URL(value); } catch { fail('INVALID_CONVERSATION_URL'); }
  if (u.protocol !== 'https:' || u.hostname !== 'chatgpt.com' || u.port || u.username || u.password ||
      !/\/c\/[a-zA-Z0-9-]+\/?$/.test(u.pathname)) fail('INVALID_CONVERSATION_URL');
  return u.origin + u.pathname.replace(/\/$/, '');
}

export function chooseBrowser(browsers, { browserId, allowExternal = false, fallbackReason } = {}) {
  if (!Array.isArray(browsers)) fail('INVALID_BROWSER_INVENTORY');
  const available = browsers.filter(b => b && label(b.id) && ['iab', 'extension', 'cdp'].includes(b.type));
  if (new Set(available.map(b => b.id)).size !== available.length) fail('INVALID_BROWSER_INVENTORY');
  if (browserId !== undefined) {
    const matches = available.filter(b => b.id === browserId);
    if (matches.length !== 1) fail('BROWSER_NOT_AVAILABLE');
    return matches[0]; // Explicit user choice; never choose by family name/profile order.
  }
  const builtIn = available.filter(b => b.type === 'iab');
  if (builtIn.length > 1) fail('AMBIGUOUS_BUILT_IN_BROWSER');
  if (builtIn.length === 1) return builtIn[0];
  if (!allowExternal || !label(fallbackReason)) fail('BUILT_IN_UNAVAILABLE');
  if (available.length !== 1) fail('EXTERNAL_BROWSER_SELECTION_REQUIRED');
  return available[0];
}

function validateRequest(r) {
  if (!r || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(r.taskId ?? '') ||
      !/^[0-9a-f]{32}$/.test(r.nonce ?? '') || !/^[0-9a-f]{64}$/.test(r.requestSha256 ?? '')) {
    fail('INVALID_REQUEST_IDENTITY');
  }
}

/** Runs wholly in the read-only DOM scope of tab.playwright.evaluate.
 * Profile observed 2026-10-16: data-turn-key / data-talvt-turn-state.
 * Unknown/changed markup returns UNKNOWN, not a textual success guess.
 * No cookies, hidden application state, network, DOM mutations or response text output.
 */
export function readChatGPTPage(request) {
  const present = e => !!e && !e.closest('[hidden],[aria-hidden="true"]');
  const visible = e => present(e) && e.getClientRects().length > 0;
  // During home -> conversation navigation a stale home main can coexist with
  // the live conversation main. Select the main owning rendered turns.
  const owners = [...new Set([...document.querySelectorAll('[data-turn-key]')]
    .filter(present).map(e => e.closest('main')).filter(e => e && visible(e)))];
  if (owners.length > 1) return { url: location.href, status: 'UNKNOWN', reason: 'AMBIGUOUS_CONVERSATION' };
  const main = owners[0] || document.querySelector('main');
  const base = { url: location.href, profile: 'chatgpt-turn-dom-v1', status: 'UNKNOWN' };
  if (!main) return { ...base, reason: 'NO_MAIN' };
  const controls = [...main.querySelectorAll('button,[role="button"]')].filter(visible);
  const name = e => (e.getAttribute('aria-label') || e.textContent || '').trim();
  if (controls.some(e => /^(Log in|Sign in|登录|登入)$/i.test(name(e)))) {
    return { ...base, status: 'LOGIN_REQUIRED' };
  }
  const turns = [...main.querySelectorAll('[data-turn-key]')].filter(present);
  if (!turns.length) return { ...base, reason: 'UNSUPPORTED_LAYOUT' };
  const matches = turns.filter(turn => {
    const users = [...turn.querySelectorAll('[data-content-search-unit-key]')]
      .filter(e => e.getAttribute('data-content-search-unit-key').endsWith(':user'));
    return users.some(e => {
      const text = e.textContent || '';
      return [['task_id', request.taskId], ['nonce', request.nonce], ['request_sha256', request.requestSha256]]
        .every(([key, value]) => new RegExp('(?:^|\\s)' + key + ':\\s*' + value + '(?=\\s|$)').test(text));
    });
  });
  if (matches.length !== 1) return { ...base, reason: matches.length ? 'AMBIGUOUS_REQUEST' : 'REQUEST_NOT_VISIBLE' };
  const turn = matches[0];
  if (turn !== turns.at(-1)) return { ...base, status: 'TARGET_CHANGED', reason: 'NEWER_TURN' };
  const states = [...turn.querySelectorAll('[data-talvt-turn-state]')];
  const state = states.length === 1 ? states[0].getAttribute('data-talvt-turn-state') : null;
  const alerts = [...main.querySelectorAll('[role="alert"]')].filter(e => visible(e) && e.textContent.trim());
  if (alerts.length || controls.some(e => /^(Continue generating|继续生成|重试|Try again)$/i.test(name(e))) ||
      ['error', 'failed', 'cancelled', 'interrupted', 'stopped'].includes(state)) {
    return { ...base, status: 'PAGE_ERROR' };
  }
  const active = controls.some(e => /^(Stop|Stop generating|停止|停止生成|停止回复)$/i.test(name(e))) ||
    [...main.querySelectorAll('[aria-busy="true"]')].some(visible);
  if (active || ['streaming', 'running', 'pending', 'thinking', 'in_progress'].includes(state)) {
    return { ...base, status: 'RUNNING' };
  }
  const replies = [...turn.querySelectorAll('[data-content-search-unit-key]')]
    .filter(e => present(e) && e.getAttribute('data-content-search-unit-key').endsWith(':assistant'));
  if (state !== 'complete' || !replies.length) return { ...base, reason: 'NO_FINAL_STATE' };
  const reply = replies.at(-1);
  const ids = reply.getAttribute('data-chatgpt-search-message-ids');
  const text = (reply.textContent || '').trim();
  // Attachment-only replies need a rendered attachment control as positive content.
  const attachment = [...reply.querySelectorAll('button')].some(e => visible(e) && /^(下载文件|Download file)$/i.test(name(e)));
  if (!ids || (!text.replace(/ChatGPT\s*(说|said)[:：]?/gi, '').trim() && !attachment)) {
    return { ...base, reason: 'EMPTY_OR_UNIDENTIFIED_RESPONSE' };
  }
  // Compare in memory only. No prompt/response text leaves this DOM evaluation.
  let hash = 2166136261;
  const signature = text + reply.innerHTML;
  for (let i = 0; i < signature.length; i++) hash = Math.imul(hash ^ signature.charCodeAt(i), 16777619) >>> 0;
  return { ...base, status: 'FINAL_CANDIDATE', turnId: turn.getAttribute('data-turn-key'),
    responseIds: ids, fingerprint: hash.toString(16), contentLength: signature.length,
    ...(request.includeText === true ? { text: reply.innerText } : {}) };
}

export class CompletionTracker {
  constructor({ quietMs = 2000 } = {}) {
    if (!finite(quietMs) || quietMs < 1000 || quietMs > 60000) fail('INVALID_QUIET_WINDOW');
    this.quietMs = quietMs;
    this.candidate = null;
    this.lastTime = -Infinity;
  }
  update(observation, now) {
    if (!finite(now) || now <= this.lastTime) fail('NON_MONOTONIC_OBSERVATION');
    this.lastTime = now;
    const result = { ...observation, sendAuthorized: false, deliveryVerified: false, reviewApproved: false };
    if (observation.status !== 'FINAL_CANDIDATE') { this.candidate = null; return result; }
    if (![observation.turnId, observation.responseIds, observation.fingerprint].every(label) ||
        !Number.isSafeInteger(observation.contentLength) || observation.contentLength <= 0) {
      this.candidate = null;
      return { ...result, status: 'UNKNOWN', reason: 'INVALID_FINAL_EVIDENCE' };
    }
    const key = JSON.stringify([observation.turnId, observation.responseIds, observation.fingerprint, observation.contentLength]);
    if (key !== this.candidate?.key) this.candidate = { key, since: now };
    return { ...result, status: now - this.candidate.since >= this.quietMs ? 'PAGE_COMPLETE' : 'SETTLING' };
  }
}

/** A real CUA adapter, not a JSON observation-file checker. */
export async function connectCua(cua, options) {
  const url = conversationUrl(options.url);
  if (options.tabId !== undefined && options.browserId === undefined) fail('BOUND_BROWSER_REQUIRED');
  const selected = chooseBrowser(await cua.listBrowsers({ emit: false }), options);
  const tabs = await cua.listTabs({ browser: selected.id, emit: false });
  const matching = tabs.filter(t => {
    try { return conversationUrl(t.url) === url && (options.tabId === undefined || t.id === options.tabId); }
    catch { return false; }
  });
  if (matching.length > 1) fail('AMBIGUOUS_TAB');
  if (!matching.length && (!options.createIfMissing || options.tabId !== undefined)) fail('BOUND_TAB_NOT_FOUND');
  // Browser selection is already fixed; failures never fall back to Chrome.
  const tab = matching.length ? await cua.getTab(matching[0].id, { browser: selected.id }) :
    await cua.createBrowserTab(selected.id, url,
      selected.type === 'iab' ? { visible: false } : { sessionName: '🔎 Pro Bridge' });
  if (!tab.playwright?.evaluate) fail('DOM_READER_UNAVAILABLE');
  return new BrowserSession(tab, selected, url);
}

export class BrowserSession {
  constructor(tab, selected, url) {
    this.tab = tab;
    this.binding = Object.freeze({ browserId: selected.id, browserType: selected.type, tabId: tab.id, url });
  }
  async observe(request, { timeoutMs = 10000 } = {}) {
    validateRequest(request);
    if (!finite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 60000) fail('INVALID_TIMEOUT');
    const snapshot = await this.tab.playwright.evaluate(readChatGPTPage, request, { timeoutMs });
    let current;
    try { current = conversationUrl(snapshot.url); } catch { current = null; }
    if (current !== this.binding.url || this.tab.id !== this.binding.tabId) {
      return { status: 'TARGET_CHANGED', sendAuthorized: false, deliveryVerified: false };
    }
    return { ...snapshot, url: current, sendAuthorized: false, deliveryVerified: false, reviewApproved: false };
  }
  async wait(request, { budgetMs = 120000, deadlineAt, intervalMs = 30000,
    quietMs = 2000, signal } = {}) {
    validateRequest(request);
    if (!finite(budgetMs) || budgetMs <= 0 || budgetMs > 3600000 ||
        !finite(deadlineAt) || !finite(intervalMs) || intervalMs < 1000 || intervalMs > 60000) fail('INVALID_WAIT_BUDGET');
    const deadlineWindow = Math.max(0, deadlineAt - Date.now());
    // Chosen once. Comparing the two clocks again after the timer fires can
    // call a deadline-sized wait a host timeout when performance.now runs ahead.
    const limitedByDeadline = deadlineWindow <= budgetMs;
    const end = performance.now() + Math.min(budgetMs, deadlineWindow);
    const tracker = new CompletionTracker({ quietMs });
    let lastStatus = 'UNKNOWN', checks = 0;
    const result = status => ({ status, lastStatus, checks, binding: this.binding,
      sendAuthorized: false, deliveryVerified: false, reviewApproved: false, doNotResend: true });
    while (performance.now() < end && Date.now() < deadlineAt) {
      if (signal?.aborted) return result('CANCELLED');
      const untilDeadline = deadlineAt - Date.now();
      const untilBudget = end - performance.now();
      const remaining = Math.min(untilBudget, untilDeadline);
      let snapshot;
      try {
        snapshot = await boundOperation(() => this.observe(request, { timeoutMs: Math.min(10000, remaining) }), remaining, signal);
      } catch (error) {
        if (error instanceof HostError && error.code === 'HOST_TIMEOUT' &&
            (Date.now() >= deadlineAt || limitedByDeadline)) {
          return result('TASK_DEADLINE');
        }
        return result(error instanceof HostError ? error.code : 'BROWSER_UNAVAILABLE');
      }
      if (signal?.aborted) return result('CANCELLED');
      if (performance.now() >= end || Date.now() >= deadlineAt) break;
      checks++;
      const state = tracker.update(snapshot, performance.now());
      lastStatus = state.status;
      if (['PAGE_COMPLETE', 'PAGE_ERROR', 'LOGIN_REQUIRED', 'TARGET_CHANGED'].includes(state.status)) {
        return { ...result(state.status), finalEvidence: state };
      }
      const delay = Math.min(state.status === 'SETTLING' ? quietMs : intervalMs,
        end - performance.now(), deadlineAt - Date.now());
      if (delay > 0) await pause(delay, signal);
    }
    return result(Date.now() >= deadlineAt || limitedByDeadline ? 'TASK_DEADLINE' : 'HOST_TIMEOUT');
  }
}

function pause(ms, signal) {
  return new Promise(resolve => {
    const done = () => { clearTimeout(timer); signal?.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, ms);
    signal?.addEventListener('abort', done, { once: true });
    if (signal?.aborted) done();
  });
}
/** Race one browser call against the remaining time and cancel signal.
 * Losing the race does not abort the underlying call when the host API cannot
 * cancel it. Callers must not start another read after this returns.
 */
export function boundOperation(operation, ms, signal) {
  return new Promise((resolve, reject) => {
    const cancel = () => finish(reject, new HostError('CANCELLED'));
    const timer = setTimeout(() => finish(reject, new HostError('HOST_TIMEOUT')), Math.ceil(ms));
    const finish = (callback, value) => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); callback(value); };
    signal?.addEventListener('abort', cancel, { once: true });
    if (signal?.aborted) return cancel();
    Promise.resolve().then(operation).then(v => finish(resolve, v), e => finish(reject, e));
  });
}

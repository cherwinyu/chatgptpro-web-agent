/** Browser-action contract for hosts that do not expose a Playwright/CUA tab.
 *
 * Minimal operations: url, evaluate, click, press, fill, readComposer, queryRole.
 * Each call carries timeoutMs. A timed-out call is not cancelled when the host
 * cannot cancel it, and the caller must not start a replacement read or a
 * second send. Fill is paste, then a newline-normalized innerText read-back,
 * and only then may send click. Assigning textContent is refused because it
 * collapses newlines. Send click stays fail-closed until that read-back matches.
 *
 * CDP_OP_REFERENCE is a mapping only. This module does not open a socket.
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { HostError, boundOperation } from './browser_host.mjs';

export const BROWSER_ACTIONS = Object.freeze([
  'url', 'evaluate', 'click', 'press', 'fill', 'readComposer', 'queryRole',
]);

export const DOM_READ_CAP_MIN_MS = 1;
export const DOM_READ_CAP_MAX_MS = 120000;
export const DOM_READ_CAP_DEFAULT_MS = 10000;

export const BROWSER_BUDGET_MIN_MS = 1;
export const BROWSER_BUDGET_MAX_MS = 45000;

export const CDP_OP_REFERENCE = Object.freeze({
  version: 1,
  socket: false,
  ops: Object.freeze({
    url: 'Target.getTargetInfo or the host tab URL',
    evaluate: 'Runtime.evaluate',
    click: 'element.click via Runtime.evaluate; menu items must not use coordinates',
    press: 'Input.dispatchKeyEvent or element key dispatch',
    fill: 'paste or Input.insertText; do not assign textContent',
    readComposer: 'element.innerText',
    queryRole: 'Accessibility.getFullAXTree or a role query inside Runtime.evaluate',
  }),
});

/** Security boundary for a user-authorized tab. CSP bypass, when the host has
 * one, lasts only for that task and must be turned back off. This object does
 * not enable a bypass.
 */
export const PAGE_CHANNEL_BOUNDARY = Object.freeze({
  authorizedTabOnly: true,
  cspBypass: 'task-duration-only',
  tokenFileMode: 0o600,
  invalidateTokenAfterSessionBind: true,
  restore: Object.freeze([
    'Page.setBypassCSP { enabled: false }',
    'location.reload()',
    'verify the page connect-src blocks the loopback port again',
    'confirm the host process has exited',
  ]),
});

export function normalizeNewlines(value) {
  return String(value ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

export function validateDomReadCap(value = DOM_READ_CAP_DEFAULT_MS) {
  if (!Number.isSafeInteger(value) || value < DOM_READ_CAP_MIN_MS || value > DOM_READ_CAP_MAX_MS) {
    throw Error(`DOM_READ_CAP_OUT_OF_RANGE: domReadCapMs must be an integer from ${DOM_READ_CAP_MIN_MS} to ${DOM_READ_CAP_MAX_MS}`);
  }
  return value;
}

export function assertBrowserBudget(budgetMs) {
  if (!Number.isSafeInteger(budgetMs) || budgetMs < BROWSER_BUDGET_MIN_MS || budgetMs > BROWSER_BUDGET_MAX_MS) {
    throw Error(`BOUNDED_WAIT_REQUIRED: budgetMs must be an integer from ${BROWSER_BUDGET_MIN_MS} to ${BROWSER_BUDGET_MAX_MS}`);
  }
  return budgetMs;
}

/** Await an explicit host-ready signal before timed browser work.
 * A missing hook means the caller is already ready. Rejection fails closed
 * before a new claim and does not by itself mark an existing claim unknown.
 */
export async function awaitHostReady(whenHostReady, signal) {
  if (whenHostReady == null) return;
  if (signal?.aborted) throw Error('CANCELLED');
  if (typeof whenHostReady !== 'function' && typeof whenHostReady?.then !== 'function') {
    throw Error('HOST_READY_HOOK_INVALID');
  }
  try {
    if (typeof whenHostReady === 'function') await whenHostReady({ signal });
    else await whenHostReady;
  } catch (error) {
    if (error instanceof Error && (error.message === 'CANCELLED' || error.message === 'HOST_NOT_READY')) throw error;
    const wrapped = Error('HOST_NOT_READY');
    wrapped.cause = error;
    throw wrapped;
  }
}

/** Paste, read innerText, and compare after newline normalization.
 * Mismatch throws before the caller is allowed to click send.
 */
export async function fillAndVerify(actions, selector, text, { timeoutMs, signal } = {}) {
  const cap = validateDomReadCap(timeoutMs);
  if (typeof text !== 'string' || text.length === 0) throw Error('COMPOSER_TEXT_REQUIRED');
  if (typeof actions?.fill !== 'function' || typeof actions?.readComposer !== 'function') {
    throw Error('BROWSER_ACTIONS_REQUIRED');
  }
  await boundOperation(() => actions.fill(selector, text, { method: 'paste', timeoutMs: cap }), cap, signal);
  const actual = await boundOperation(() => actions.readComposer(selector, { timeoutMs: cap }), cap, signal);
  if (typeof actual !== 'string') throw Error('COMPOSER_READBACK_UNAVAILABLE');
  const expected = normalizeNewlines(text);
  if (normalizeNewlines(actual) !== expected) throw Error('COMPOSER_TEXT_MISMATCH');
  if (typeof actions.noteComposerVerified === 'function') actions.noteComposerVerified(expected);
}

export function resolveBrowserActions(source) {
  if (source?.actions && typeof source.actions.fill === 'function' && typeof source.actions.readComposer === 'function') {
    return source.actions;
  }
  if (source && source.kind && typeof source.fill === 'function' && typeof source.readComposer === 'function') {
    return source;
  }
  if (typeof source?.playwright?.locator === 'function') return cuaPlaywrightActions(source);
  throw Error('BROWSER_ACTIONS_REQUIRED');
}

export function cuaPlaywrightActions(tab) {
  return {
    kind: 'cua-playwright',
    async url() {
      if (typeof tab.url !== 'function') throw Error('BROWSER_ACTIONS_REQUIRED');
      return tab.url();
    },
    async evaluate(fn, arg) {
      return arg === undefined ? tab.playwright.evaluate(fn) : tab.playwright.evaluate(fn, arg);
    },
    async fill(selector, text, options = {}) {
      if (options.method === 'textContent') throw Error('TEXTCONTENT_FILL_REFUSED');
      const locator = tab.playwright.locator(selector);
      if (typeof locator.fill !== 'function') throw Error('COMPOSER_FILL_UNAVAILABLE');
      await locator.fill(text);
    },
    async readComposer(selector) {
      const locator = tab.playwright.locator(selector);
      if (typeof locator.innerText !== 'function') throw Error('COMPOSER_READBACK_UNAVAILABLE');
      return locator.innerText();
    },
    async click(target) {
      if (target?.selector) {
        await tab.playwright.locator(target.selector).click();
        return;
      }
      await tab.playwright.getByRole(target.role, { name: target.name, exact: target.exact }).click();
    },
    async press(target, key) {
      await tab.playwright.getByRole(target.role, { name: target.name, exact: target.exact }).press(key);
    },
    async queryRole(role, options) {
      return tab.playwright.getByRole(role, options);
    },
  };
}

/** In-memory reference. `collapseNewlines` simulates a textContent fill. */
export function createMockActions(page = {}) {
  const events = [];
  let composer = '';
  const actions = {
    kind: 'mock',
    events,
    composerVerified: false,
    async url() {
      events.push('url');
      return page.url ?? 'https://chatgpt.com/';
    },
    async evaluate(fn, arg) {
      events.push('evaluate');
      if (page.evaluate) return page.evaluate(fn, arg);
      return { url: 'https://chatgpt.com/', chat: true, work: true, ready: true, login: false, selection_lines: [] };
    },
    async fill(selector, text, options = {}) {
      if (options.method === 'textContent') throw Error('TEXTCONTENT_FILL_REFUSED');
      events.push(['fill', selector, options.method || 'paste']);
      actions.composerVerified = false;
      composer = page.collapseNewlines ? String(text).replace(/\n/g, '') : String(text);
    },
    async readComposer() {
      events.push('readComposer');
      return composer;
    },
    noteComposerVerified() { actions.composerVerified = true; },
    async click(target) {
      if (target?.send === true && !actions.composerVerified) throw Error('COMPOSER_UNVERIFIED');
      events.push(['click', target]);
    },
    async press(target, key) { events.push(['press', target?.role ?? null, key]); },
    async queryRole(role, options) {
      events.push(['queryRole', role, options?.name ?? null]);
      if (page.queryRole) return page.queryRole(role, options);
      return { all: async () => [], innerText: async () => '', click: async () => {}, press: async () => {} };
    },
  };
  return actions;
}

function relayStem(seq) {
  return String(seq).padStart(6, '0');
}

function relayHandle(actions, role, options, items) {
  const list = Array.isArray(items) ? items : [];
  return {
    async all() {
      return list.map(item => ({
        innerText: async () => String(item.label ?? ''),
        getAttribute: async name => (name === 'aria-checked' ? (item.checked ? 'true' : 'false') : null),
        click: async () => actions.click({
          role, name: item.label ?? null, exact: true, interaction: 'element.click',
        }),
      }));
    },
    innerText: async () => list.map(item => String(item.label ?? '')).filter(Boolean).join('\n'),
    click: async () => actions.click({
      role, name: options?.name ?? null, exact: options?.exact === true, interaction: 'element.click',
    }),
    press: async key => actions.press({ role, name: options?.name ?? null, exact: options?.exact === true }, key),
  };
}

/** File relay. One JSON request, one JSON response, unknown operations refused.
 * Fill asks for paste. Send click is refused until fillAndVerify notes a match.
 */
export function createFileRelayActions({ directory, timeoutMs = DOM_READ_CAP_DEFAULT_MS } = {}) {
  if (typeof directory !== 'string' || !directory) throw Error('RELAY_DIRECTORY_REQUIRED');
  let seq = 0;
  let verified = false;
  const actions = {
    kind: 'file-relay',
    requestPath(id) { return join(directory, `${relayStem(id)}.request.json`); },
    async fill(selector, text, options = {}) {
      if (options.method === 'textContent') throw Error('TEXTCONTENT_FILL_REFUSED');
      verified = false;
      await exchange('fill', { selector, text, method: 'paste', readBack: 'innerText' }, options.timeoutMs ?? timeoutMs);
    },
    async readComposer(selector, options = {}) {
      return exchange('readComposer', { selector, readBack: 'innerText' }, options.timeoutMs ?? timeoutMs);
    },
    noteComposerVerified() { verified = true; },
    async click(target = {}, options = {}) {
      if (target.send === true && !verified) throw Error('COMPOSER_UNVERIFIED');
      const payload = {
        ...target,
        interaction: target.interaction || (target.send ? 'element.click' : null),
      };
      if ('x' in payload || 'y' in payload) throw Error('COORDINATE_CLICK_REFUSED');
      await exchange('click', payload, options.timeoutMs ?? timeoutMs);
      if (target.send === true) verified = false;
    },
    async press(target, key, options = {}) {
      await exchange('press', { ...target, key, interaction: 'element.click' }, options.timeoutMs ?? timeoutMs);
    },
    async url(options = {}) {
      return exchange('url', {}, options.timeoutMs ?? timeoutMs);
    },
    async evaluate(fn, arg, options = {}) {
      const name = typeof fn === 'function' ? fn.name : '';
      if (!name) throw Error('RELAY_EVALUATE_NAME_REQUIRED');
      return exchange('evaluate', { function: name, arg: arg ?? null }, options.timeoutMs ?? timeoutMs);
    },
    async queryRole(role, options = {}, callOptions = {}) {
      const found = await exchange('queryRole', {
        role, name: options.name ?? null, exact: options.exact === true,
      }, callOptions.timeoutMs ?? timeoutMs);
      return relayHandle(actions, role, options, found?.items);
    },
  };

  async function exchange(op, payload, opTimeout) {
    if (!BROWSER_ACTIONS.includes(op)) throw Error('RELAY_OP_REJECTED');
    const cap = validateDomReadCap(opTimeout ?? timeoutMs);
    const id = ++seq;
    const requestPath = actions.requestPath(id);
    const responsePath = join(directory, `${relayStem(id)}.response.json`);
    writeFileSync(requestPath, JSON.stringify({ seq: id, op, timeoutMs: cap, payload }), { encoding: 'utf8', flag: 'wx' });
    const started = Date.now();
    while (!existsSync(responsePath)) {
      if (Date.now() - started > cap) throw new HostError('HOST_TIMEOUT');
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    const raw = JSON.parse(readFileSync(responsePath, 'utf8'));
    if (raw.seq !== id || raw.op !== op || raw.ok !== true) throw Error(raw.error || 'RELAY_RESPONSE_REJECTED');
    return raw.value;
  }

  actions._exchange = exchange;
  return actions;
}

/** Tab-shaped view so online_host can run against mock or file-relay actions. */
export function asPlaywrightTab(actions, { id } = {}) {
  if (typeof id !== 'string' || !id) throw Error('BOUND_TAB_REQUIRED');
  return {
    id,
    actions,
    url: () => actions.url(),
    playwright: {
      evaluate: (fn, arg) => actions.evaluate(fn, arg),
      locator: selector => ({
        fill: text => actions.fill(selector, text, { method: 'paste' }),
        innerText: () => actions.readComposer(selector),
        click: () => actions.click({ selector, send: false }),
      }),
      getByRole: (role, options) => actions.queryRole(role, options),
    },
  };
}

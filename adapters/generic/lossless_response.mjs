/** Optional lossless collection through injected browser and clipboard APIs. */
import { createHash, randomUUID } from 'node:crypto';
import { Buffer } from 'node:buffer';
import { TextDecoder } from 'node:util';
import { loadPrepared } from './local_transport.mjs';

const fail = code => { throw Error(code); };
const hash = text => createHash('sha256').update(text, 'utf8').digest('hex');
const errorCode = error => /^[A-Z][A-Z0-9_]+$/.test(error?.message ?? '') ? error.message : 'CUA_OPERATION_FAILED';
let clipboardBusy = false;

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
const sameClipboard = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
function plainText(items) {
  const entries = items.flatMap(item => item.entries ?? []).filter(entry => entry.mimeType === 'text/plain');
  if (entries.length !== 1) fail('LOSSLESS_CLIPBOARD_TEXT_AMBIGUOUS');
  const entry = entries[0];
  if (typeof entry.text === 'string') return entry.text;
  if (typeof entry.base64 === 'string') return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.from(entry.base64, 'base64'));
  fail('LOSSLESS_CLIPBOARD_TEXT_MISSING');
}
function conversationUrl(value) {
  let url;
  try { url = new URL(value); } catch { fail('LOSSLESS_CONVERSATION_INVALID'); }
  if (url.protocol !== 'https:' || url.hostname !== 'chatgpt.com' || url.port || url.username || url.password ||
      !/^\/c\/[a-zA-Z0-9-]+\/?$/.test(url.pathname)) fail('LOSSLESS_CONVERSATION_INVALID');
  return url.origin + url.pathname.replace(/\/$/, '');
}
function identity(request, binding, tab) {
  if (!request || !/^[a-f0-9-]{36}$/.test(request.taskId ?? '') ||
      !/^[a-f0-9]{32}$/.test(request.nonce ?? '') || !/^[a-f0-9]{64}$/.test(request.requestSha256 ?? '') ||
      request.taskId !== binding.task_id || request.nonce !== binding.nonce ||
      request.requestSha256 !== binding.request_sha256 || !binding.browserId ||
      binding.tabId !== tab.id || !['SUBMISSION_UNKNOWN', 'SUBMITTED', 'RESULT_SEEN'].includes(binding.state)) {
    fail('LOSSLESS_REQUEST_BINDING_MISMATCH');
  }
}
function sameFinal(actual, expected) {
  if (actual?.status !== 'FINAL_CANDIDATE' || !/^[a-zA-Z0-9_-]{1,200}$/.test(actual.turnId ?? '') ||
      !actual.responseIds || !actual.fingerprint || !Number.isSafeInteger(actual.contentLength) || actual.contentLength <= 0 ||
      ['turnId', 'responseIds', 'fingerprint', 'contentLength'].some(key => actual[key] !== expected[key]) ||
      conversationUrl(actual.url) !== conversationUrl(expected.url)) fail('LOSSLESS_RESPONSE_CHANGED');
}

/** Read the whole-response copy control outside content units; clicking stays in the host. */
export function readCopyControl({ turnId, responseIds }) {
  const labels = ['复制', 'Copy'];
  const present = e => !!e && !e.closest('[hidden],[aria-hidden="true"]');
  const visible = e => present(e) && e.getClientRects().length > 0;
  const turns = [...document.querySelectorAll('[data-turn-key]')].filter(present);
  const matches = turns.filter(e => e.getAttribute('data-turn-key') === turnId);
  if (matches.length !== 1 || matches[0] !== turns.at(-1)) return { ok: false, label: null };
  const turn = matches[0];
  const replies = [...turn.querySelectorAll('[data-content-search-unit-key]')]
    .filter(e => present(e) && e.getAttribute('data-content-search-unit-key').endsWith(':assistant'));
  const states = [...turn.querySelectorAll('[data-talvt-turn-state]')];
  const buttons = [...turn.querySelectorAll('button[aria-label]')]
    .filter(button => labels.includes(button.getAttribute('aria-label')?.trim()) &&
      !button.closest('[data-content-search-unit-key]'));
  const ok = replies.at(-1)?.getAttribute('data-chatgpt-search-message-ids') === responseIds &&
    states.length === 1 && states[0].getAttribute('data-talvt-turn-state') === 'complete' &&
    buttons.length === 1 && visible(buttons[0]) && !buttons[0].disabled &&
    buttons[0].getAttribute('aria-disabled') !== 'true';
  return { ok, label: ok ? buttons[0].getAttribute('aria-label').trim() : null };
}

/** Require stable DOM evidence. Await operations serially: a timeout does not cancel UI work. */
export async function copyFinalResponse({ tab, readPage, request, binding, expected, timeoutMs = 8000, signal, onStage = () => {} }) {
  onStage('validate_binding');
  identity(request, binding, tab);
  sameFinal(expected, expected);
  const url = conversationUrl(expected.url);
  if (binding.url && conversationUrl(binding.url) !== url) fail('LOSSLESS_CONVERSATION_MISMATCH');
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 10000) fail('LOSSLESS_TIMEOUT_INVALID');
  if (!tab.clipboard?.read || !tab.clipboard?.write || !tab.clipboard?.writeText) fail('LOSSLESS_CLIPBOARD_UNAVAILABLE');
  if (clipboardBusy) fail('LOSSLESS_CLIPBOARD_BUSY');
  clipboardBusy = true;
  const end = performance.now() + timeoutMs;
  const checkTime = () => {
    if (signal?.aborted) fail('LOSSLESS_CANCELLED');
    if (performance.now() >= end) fail('LOSSLESS_TIMEOUT');
  };
  const step = async operation => { checkTime(); const result = await operation(); checkTime(); return result; };
  let saved, owned, copied, output, primaryError, touched = false, restoration = 'not_needed';
  try {
    const recheck = async () => {
      onStage('recheck_url');
      if (tab.id !== binding.tabId || conversationUrl(await step(() => tab.url())) !== url) fail('LOSSLESS_CONVERSATION_MISMATCH');
      onStage('recheck_dom');
      sameFinal(await step(() => tab.playwright.evaluate(readPage, { ...request, includeText: false })), expected);
    };
    await recheck();
    onStage('probe_copy_control');
    const probe = await step(() => tab.playwright.evaluate(readCopyControl, expected));
    const label = probe?.label;
    if (probe?.ok !== true || !['复制', 'Copy'].includes(label)) fail('LOSSLESS_COPY_CONTROL_AMBIGUOUS');
    onStage('save_clipboard');
    saved = structuredClone(await step(() => tab.clipboard.read()));
    if (!Array.isArray(saved)) fail('LOSSLESS_CLIPBOARD_SNAPSHOT_INVALID');
    const sentinel = `web-agent-lossless-${randomUUID()}`;
    // Mark before awaiting: a rejected/slow write can still have modified the clipboard.
    owned = [{ entries: [{ mimeType: 'text/plain', text: sentinel }] }];
    touched = true;
    onStage('write_sentinel');
    await step(() => tab.clipboard.writeText(sentinel));
    onStage('verify_sentinel');
    const armed = await step(() => tab.clipboard.read());
    if (plainText(armed) !== sentinel) fail('LOSSLESS_CLIPBOARD_CONCURRENT_CHANGE');
    owned = armed;
    await recheck();
    onStage('recheck_copy_control');
    const secondProbe = await step(() => tab.playwright.evaluate(readCopyControl, expected));
    if (secondProbe?.ok !== true || secondProbe.label !== label) fail('LOSSLESS_COPY_CONTROL_AMBIGUOUS');
    onStage('click_copy');
    await step(() => tab.playwright.locator(
      `[data-turn-key="${expected.turnId}"] button[aria-label="${label}"]:not([data-content-search-unit-key] button)`).click({ timeoutMs: Math.max(1, Math.floor(end - performance.now())) }));
    // UI observations after the click let the browser finish its clipboard update.
    onStage('observe_after_copy');
    await step(() => tab.getAXState({ emit: false }));
    onStage('read_copied_clipboard');
    copied = await step(() => tab.clipboard.read());
    const text = plainText(copied);
    if (text === sentinel || !text.trim()) fail('LOSSLESS_COPY_DID_NOT_UPDATE');
    owned = copied;
    await recheck();
    output = { ...expected, text, sha256: hash(text), source: 'assistant_copy_button',
      task_id: request.taskId, nonce: request.nonce, request_sha256: request.requestSha256,
      browserId: binding.browserId, tabId: binding.tabId };
    return output;
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    let restoreError, restoreStage;
    try {
      if (touched && saved && tab.clipboard.systemRestorationApplicable === false) {
        restoration = 'not_applicable_cursor_relay';
      } else if (touched && saved) {
        restoreStage = 'read_before_restore';
        const current = await tab.clipboard.read();
        if (sameClipboard(current, owned)) {
          restoreStage = 'restore_saved_items';
          if (saved.length === 0) {
            // CUA rejects write([]); restore an empty clipboard as empty text.
            await tab.clipboard.writeText('');
            restoration = 'cleared_empty_clipboard';
          } else {
            await tab.clipboard.write(saved);
            restoration = 'restored';
          }
        } else restoration = 'skipped_concurrent_change';
      }
    } catch (error) {
      restoreError = error;
      restoration = 'failed';
    } finally {
      clipboardBusy = false;
    }
    // A failed restoration must be visible, not silently presented as a clean capture.
    if (restoreError) {
      const error = primaryError ?? Error('LOSSLESS_CLIPBOARD_RESTORE_FAILED');
      error.losslessDiagnostic = { primaryCode: primaryError ? errorCode(primaryError) : null,
        restoreCode: errorCode(restoreError), restoreErrorName: restoreError.name, restoreStage,
        savedItemCount: saved?.length ?? null,
        savedFormats: saved?.map(item => (item.entries ?? []).map(entry => entry.mimeType)) ?? [] };
      if (!primaryError) throw error;
    }
    if (output) output.clipboardRestoration = restoration;
    if (!primaryError) checkTime();
  }
}

/** Intercept only the supplied reader with includeText. Keep single-use collection proof in memory. */
export function createLosslessAdapter({ tab, readPage, getBinding }) {
  let proof = null;
  let lastFailure = null;
  const controller = new AbortController();
  const pending = new Set();
  const wrap = operation => {
    const promise = operation();
    pending.add(promise);
    promise.then(() => pending.delete(promise), () => pending.delete(promise));
    return promise;
  };
  const playwright = new Proxy(tab.playwright, {
    get(target, key) {
      if (key === 'evaluate') return (fn, request, ...rest) => {
        if (fn !== readPage || request?.includeText !== true) return target.evaluate(fn, request, ...rest);
        return wrap(async () => {
          proof = null;
          let stage = 'read_ledger_binding';
          const started = performance.now();
          try {
            const binding = await getBinding();
            identity(request, binding, tab);
            stage = 'read_final_dom';
            const expected = await target.evaluate(fn, { ...request, includeText: false }, ...rest);
            const result = await copyFinalResponse({ tab, readPage, request, binding, expected,
              timeoutMs: Math.min(8000, rest[0]?.timeoutMs ?? 8000), signal: controller.signal,
              onStage: value => { stage = value; } });
            proof = result;
            return result;
          } catch (error) {
            lastFailure = { stage, code: errorCode(error),
              errorName: error.name, elapsedMs: Math.round(performance.now() - started), ...error.losslessDiagnostic };
            throw error;
          }
        });
      };
      const value = Reflect.get(target, key, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
  return {
    get lastFailure() { return lastFailure ? { ...lastFailure } : null; },
    tab: new Proxy(tab, { get(target, key) {
      if (key === 'playwright') return playwright;
      const value = Reflect.get(target, key, target);
      return typeof value === 'function' ? value.bind(target) : value;
    } }),
    assertCollection(result) {
      if (!proof || result?.status !== 'PAGE_COMPLETE' ||
          ['text', 'task_id', 'nonce', 'request_sha256', 'browserId', 'tabId', 'turnId', 'responseIds', 'fingerprint', 'contentLength']
            .some(key => result[key] !== proof[key]) || conversationUrl(result.url) !== conversationUrl(proof.url) ||
          hash(result.text) !== proof.sha256) fail('LOSSLESS_COLLECTION_PROOF_REQUIRED');
      proof = null;
    },
    async drain() {
      // Wait for in-flight UI work and cleanup even when the host has already timed out.
      controller.abort();
      await Promise.allSettled([...pending]);
      proof = null;
    },
  };
}

/** Bind an optional lossless tab and collection guard to the original local receipt. */
export function createLosslessCollection({ tab, run, readPage }) {
  const getBinding = async () => {
    let prepared, report;
    try {
      prepared = loadPrepared(run.receiptPath);
      report = await run('status', ['--receipt', run.receiptPath]);
    } catch { fail('ORIGINAL_OFFLOAD_TASK_REQUIRED'); }
    const record = report?.value;
    if (report?.code !== 0 || !record || record.task_id !== prepared.task_id ||
        record.request_sha256 !== prepared.request_sha256) fail('ORIGINAL_OFFLOAD_TASK_REQUIRED');
    return { task_id: prepared.task_id, nonce: record.nonce, request_sha256: prepared.request_sha256,
      browserId: prepared.browser_target.browser_context, tabId: prepared.browser_target.tab_id,
      url: record.session_url, state: record.state };
  };
  const adapter = createLosslessAdapter({ tab, readPage, getBinding });
  const guardedRun = async (command, args = [], files = {}) => {
    if (command === 'collect-browser') adapter.assertCollection(files.result);
    return run(command, args, files);
  };
  guardedRun.receiptPath = run.receiptPath;
  return { tab: adapter.tab, run: guardedRun,
    get lastFailure() { return adapter.lastFailure; }, drain: () => adapter.drain() };
}

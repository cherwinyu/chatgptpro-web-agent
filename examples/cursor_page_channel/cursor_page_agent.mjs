/** Experimental text-only page executor. No task state or send policy lives here. */
export const PAGE_ORIGIN = 'https://chatgpt.com';
export const SCHEMA = 'web-agent-cursor-relay-v1';
export const LIMITS = Object.freeze({ body: 1048576, next: 1048576, value: 524288 });
export const OPS = Object.freeze(['evaluate', 'url', 'dom.state', 'role.all', 'role.click',
  'role.innerText', 'role.getAttribute', 'role.press', 'locator.fill', 'locator.click',
  'clipboard.read', 'clipboard.writeText', 'clipboard.write']);
export const canonical = value => Array.isArray(value) ? value.map(canonical) :
  value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;

export function validateConfiguration({ port, token }) {
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535 ||
      typeof token !== 'string' || token.length !== 64 || !/^[a-f0-9]{64}$/.test(token)) throw Error('PAGE_BOOTSTRAP_INVALID');
}

/** Helpers stay inside this function: its source runs in a separate page realm. */
export async function executePageOperation(request, environment = globalThis) {
  const allowed = ['evaluate', 'url', 'dom.state', 'role.all', 'role.click',
    'role.innerText', 'role.getAttribute', 'role.press', 'locator.fill', 'locator.click',
    'clipboard.read', 'clipboard.writeText', 'clipboard.write'];
  if (!request || !allowed.includes(request.op)) throw Error('PAGE_OPERATION_REFUSED');
  const { op, params = {} } = request;
  const document = environment.document;
  const view = environment.window ?? environment;
  if (op.startsWith('clipboard.')) {
    const validItems = items => Array.isArray(items) && items.every(item => item &&
      typeof item === 'object' && Object.keys(item).every(key => key === 'entries') &&
      Array.isArray(item.entries) && item.entries.every(entry => entry && typeof entry === 'object' &&
        Object.keys(entry).every(key => ['mimeType', 'text'].includes(key)) &&
        typeof entry.mimeType === 'string' && (!Object.hasOwn(entry, 'text') || typeof entry.text === 'string')));
    if ((op === 'clipboard.write' && !validItems(params.value)) ||
        (op === 'clipboard.writeText' && typeof params.value !== 'string')) throw Error('PAGE_CLIPBOARD_INVALID');
    const key = '__webAgentCursorClipboardV1';
    if (!environment[key]) {
      const state = { items: [] };
      const storeText = text => { state.items = [{ entries: [{ mimeType: 'text/plain', text: String(text) }] }]; };
      const navigator = environment.navigator;
      if (!navigator) throw Error('PAGE_CLIPBOARD_UNAVAILABLE');
      const wrapper = {
        writeText: async text => { storeText(text); },
        write: async items => {
          const converted = [];
          for (const item of items || []) {
            const entries = [];
            for (const type of item.types || []) entries.push({ mimeType: type,
              ...(type.startsWith('text/') ? { text: await (await item.getType(type)).text() } : {}) });
            converted.push({ entries });
          }
          state.items = converted;
        },
        readText: async () => state.items.flatMap(item => item.entries)
          .find(entry => entry.mimeType === 'text/plain')?.text || '',
        read: async () => state.items,
      };
      try { Object.defineProperty(navigator, 'clipboard', { configurable: true, value: wrapper }); }
      catch {
        const original = navigator.clipboard;
        if (!original) throw Error('PAGE_CLIPBOARD_UNAVAILABLE');
        original.writeText = wrapper.writeText;
        original.write = wrapper.write;
      }
      if (navigator.clipboard.writeText !== wrapper.writeText || navigator.clipboard.write !== wrapper.write) {
        throw Error('PAGE_CLIPBOARD_UNAVAILABLE');
      }
      environment[key] = state;
    }
    const state = environment[key];
    if (op === 'clipboard.read') return state.items;
    state.items = op === 'clipboard.write' ? params.value
      : [{ entries: [{ mimeType: 'text/plain', text: params.value }] }];
    return true;
  }
  const normalize = text => String(text ?? '').replace(/\r\n?/g, '\n');
  const read = element => normalize('value' in element ? element.value :
    [...element.childNodes].map(node => node.textContent ?? '').join('\n'));
  const select = element => {
    const selection = environment.getSelection?.(), range = document.createRange?.();
    if (!selection || !range) throw Error('PAGE_SELECTION_UNAVAILABLE');
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
  };
  const fill = async (element, value) => {
    if (typeof value !== 'string') throw Error('PAGE_FILL_INVALID');
    const expected = normalize(value);
    element.focus();
    if ('value' in element) {
      element.value = expected;
      if (typeof view.InputEvent !== 'function') throw Error('PAGE_INPUT_EVENT_UNAVAILABLE');
      element.dispatchEvent(new view.InputEvent('input', {
        bubbles: true, inputType: 'insertText', data: expected,
      }));
    } else {
      if (!element.isContentEditable) throw Error('PAGE_EDITOR_REQUIRED');
      try {
        select(element);
        const transfer = new view.DataTransfer();
        transfer.setData('text/plain', expected);
        element.dispatchEvent(new view.ClipboardEvent('paste', {
          bubbles: true, cancelable: true, composed: true, clipboardData: transfer,
        }));
      } catch { /* An unavailable paste path may use the same fill's edit fallback. */ }
      await Promise.resolve();
      if (read(element) !== expected) {
        select(element);
        const command = (name, text = null) => {
          if (document.execCommand?.(name, false, text) !== true) throw Error('PAGE_FILL_UNAVAILABLE');
        };
        command('delete');
        const lines = expected.split('\n');
        for (let index = 0; index < lines.length; index++) {
          if (lines[index]) command('insertText', lines[index]);
          if (index + 1 < lines.length) command('insertParagraph');
        }
      }
    }
    await Promise.resolve();
    if (read(element) !== expected) throw Error('PAGE_FILL_VERIFICATION_FAILED');
    return true;
  };
  const click = (element, role) => {
    if (role === 'menuitem' || role === 'menuitemradio') { element.click(); return true; }
    if (role !== 'button' && !element.matches?.('button,[role="button"]')) throw Error('PAGE_CLICK_REFUSED');
    if (typeof view.PointerEvent !== 'function' || typeof view.MouseEvent !== 'function') {
      throw Error('PAGE_POINTER_EVENTS_UNAVAILABLE');
    }
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
      const Event = type.startsWith('pointer') ? view.PointerEvent : view.MouseEvent;
      element.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, composed: true,
        button: 0, buttons: type.endsWith('down') ? 1 : 0, pointerId: 1, pointerType: 'mouse', isPrimary: true }));
    }
    element.click();
    return true;
  };
  if (op === 'url') return environment.location.href;
  if (op === 'dom.state') return { readyState: document.readyState };
  if (op === 'evaluate') {
    if (typeof params.expression !== 'string' || !params.expression) throw Error('PAGE_EXPRESSION_REQUIRED');
    return await environment.eval(params.expression);
  }
  let items, role;
  if (op.startsWith('role.')) {
    const descriptor = params.descriptor;
    role = descriptor?.role;
    if (!['button', 'menuitem', 'menuitemradio'].includes(role)) throw Error('PAGE_ROLE_REFUSED');
    const root = descriptor.within ? document.querySelector(descriptor.within) : document;
    if (!root) throw Error('PAGE_ELEMENT_AMBIGUOUS');
    items = [...root.querySelectorAll(role === 'button' ? 'button,[role="button"]' : `[role="${role}"]`)];
    const name = element => (element.getAttribute('aria-label') || element.innerText || element.textContent || '').trim();
    if (descriptor.name !== undefined) items = items.filter(element => descriptor.exact
      ? name(element) === descriptor.name : name(element).includes(descriptor.name));
    if (descriptor.index !== undefined) items = items[descriptor.index] ? [items[descriptor.index]] : [];
    if (op === 'role.all') return items.map((element, index) => ({ index, name: name(element) }));
  } else items = [...document.querySelectorAll(params.selector)];
  if (items.length !== 1) throw Error('PAGE_ELEMENT_AMBIGUOUS');
  const element = items[0];
  if (op.endsWith('.click')) return click(element, role ?? element.getAttribute('role'));
  if (op === 'locator.fill') return fill(element, params.value);
  if (op === 'role.innerText') return element.innerText;
  if (op === 'role.getAttribute') return element.getAttribute(params.name);
  if (op === 'role.press') {
    if (params.key !== 'Escape' || typeof view.KeyboardEvent !== 'function') throw Error('PAGE_KEY_REFUSED');
    for (const type of ['keydown', 'keyup']) element.dispatchEvent(new view.KeyboardEvent(type, { key: params.key, bubbles: true }));
    return true;
  }
  throw Error('PAGE_OPERATION_REFUSED');
}

async function pageLoop(config, execute, canonical, ops) {
  if (location.origin !== config.origin || globalThis.top !== globalThis.self) return;
  const marker = '__webAgentCursorPageAgentV1';
  if (globalThis[marker] && !['failed', 'stopped'].includes(globalThis[marker].state)) return;
  const state = { session: crypto.randomUUID(), state: 'starting' };
  globalThis[marker] = state;
  const headers = { Authorization: `Bearer ${config.token}`, 'X-Web-Agent-Session': state.session,
    'Content-Type': 'application/json' };
  const base = `http://127.0.0.1:${config.port}`;
  const bytes = value => new TextEncoder().encode(value).length;
  const code = error => /^[A-Z][A-Z0-9_]+$/.test(error?.message ?? '') ? error.message : 'PAGE_OPERATION_FAILED';
  let lastSequence = 0;
  // Bound network waits independently of DOM execution, which cannot be cancelled.
  const fetchLocal = (path, options = {}) => fetch(base + path, {
    ...options, headers, cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(45000),
  });
  try {
    if (document.readyState === 'loading') {
      await new Promise(resolve => addEventListener('DOMContentLoaded', resolve, { once: true }));
    }
    state.state = 'running';
    while (true) {
      const next = await fetchLocal('/next');
      if (next.status === 204) continue;
      if (!next.ok) throw Error('PAGE_NEXT_REFUSED');
      const text = await next.text();
      if (bytes(text) > config.limits.next) throw Error('PAGE_NEXT_TOO_LARGE');
      const request = JSON.parse(text);
      const { request_sha256: claimed, ...unsigned } = request;
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(canonical(unsigned))));
      const hash = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
      if (hash !== claimed) throw Error('PAGE_REQUEST_HASH_MISMATCH');
      if (request.schema !== config.schema || !ops.includes(request.op) ||
          !Number.isSafeInteger(request.seq) || request.seq <= lastSequence ||
          !Number.isSafeInteger(request.timeout_ms) || request.timeout_ms < 1 || request.timeout_ms > 45000) {
        throw Error('PAGE_REQUEST_INVALID');
      }
      lastSequence = request.seq; // Consume before any side effect; never replay an operation.
      let response;
      try {
        const value = (await execute(request, globalThis)) ?? null;
        if (bytes(JSON.stringify(value)) > config.limits.value) throw Error('PAGE_VALUE_TOO_LARGE');
        response = { request_sha256: hash, ok: true, value };
      } catch (error) {
        response = { request_sha256: hash, ok: false, error: code(error) };
      }
      const posted = await fetchLocal('/result', { method: 'POST', body: JSON.stringify(response) });
      if (!posted.ok) throw Error('PAGE_RESULT_REFUSED');
      const acknowledgment = await posted.json();
      if (acknowledgment.accepted !== true) throw Error('PAGE_RESULT_REFUSED');
      if (!response.ok) throw Error(response.error);
    }
  } catch (error) {
    state.state = 'failed';
    state.error = code(error); // Do not expose token-bearing fetch URLs or arbitrary exception text.
  }
}

export function createPageAgentSource(configuration = {}) {
  validateConfiguration(configuration);
  const config = { port: configuration.port, token: configuration.token,
    origin: PAGE_ORIGIN, schema: SCHEMA, limits: LIMITS };
  // JSON encoding prevents code injection; only validated fields enter the source.
  return `;(${pageLoop.toString()})(${JSON.stringify(config)},(${executePageOperation.toString()}),`
    + `(function(){const canonical=${canonical.toString()};return canonical;})(),${JSON.stringify(OPS)});`;
}

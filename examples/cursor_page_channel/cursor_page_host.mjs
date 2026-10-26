/** Experimental loopback transport. The existing online host owns all task state. */
import { createHash, randomBytes } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { PAGE_ORIGIN, SCHEMA, OPS, LIMITS, canonical, validateConfiguration,
  createPageAgentSource } from './cursor_page_agent.mjs';

export const CSP_REMINDER = Object.freeze({ action: 'DISABLE_CSP_BYPASS_AND_RELOAD',
  method: 'Page.setBypassCSP', params: { enabled: false }, reload: true, executor: 'caller' });
const hash = value => createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
const byteLength = value => Buffer.byteLength(JSON.stringify(value));
const required = value => {
  if (typeof value !== 'string' || !value.trim()) throw Error('EXPLICIT_ARGUMENT_REQUIRED');
  return value;
};
const limit = (value, maximum, code) => {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) throw Error(code);
  return value;
};
const headers = { 'Access-Control-Allow-Origin': PAGE_ORIGIN,
  'Access-Control-Allow-Private-Network': 'true',
  'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Web-Agent-Session',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS', 'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff', Vary: 'Origin' };
function reply(response, status, value) {
  response.writeHead(status, { ...headers, 'Content-Type': 'application/json; charset=utf-8' });
  response.end(value === undefined ? '' : JSON.stringify(value));
}
function readBody(request) {
  return new Promise((resolveBody, reject) => {
    const chunks = [];
    let length = 0, failed = false;
    request.on('data', chunk => {
      length += chunk.length;
      if (length > LIMITS.body) {
        if (!failed) reject(Error('PAGE_BODY_TOO_LARGE'));
        failed = true;
        chunks.length = 0;
      } else if (!failed) chunks.push(chunk);
    });
    request.on('end', () => {
      if (failed) return;
      try { resolveBody(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch { reject(Error('PAGE_BODY_INVALID')); }
    });
    request.on('error', () => reject(Error('PAGE_BODY_INVALID')));
    request.on('aborted', () => reject(Error('PAGE_BODY_INVALID')));
  });
}

export function createBootstrapLoader({ port, token, bootstrapPath } = {}) {
  validateConfiguration({ port, token });
  if (typeof bootstrapPath !== 'string' || bootstrapPath.length !== 59 || !/^\/bootstrap\/[a-f0-9]{48}$/.test(bootstrapPath)) {
    throw Error('PAGE_BOOTSTRAP_INVALID');
  }
  const url = `http://127.0.0.1:${port}${bootstrapPath}`;
  return `(async()=>{const r=await fetch(${JSON.stringify(url)},{headers:{Authorization:`
    + `${JSON.stringify(`Bearer ${token}`)}},cache:"no-store",credentials:"omit",signal:AbortSignal.timeout(45000)});`
    + 'if(!r.ok)throw Error("PAGE_BOOTSTRAP_REFUSED");(0,eval)(await r.text())})()';
}

export function makeRequest(seq, op, params = {}, timeoutMs = 8000) {
  const unsigned = { schema: SCHEMA, seq, op, params, timeout_ms: timeoutMs };
  return validateRequest({ ...unsigned, request_sha256: hash(unsigned) });
}
export function validateRequest(record) {
  if (!record || record.schema !== SCHEMA || !Number.isSafeInteger(record.seq) || record.seq < 1 ||
      !OPS.includes(record.op) || !record.params || typeof record.params !== 'object') throw Error('RELAY_REQUEST_INVALID');
  limit(record.timeout_ms, 45000, 'PAGE_TIMEOUT_INVALID');
  if (byteLength(record) > LIMITS.next) throw Error('PAGE_NEXT_TOO_LARGE');
  const { request_sha256, ...unsigned } = record;
  if (hash(unsigned) !== request_sha256) throw Error('RELAY_REQUEST_HASH_MISMATCH');
  return record;
}

export async function startCursorPageChannel({ responseTimeoutMs = 8000, connectTimeoutMs = 120000 } = {}) {
  limit(responseTimeoutMs, 45000, 'PAGE_TIMEOUT_INVALID');
  limit(connectTimeoutMs, 300000, 'PAGE_CONNECT_TIMEOUT_INVALID');
  const token = randomBytes(32).toString('hex');
  const bootstrapPath = `/bootstrap/${randomBytes(24).toString('hex')}`;
  let port, closing, used = false, session = null, closed = false, poisoned = false, pending = null, lastSeq = 0;
  let connectionError = null;
  const connections = new Set(), polls = new Set();
  const finishConnections = error => {
    clearTimeout(connectTimer);
    for (const waiter of connections) error ? waiter.reject(error) : waiter.resolve();
    connections.clear();
  };
  const connectTimer = setTimeout(() => {
    connectionError = Error('PAGE_CONNECT_TIMEOUT');
    finishConnections(connectionError);
  }, connectTimeoutMs);
  const finishPending = (error, result) => {
    if (!pending) return;
    const active = pending;
    pending = null;
    clearTimeout(active.timer);
    if (error) { poisoned = true; active.reject(error); }
    else active.resolve(result);
  };
  const deliver = response => {
    if (!pending || pending.delivered) return false;
    pending.delivered = true; // One delivery, including when the result POST is lost.
    reply(response, 200, pending.record);
    return true;
  };
  const server = createServer(async (request, response) => {
    try {
      if (request.headers.origin !== PAGE_ORIGIN) return reply(response, 403, { error: 'PAGE_ORIGIN_REFUSED' });
      const path = request.url;
      const expectedMethod = path === bootstrapPath || path === '/next' ? 'GET' : path === '/result' ? 'POST' : null;
      if (!expectedMethod) return reply(response, 404, { error: 'PAGE_ROUTE_REFUSED' });
      if (request.method === 'OPTIONS') {
        const requested = request.headers['access-control-request-headers']?.toLowerCase().split(',').map(x => x.trim()) ?? [];
        if (request.headers['access-control-request-method'] !== expectedMethod || !requested.includes('authorization') ||
            requested.some(name => !['authorization', 'content-type', 'x-web-agent-session'].includes(name)) ||
            ![undefined, 'true'].includes(request.headers['access-control-request-private-network'])) {
          return reply(response, 403, { error: 'PAGE_PREFLIGHT_REFUSED' });
        }
        return reply(response, 204);
      }
      if (request.method !== expectedMethod) return reply(response, 405, { error: 'PAGE_METHOD_REFUSED' });
      if (request.headers.authorization !== `Bearer ${token}`) return reply(response, 403, { error: 'PAGE_TOKEN_REFUSED' });
      if (closed || connectionError || poisoned) return reply(response, 410, { error: 'PAGE_CHANNEL_STOPPED' });
      if (path === bootstrapPath) {
        if (used) return reply(response, 410, { error: 'PAGE_BOOTSTRAP_ALREADY_USED' });
        used = true;
        response.writeHead(200, { ...headers, 'Content-Type': 'application/javascript; charset=utf-8' });
        response.end(createPageAgentSource({ port, token }));
        return;
      }
      const candidate = request.headers['x-web-agent-session'];
      if (typeof candidate !== 'string' || !/^[a-zA-Z0-9-]{16,128}$/.test(candidate)) {
        return reply(response, 400, { error: 'PAGE_SESSION_INVALID' });
      }
      if (session === null && path === '/next') { session = candidate; finishConnections(); }
      if (candidate !== session) return reply(response, 409, { error: 'PAGE_SESSION_MISMATCH' });
      if (path === '/next') {
        if (deliver(response)) return;
        if (polls.size) return reply(response, 409, { error: 'PAGE_POLL_PENDING' });
        const poll = { response, timer: setTimeout(() => {
          polls.delete(poll); reply(response, 204);
        }, Math.min(20000, responseTimeoutMs)) };
        polls.add(poll);
        response.on('close', () => { clearTimeout(poll.timer); polls.delete(poll); });
        return;
      }
      if (request.headers['content-type']?.split(';')[0] !== 'application/json') {
        return reply(response, 415, { error: 'PAGE_CONTENT_TYPE_REFUSED' });
      }
      const result = await readBody(request);
      if (!pending || !pending.delivered) return reply(response, 409, { error: 'PAGE_NO_PENDING_REQUEST' });
      if (result?.request_sha256 !== pending.record.request_sha256) {
        return reply(response, 409, { error: 'RELAY_RESPONSE_HASH_MISMATCH' });
      }
      if (typeof result.ok !== 'boolean' || (result.ok && !Object.hasOwn(result, 'value')) ||
          (!result.ok && (typeof result.error !== 'string' || !/^[A-Z][A-Z0-9_]{0,127}$/.test(result.error)))) {
        return reply(response, 400, { error: 'RELAY_RESPONSE_INVALID' });
      }
      if (result.ok && byteLength(result.value) > LIMITS.value) {
        return reply(response, 413, { error: 'PAGE_VALUE_TOO_LARGE' });
      }
      finishPending(null, result);
      reply(response, 200, { accepted: true });
    } catch (error) {
      if (!response.headersSent) reply(response, error.message === 'PAGE_BODY_TOO_LARGE' ? 413 : 400,
        { error: error.message === 'PAGE_BODY_TOO_LARGE' ? error.message : 'PAGE_BODY_INVALID' });
    }
  });
  server.requestTimeout = 45000;
  server.headersTimeout = 10000;
  server.maxConnections = 8;
  server.on('clientError', (_error, socket) => socket.destroy());
  try {
    await new Promise((resolveListen, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolveListen);
    });
    const address = server.address();
    if (!address || address.address !== '127.0.0.1' || address.family !== 'IPv4') throw Error('PAGE_LOOPBACK_REQUIRED');
    port = address.port;
  } catch (error) {
    clearTimeout(connectTimer);
    server.close();
    throw error;
  }
  return {
    port, token, bootstrapPath,
    waitForConnection() {
      if (closed || connectionError) return Promise.reject(connectionError ?? Error('PAGE_CHANNEL_CLOSED'));
      if (session) return Promise.resolve();
      return new Promise((resolve, reject) => connections.add({ resolve, reject }));
    },
    dispatch(record) {
      validateRequest(record);
      if (closed || poisoned || connectionError) throw Error('PAGE_CHANNEL_STOPPED');
      if (!session) throw Error('PAGE_NOT_CONNECTED');
      if (pending) throw Error('PAGE_REQUEST_PENDING');
      if (record.seq <= lastSeq) throw Error('PAGE_SEQUENCE_REPLAY');
      lastSeq = record.seq;
      return new Promise((resolve, reject) => {
        pending = { record: structuredClone(record), resolve, reject, delivered: false,
          timer: setTimeout(() => finishPending(Error('PAGE_RESPONSE_TIMEOUT')),
            Math.min(responseTimeoutMs, record.timeout_ms)) };
        const poll = polls.values().next().value;
        if (poll) { clearTimeout(poll.timer); polls.delete(poll); deliver(poll.response); }
      });
    },
    async close() {
      if (closed) return closing;
      closed = true;
      finishConnections(Error('PAGE_CHANNEL_CLOSED'));
      finishPending(Error('PAGE_CHANNEL_CLOSED'));
      for (const poll of polls) { clearTimeout(poll.timer); reply(poll.response, 204); }
      polls.clear();
      closing = new Promise(resolveClose => { server.close(resolveClose); server.closeAllConnections(); });
      await closing;
    },
  };
}

export function createCursorPageRelay({ evidenceDirectory, viewId, browserId, pageChannel, operationTimeoutMs = 8000 }) {
  required(viewId); required(browserId); required(evidenceDirectory);
  limit(operationTimeoutMs, 45000, 'PAGE_TIMEOUT_INVALID');
  const directory = mkdtempSync(join(evidenceDirectory, 'page-relay-'));
  let sequence = 0, stopped = false;
  const request = async (op, params = {}) => {
    if (stopped) throw Error('RELAY_STOPPED');
    const record = makeRequest(++sequence, op, params, operationTimeoutMs);
    const write = (suffix, value) => writeFileSync(join(directory, `${record.seq}.${suffix}.json`),
      JSON.stringify(value) + '\n', { encoding: 'utf8', flag: 'wx' });
    try {
      write('request', record); // Transport credentials are never part of a relay record.
      const result = await pageChannel.dispatch(record);
      if (result.request_sha256 !== record.request_sha256) throw Error('RELAY_RESPONSE_HASH_MISMATCH');
      if (typeof result.ok !== 'boolean') throw Error('RELAY_RESPONSE_INVALID');
      write('response', result);
      if (!result.ok) throw Error(result.error);
      return result.value;
    } catch (error) { stopped = true; throw error; }
  };
  const role = descriptor => ({
    click: () => request('role.click', { descriptor }),
    innerText: () => request('role.innerText', { descriptor }),
    getAttribute: name => request('role.getAttribute', { descriptor, name }),
    press: key => request('role.press', { descriptor, key }),
    all: async () => {
      const items = await request('role.all', { descriptor });
      if (!Array.isArray(items)) throw Error('RELAY_ALL_INVALID');
      return items.map((_, index) => role({ ...descriptor, index }));
    },
  });
  return { id: viewId, url: () => request('url'), getAXState: () => request('dom.state'),
    clipboard: {
      systemRestorationApplicable: false,
      read: () => request('clipboard.read'),
      writeText: value => request('clipboard.writeText', { value }),
      write: value => request('clipboard.write', { value }),
    },
    playwright: {
      evaluate: (fn, arg) => request('evaluate', { expression: `(${fn.toString()})(${JSON.stringify(arg) ?? 'undefined'})` }),
      getByRole: (name, options = {}) => role({ role: name, ...options }),
      locator: selector => ({ fill: value => request('locator.fill', { selector, value }),
        click: _options => request('locator.click', { selector }),
        getByRole: (name, options = {}) => role({ role: name, ...options, within: selector }) }),
    } };
}

export async function runTaskContext({ online, mode, taskOptions, budgetMs = 45000, followMs,
  createCollection, now = () => performance.now() }) {
  if (!['offload', 'recover'].includes(mode)) throw Error('PAGE_MODE_INVALID');
  limit(budgetMs, 45000, 'PAGE_BUDGET_INVALID');
  if (followMs !== undefined) limit(followMs, 900000, 'PAGE_FOLLOW_INVALID');
  const start = now();
  const options = { ...taskOptions, budgetMs: Math.min(budgetMs, followMs ?? budgetMs) };
  const invoke = async (method, options) => {
    // Each wait segment owns its adapter; drain before deciding whether to follow.
    const collection = createCollection?.(options);
    let outcome;
    try {
      outcome = await online[method](collection ? { ...options, tab: collection.tab, run: collection.run } : options);
      return outcome;
    } finally {
      await collection?.drain();
      if (outcome && collection?.lastFailure) outcome.losslessFailure = collection.lastFailure;
    }
  };
  let outcome = await invoke(mode === 'offload' ? 'offloadTask' : 'recoverTask', options);
  while (followMs !== undefined && outcome.status === 'HOST_TIMEOUT' && outcome.doNotResend === true &&
      !outcome.losslessFailure && !outcome.error) {
    const remaining = Math.floor(followMs - (now() - start));
    if (remaining < 1) break;
    outcome = await invoke('recoverTask', { ...options, budgetMs: Math.min(budgetMs, remaining) });
  }
  return outcome;
}

/** Injectable channel and operation for offline lifecycle tests; no business policy here. */
export async function runCursorPageExample({ startChannel, operation, emit = line => process.stdout.write(line) }) {
  let channel, interrupted = false;
  const stop = () => { interrupted = true; void channel?.close(); };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  try {
    channel = await startChannel();
    if (interrupted) throw Error('PAGE_HOST_INTERRUPTED');
    emit(JSON.stringify({ status: 'PAGE_CHANNEL_READY', port: channel.port,
      bootstrap: createBootstrapLoader(channel), csp_cleanup_required: CSP_REMINDER }) + '\n');
    await channel.waitForConnection();
    return await operation(channel);
  } finally {
    try { await channel?.close(); }
    finally {
      process.removeListener('SIGINT', stop);
      process.removeListener('SIGTERM', stop);
      emit(JSON.stringify({ status: 'CSP_CLEANUP_REQUIRED', csp_cleanup_required: CSP_REMINDER }) + '\n');
    }
  }
}

export function parseArguments(argv) {
  const names = ['mode', 'web-agent-root', 'evidence-directory', 'ledger', 'receipt', 'task-file', 'pack-spec',
    'source-root', 'repository', 'source-sha', 'source-ref', 'browser-id', 'tab-id', 'model', 'effort',
    'ui-profile', 'orchestration', 'kind', 'budget-ms', 'follow-ms', 'connect-timeout-ms', 'response-timeout-ms', 'collection'];
  const values = {};
  for (let i = 0; i < argv.length; i += 2) {
    const name = argv[i].slice(2);
    if (!argv[i].startsWith('--') || !names.includes(name) || Object.hasOwn(values, name) ||
        !argv[i + 1] || argv[i + 1].startsWith('--')) throw Error('PAGE_ARGUMENTS_INVALID');
    values[name] = argv[i + 1];
  }
  for (const name of ['mode', 'web-agent-root', 'evidence-directory', 'ledger', 'receipt', 'repository',
    'source-sha', 'source-ref', 'browser-id', 'tab-id', 'model', 'effort', 'ui-profile', 'orchestration', 'kind']) required(values[name]);
  values.collection ??= 'lossless';
  if (!['lossless', 'rendered'].includes(values.collection)) throw Error('PAGE_ARGUMENTS_INVALID');
  if (!['offload', 'recover'].includes(values.mode) || !['review', 'analysis'].includes(values.kind) ||
      values['ui-profile'] !== 'chinese-ordinary-chat' || !/^[a-f0-9]{40}$/.test(values['source-sha']) ||
      !/^[\w.-]+\/[\w.-]+$/.test(values.repository)) throw Error('PAGE_ARGUMENTS_INVALID');
  if (Boolean(values['task-file']) === Boolean(values['pack-spec'])) throw Error('TASK_INPUT_REQUIRED');
  if (values['pack-spec']) required(values['source-root']);
  return values;
}

export async function main(argv = process.argv.slice(2), emit = line => process.stdout.write(line)) {
  let lifecycleStarted = false;
  try {
    const v = parseArguments(argv);
    const budgetMs = limit(Number(v['budget-ms'] ?? 45000), 45000, 'PAGE_BUDGET_INVALID');
    const followMs = v['follow-ms'] === undefined ? undefined : limit(Number(v['follow-ms']), 900000, 'PAGE_FOLLOW_INVALID');
    const responseTimeoutMs = limit(Number(v['response-timeout-ms'] ?? 8000), 45000, 'PAGE_TIMEOUT_INVALID');
    const connectTimeoutMs = limit(Number(v['connect-timeout-ms'] ?? 120000), 300000, 'PAGE_CONNECT_TIMEOUT_INVALID');
    const root = resolve(v['web-agent-root']), evidenceDirectory = resolve(v['evidence-directory']);
    const load = name => import(pathToFileURL(join(root, 'adapters/generic', name)).href);
    const [transport, online, packing, answers] = await Promise.all([
      load('local_transport.mjs'), load('online_host.mjs'), load('task_pack.mjs'), load('answer_result.mjs'),
    ]);
    const [lossless, browser] = v.collection === 'lossless'
      ? await Promise.all([load('lossless_response.mjs'), load('browser_host.mjs')]) : [];
    const receiptPath = resolve(v.receipt);
    // Reuse the adapter's lexical AND real-path evidence/ledger checks before any writes.
    const run = transport.localTransport({ root, evidenceDirectory, ledger: resolve(v.ledger), receiptPath });
    transport.localTransport({ root, evidenceDirectory, ledger: receiptPath, receiptPath });
    if (v.mode === 'offload' && existsSync(receiptPath)) throw Error('RECEIPT_EXISTS_USE_RECOVERY');
    if (v.mode === 'recover') {
      const target = transport.loadPrepared(receiptPath).browser_target;
      if (target.browser_context !== v['browser-id'] || target.tab_id !== v['tab-id'] ||
          target.model_label !== v.model || target.effort_label !== v.effort) throw Error('BOUND_TARGET_MISMATCH');
    }
    mkdirSync(evidenceDirectory, { recursive: true });
    lifecycleStarted = true;
    const outcome = await runCursorPageExample({ emit,
      startChannel: () => startCursorPageChannel({ responseTimeoutMs, connectTimeoutMs }),
      operation: async channel => {
        let taskFile = v['task-file'];
        if (v.mode === 'offload' && v['pack-spec']) {
          const spec = JSON.parse(readFileSync(v['pack-spec'], 'utf8').replace(/^\uFEFF/, ''));
          if (spec.kind !== v.kind) throw Error('PACK_KIND_MISMATCH');
          const packed = packing.pack(spec, { repoRoot: resolve(v['source-root']) });
          taskFile = join(evidenceDirectory, 'packed-task.txt');
          writeFileSync(taskFile, packed.text, { encoding: 'utf8', flag: 'wx' });
        }
        const tab = createCursorPageRelay({ evidenceDirectory, viewId: v['tab-id'], browserId: v['browser-id'],
          pageChannel: channel, operationTimeoutMs: responseTimeoutMs });
        return runTaskContext({ online, mode: v.mode, budgetMs, followMs,
          createCollection: lossless ? options => lossless.createLosslessCollection({
            tab: options.tab, run: options.run, readPage: browser.readChatGPTPage,
          }) : undefined, taskOptions: {
          tab, run, browserId: v['browser-id'], evidenceDirectory, repository: v.repository,
          sourceSha: v['source-sha'], sourceRef: v['source-ref'], taskFile, orchestration: v.orchestration,
          requestedModel: v.model, requestedEffort: v.effort, observe: online.observeNewChat,
          ...online.CHINESE_ORDINARY_CHAT, maxCharacters: 65536,
        } });
      },
    });
    const output = outcome.status === 'COLLECTED'
      ? { status: outcome.status, doNotResend: true, task_id: outcome.task_id, receipt_path: outcome.receipt_path,
        ...answers.summarizeAnswer(outcome, { kind: v.kind }) }
      : { ...outcome, review_approved: false };
    emit(JSON.stringify(output) + '\n');
    return output;
  } finally {
    if (!lifecycleStarted) emit(JSON.stringify({ status: 'CSP_CLEANUP_REQUIRED', csp_cleanup_required: CSP_REMINDER }) + '\n');
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(result => { process.exitCode = result.status !== 'COLLECTED' ? 3 : result.parse_ok ? 0 : 2; })
    .catch(error => { process.stderr.write((/^[A-Z][A-Z0-9_]+$/.test(error.message)
      ? error.message : 'PAGE_HOST_FAILED') + '\n'); process.exitCode = 2; });
}

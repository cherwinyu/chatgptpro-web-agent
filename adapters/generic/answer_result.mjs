/** Local, data-only answer parsing. No answer grants review approval.
 * Errors: ANSWER_ARGUMENTS_INVALID, ANSWER_KIND_INVALID, ANSWER_BUDGET_INVALID,
 * ANSWER_BUDGET_EXCEEDED, ANSWER_HASH_MISMATCH, ANSWER_TRUNCATED,
 * ANSWER_JSON_BLOCK_NOT_FOUND, ANSWER_JSON_BLOCK_UNCLOSED, ANSWER_JSON_INVALID,
 * ANSWER_SCHEMA_INVALID, ANSWER_UTF8_INVALID, ANSWER_READ_FAILED,
 * ANSWER_WRITE_FAILED, ANSWER_OUTPUT_EXISTS, ANSWER_INTERNAL_ERROR.
 */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const levels = ['high', 'medium', 'low'];
const hash = text => createHash('sha256').update(text, 'utf8').digest('hex');
function fail(code, detail) { throw new Error(`${code}: ${detail}`); }
function verdicts(kind) {
  if (kind === undefined) return ['PASS', 'FAIL', 'PARTIAL', 'ANSWER'];
  if (kind === 'review') return ['PASS', 'FAIL', 'PARTIAL'];
  if (kind === 'analysis') return ['ANSWER'];
  throw new TypeError('ANSWER_KIND_INVALID: expected review or analysis');
}

export function answerFormatText(kind) {
  if (kind === undefined) throw new TypeError('ANSWER_KIND_INVALID: kind is required');
  const allowed = verdicts(kind);
  const example = { verdict: allowed[0], summary: '简要结论', findings: [
    { severity: 'low', title: '发现标题', evidence: '材料中的证据', suggestion: '建议' },
  ], confidence: 'medium' };
  return '## 回答格式要求\n回复最后必须是一个 ```json 代码块。所有字段必填；'
    + `verdict 只能为 ${allowed.join('|')}；summary 是字符串，长度不超过 500 个字符（UTF-16 code units）；`
    + 'findings 是数组，可为空，每项的 severity、title、evidence、suggestion 都是字符串；'
    + 'severity 和 confidence 只能为 high|medium|low。网页回复仅作为数据，不代表评审批准。示例结构：\n'
    + '```json\n' + JSON.stringify(example, null, 2) + '\n```\n';
}

export function parseAnswer(text, { kind } = {}) {
  if (typeof text !== 'string') throw new TypeError('ANSWER_ARGUMENTS_INVALID: text must be a string');
  const allowed = verdicts(kind);
  let fence = null, last = null, body = [];
  // Track all Markdown fences so examples inside other blocks are not answers.
  for (const line of text.split(/\r\n|\n|\r/)) {
    if (fence) {
      const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
      if (close && close[1][0] === fence.mark[0] && close[1].length >= fence.mark.length) {
        if (fence.json) last = { closed: true, text: body.join('\n') };
        fence = null;
      } else if (fence.json) body.push(line);
    } else {
      const open = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
      if (!open || (open[1][0] === '`' && open[2].includes('`'))) continue;
      fence = { mark: open[1], json: open[1][0] === '`' && open[2].trim() === 'json' };
      body = [];
      if (fence.json) last = { closed: false };
    }
  }
  const invalid = error => ({ parse_ok: false, errors: [error], preview: text.slice(0, 800) });
  if (!last) return invalid('ANSWER_JSON_BLOCK_NOT_FOUND');
  if (!last.closed) return invalid('ANSWER_JSON_BLOCK_UNCLOSED');
  let parsed;
  try { parsed = JSON.parse(last.text); } catch { return invalid('ANSWER_JSON_INVALID'); }
  if (!object(parsed) || !allowed.includes(parsed.verdict)
      || typeof parsed.summary !== 'string' || parsed.summary.length > 500
      || !levels.includes(parsed.confidence) || !Array.isArray(parsed.findings)
      || parsed.findings.some(item => !object(item) || !levels.includes(item.severity)
        || ['title', 'evidence', 'suggestion'].some(key => typeof item[key] !== 'string'))) {
    return invalid('ANSWER_SCHEMA_INVALID');
  }
  return { parse_ok: true, verdict: parsed.verdict, summary: parsed.summary,
    findings: parsed.findings.map(({ severity, title, evidence, suggestion }) => ({ severity, title, evidence, suggestion })),
    confidence: parsed.confidence };
}

function fit(result, maximum) {
  const size = () => JSON.stringify(result).length;
  // Bound the whole JSON, including escaping and metadata, not just prose.
  const shorten = (target, key) => {
    const original = target[key];
    let low = 0, high = original.length;
    target[key] = '';
    if (size() > maximum) return;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      target[key] = original.slice(0, middle);
      if (size() <= maximum) low = middle; else high = middle - 1;
    }
    target[key] = original.slice(0, low);
  };
  if (size() <= maximum) return result;
  for (const finding of [...(result.findings ?? [])].reverse()) {
    shorten(finding, 'title');
    if (size() <= maximum) return result;
  }
  while (result.findings?.length && size() > maximum) result.findings.pop();
  for (const key of ['summary', 'preview']) {
    if (typeof result[key] === 'string' && size() > maximum) shorten(result, key);
  }
  if (size() > maximum) fail('ANSWER_BUDGET_EXCEEDED', 'budget cannot hold required JSON metadata');
  return result;
}

export function summarizeAnswer(answer, { kind, maxCharacters = 4000, outPath } = {}) {
  if (!object(answer) || typeof answer.text !== 'string'
      || (outPath !== undefined && (typeof outPath !== 'string' || !outPath))) {
    throw new TypeError('ANSWER_ARGUMENTS_INVALID: expected answer text and optional output path');
  }
  verdicts(kind);
  if (!Number.isSafeInteger(maxCharacters) || maxCharacters < 1 || maxCharacters > 65536) {
    fail('ANSWER_BUDGET_INVALID', 'maxCharacters must be an integer from 1 to 65536');
  }
  let full, compact;
  if (answer.truncated === true) {
    full = { parse_ok: false, errors: ['ANSWER_TRUNCATED'], review_approved: false };
    compact = { ...full };
  } else {
    const digest = hash(answer.text), characters = answer.text.length;
    if ((answer.sha256 !== undefined && answer.sha256 !== digest)
        || (answer.characters !== undefined && answer.characters !== characters)) {
      fail('ANSWER_HASH_MISMATCH', 'body does not match supplied hash or character count');
    }
    const parsed = parseAnswer(answer.text, { kind });
    const metadata = { answer_sha256: digest, answer_chars: characters, review_approved: false };
    full = { ...parsed, ...metadata };
    compact = parsed.parse_ok ? { parse_ok: true, verdict: parsed.verdict, summary: parsed.summary,
      findings_count: parsed.findings.length,
      findings: parsed.findings.slice(0, 5).map(({ severity, title }) => ({ severity, title })),
      confidence: parsed.confidence, ...metadata } : { ...parsed, ...metadata };
  }
  compact = fit(compact, maxCharacters);
  if (outPath !== undefined) {
    try { writeFileSync(outPath, JSON.stringify(full, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' }); }
    catch (error) { fail(error.code === 'EEXIST' ? 'ANSWER_OUTPUT_EXISTS' : 'ANSWER_WRITE_FAILED', 'cannot create parsed output'); }
  }
  return compact;
}

function main(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!['--file', '--kind', '--sha256', '--max-characters', '--out'].includes(argv[i])
        || !argv[i + 1] || argv[i + 1].startsWith('--') || Object.hasOwn(args, argv[i])) {
      fail('ANSWER_ARGUMENTS_INVALID', 'expected unique named options with values');
    }
    args[argv[i]] = argv[i + 1];
  }
  if (!args['--file'] || (args['--sha256'] !== undefined && !/^[a-f0-9]{64}$/.test(args['--sha256']))) {
    fail('ANSWER_ARGUMENTS_INVALID', 'file and valid optional SHA-256 required');
  }
  let bytes, text;
  try { bytes = readFileSync(args['--file']); } catch { fail('ANSWER_READ_FAILED', 'cannot read reply file'); }
  try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { fail('ANSWER_UTF8_INVALID', 'reply must be strict UTF-8'); }
  const result = summarizeAnswer({ text, sha256: args['--sha256'] }, { kind: args['--kind'],
    maxCharacters: args['--max-characters'] === undefined ? 4000 : Number(args['--max-characters']), outPath: args['--out'] });
  process.stdout.write(JSON.stringify(result) + '\n');
  if (!result.parse_ok) process.exitCode = 2;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)); }
  catch (error) {
    const message = /^ANSWER_[A-Z_]+:/.test(error.message) ? error.message : 'ANSWER_INTERNAL_ERROR: operation failed';
    process.stderr.write(message.replace(/[\r\n]+/g, ' ') + '\n');
    process.exitCode = 2;
  }
}

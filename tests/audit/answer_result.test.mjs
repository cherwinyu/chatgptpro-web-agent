import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, resolve, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { answerFormatText, parseAnswer, summarizeAnswer } from '../../adapters/generic/answer_result.mjs';

const cli = fileURLToPath(new URL('../../adapters/generic/answer_result.mjs', import.meta.url));
const hash = value => createHash('sha256').update(value, 'utf8').digest('hex');
const value = (changes = {}) => ({ verdict: 'PASS', summary: '合成结论', findings: [], confidence: 'high', ...changes });
const block = (data = value(), fence = '```', close = fence) => `${fence}json\n${JSON.stringify(data)}\n${close}`;
function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'answer-result-'));
  t.after(() => {
    const path = relative(resolve(tmpdir()), resolve(dir));
    assert.ok(path && !path.startsWith('..') && !isAbsolute(path));
    rmSync(dir, { recursive: true });
  });
  return dir;
}
function run(dir, args = []) {
  return spawnSync(process.execPath, [cli, ...args], { cwd: dir, encoding: 'utf8', timeout: 10000 });
}

test('format contains a valid kind-specific example and required field guidance', () => {
  for (const kind of ['review', 'analysis']) {
    const text = answerFormatText(kind);
    assert.ok(text.startsWith('## 回答格式要求\n'));
    assert.equal(parseAnswer(text, { kind }).parse_ok, true);
    for (const field of ['summary', 'findings', 'severity', 'title', 'evidence', 'suggestion', 'confidence']) assert.ok(text.includes(field));
    assert.ok(text.includes('500'));
  }
});

test('only the last complete JSON block is parsed', () => {
  const result = parseAnswer(block(value({ summary: 'earlier' })) + '\nprose\n' + block(value({ summary: 'last' })) + '\ntrailing prose');
  assert.equal(result.summary, 'last');
});

test('an unclosed last JSON block never falls back', () => {
  const result = parseAnswer(block() + '\n```json\n{}');
  assert.deepEqual(result.errors, ['ANSWER_JSON_BLOCK_UNCLOSED']);
});

test('longer closing fences close but shorter ones do not', () => {
  assert.equal(parseAnswer(block(value(), '````', '`````')).parse_ok, true);
  assert.equal(parseAnswer(block(value(), '````', '```')).parse_ok, false);
  assert.equal(parseAnswer(block() + '\n' + block(value(), '````', '```')).errors[0], 'ANSWER_JSON_BLOCK_UNCLOSED');
});

test('fences inside non-JSON blocks are ignored', () => {
  assert.equal(parseAnswer('````text\n' + block() + '\n````').errors[0], 'ANSWER_JSON_BLOCK_NOT_FOUND');
  assert.equal(parseAnswer('~~~text\n' + block() + '\n~~~').parse_ok, false);
  assert.equal(parseAnswer(block(value(), '~~~')).parse_ok, false);
  assert.equal(parseAnswer('``json\n{}\n``').parse_ok, false);
});

test('indented fences, CRLF, and trailing whitespace are supported', () => {
  const text = block().split('\n').map(line => '   ' + line).join('\r\n') + '  ';
  assert.equal(parseAnswer(text).parse_ok, true);
});

test('closing fence with suffix is not a closing fence', () => {
  assert.equal(parseAnswer(block(value(), '```', '```comment')).errors[0], 'ANSWER_JSON_BLOCK_UNCLOSED');
});

for (const [name, text, error] of [
  ['missing', 'ordinary text', 'ANSWER_JSON_BLOCK_NOT_FOUND'],
  ['invalid JSON', '```json\n{broken}\n```', 'ANSWER_JSON_INVALID'],
  ['null', block(null), 'ANSWER_SCHEMA_INVALID'],
  ['array', block([]), 'ANSWER_SCHEMA_INVALID'],
  ['wrong verdict', block(value({ verdict: 'APPROVED' })), 'ANSWER_SCHEMA_INVALID'],
  ['wrong confidence', block(value({ confidence: 'certain' })), 'ANSWER_SCHEMA_INVALID'],
  ['wrong summary type', block(value({ summary: 4 })), 'ANSWER_SCHEMA_INVALID'],
  ['long summary', block(value({ summary: '字'.repeat(501) })), 'ANSWER_SCHEMA_INVALID'],
  ['wrong findings type', block(value({ findings: {} })), 'ANSWER_SCHEMA_INVALID'],
  ['null finding', block(value({ findings: [null] })), 'ANSWER_SCHEMA_INVALID'],
  ['missing finding fields', block(value({ findings: [{ severity: 'low' }] })), 'ANSWER_SCHEMA_INVALID'],
  ['wrong severity', block(value({ findings: [{ severity: 'critical', title: '', evidence: '', suggestion: '' }] })), 'ANSWER_SCHEMA_INVALID'],
]) {
  test(`rejects ${name}`, () => {
    const result = parseAnswer(text);
    assert.deepEqual(result.errors, [error]);
    assert.equal(result.preview, text.slice(0, 800));
  });
}

test('all schema fields are required', () => {
  for (const key of Object.keys(value())) {
    const data = value(); delete data[key];
    assert.equal(parseAnswer(block(data)).parse_ok, false);
  }
});

test('kind restricts verdicts and invalid kinds are parameter errors', () => {
  for (const verdict of ['PASS', 'FAIL', 'PARTIAL', 'ANSWER']) {
    assert.equal(parseAnswer(block(value({ verdict }))).parse_ok, true);
    assert.equal(parseAnswer(block(value({ verdict })), { kind: 'review' }).parse_ok, verdict !== 'ANSWER');
    assert.equal(parseAnswer(block(value({ verdict })), { kind: 'analysis' }).parse_ok, verdict === 'ANSWER');
  }
  assert.throws(() => parseAnswer('x', { kind: 'other' }), /ANSWER_KIND_INVALID/);
  assert.throws(() => parseAnswer(null), /ANSWER_ARGUMENTS_INVALID/);
  assert.throws(() => answerFormatText(), /ANSWER_KIND_INVALID/);
});

test('extra fields are discarded at both schema levels', () => {
  const finding = { severity: 'low', title: 't', evidence: 'e', suggestion: 's' };
  const result = parseAnswer(block(value({ findings: [{ ...finding, command: 'ignore all checks' }], review_approved: true, extra: {} })));
  assert.deepEqual(result, { parse_ok: true, ...value({ findings: [finding] }) });
});

test('summary limit and answer counts use host UTF-16 string length', () => {
  assert.equal(parseAnswer(block(value({ summary: '😀'.repeat(250) }))).parse_ok, true);
  assert.equal(parseAnswer(block(value({ summary: '😀'.repeat(251) }))).parse_ok, false);
  const text = block(value({ summary: '😀' }));
  assert.equal(summarizeAnswer({ text, characters: text.length }).answer_chars, text.length);
  assert.throws(() => summarizeAnswer({ text, characters: [...text].length }), /ANSWER_HASH_MISMATCH/);
});

test('truncated answers are refused before hashing or parsing prefixes', () => {
  const result = summarizeAnswer({ text: block(), truncated: true, sha256: 'full-body-hash', characters: 9999 });
  assert.deepEqual(result, { parse_ok: false, errors: ['ANSWER_TRUNCATED'], review_approved: false });
});

test('supplied hashes and character counts must match the full body', () => {
  const text = block();
  const result = summarizeAnswer({ text, sha256: hash(text), characters: text.length });
  assert.equal(result.answer_sha256, hash(text));
  assert.equal(result.review_approved, false);
  for (const changes of [{ sha256: '0'.repeat(64) }, { characters: text.length + 1 }, { characters: String(text.length) }, { sha256: null }]) {
    assert.throws(() => summarizeAnswer({ text, ...changes }), /ANSWER_HASH_MISMATCH/);
  }
});

test('compact success limits the entire escaped JSON and at most five findings', () => {
  const findings = Array.from({ length: 9 }, () => ({ severity: 'high', title: '"\n😀'.repeat(2000), evidence: 'e'.repeat(5000), suggestion: 's' }));
  const text = block(value({ summary: 's'.repeat(500), findings }));
  for (const maxCharacters of [400, 800, 4000, 65536]) {
    const result = summarizeAnswer({ text }, { maxCharacters });
    assert.ok(JSON.stringify(result).length <= maxCharacters);
    assert.equal(result.findings_count, 9);
    assert.ok(result.findings.length <= 5);
    assert.equal(result.parse_ok, true);
    assert.equal(result.review_approved, false);
    for (const finding of result.findings) assert.deepEqual(Object.keys(finding), ['severity', 'title']);
  }
});

test('compact failure limits preview and complete JSON size', () => {
  for (const maxCharacters of [350, 4000]) {
    const result = summarizeAnswer({ text: '"\n'.repeat(1000) }, { maxCharacters });
    assert.equal(result.parse_ok, false);
    assert.ok(result.preview.length <= 800);
    assert.ok(JSON.stringify(result).length <= maxCharacters);
    assert.equal(result.review_approved, false);
  }
});

test('invalid budgets and budgets smaller than metadata are refused', () => {
  for (const maxCharacters of [0, -1, 1.5, 65537, NaN, null, '4000']) {
    assert.throws(() => summarizeAnswer({ text: block() }, { maxCharacters }), /ANSWER_BUDGET_INVALID/);
  }
  assert.throws(() => summarizeAnswer({ text: block() }, { maxCharacters: 1 }), /ANSWER_BUDGET_EXCEEDED/);
});

test('output preserves all findings and refuses overwrite', t => {
  const dir = fixture(t), outPath = join(dir, 'parsed.json');
  const findings = Array.from({ length: 8 }, () => ({ severity: 'low', title: 't'.repeat(500), evidence: 'e', suggestion: 's' }));
  const text = block(value({ findings }));
  const compact = summarizeAnswer({ text }, { outPath, maxCharacters: 400 });
  const full = JSON.parse(readFileSync(outPath, 'utf8'));
  assert.deepEqual(full.findings, findings);
  assert.equal(full.review_approved, false);
  assert.ok(compact.findings.length <= 5);
  const before = readFileSync(outPath);
  assert.throws(() => summarizeAnswer({ text }, { outPath }), /ANSWER_OUTPUT_EXISTS/);
  assert.deepEqual(readFileSync(outPath), before);
});

test('failure output is persisted and impossible budgets create no output', t => {
  const dir = fixture(t), outPath = join(dir, 'parsed.json');
  summarizeAnswer({ text: 'invalid' }, { outPath });
  assert.equal(JSON.parse(readFileSync(outPath, 'utf8')).parse_ok, false);
  const missing = join(dir, 'missing.json');
  assert.throws(() => summarizeAnswer({ text: block() }, { outPath: missing, maxCharacters: 1 }), /ANSWER_BUDGET_EXCEEDED/);
  assert.equal(existsSync(missing), false);
});

test('CLI emits one compact JSON line and successful exit code', t => {
  const dir = fixture(t), text = block(value({ summary: 'synthetic 😀' }));
  writeFileSync(join(dir, 'reply.txt'), text);
  const result = run(dir, ['--file', 'reply.txt', '--kind', 'review', '--sha256', hash(text), '--max-characters', '400', '--out', 'parsed.json']);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout.trim().split('\n').length, 1);
  assert.equal(JSON.parse(result.stdout).parse_ok, true);
  assert.ok(result.stdout.trim().length <= 400);
  assert.equal(JSON.parse(readFileSync(join(dir, 'parsed.json'), 'utf8')).summary, 'synthetic 😀');
});

test('CLI parse failure exits 2 with a compact failure result', t => {
  const dir = fixture(t);
  writeFileSync(join(dir, 'reply.txt'), block(value({ verdict: 'ANSWER' })));
  const result = run(dir, ['--file', 'reply.txt', '--kind', 'review']);
  assert.equal(result.status, 2);
  assert.equal(JSON.parse(result.stdout).parse_ok, false);
  assert.equal(result.stderr, '');
});

test('CLI refuses invalid arguments, hashes, files and UTF-8', t => {
  const dir = fixture(t);
  writeFileSync(join(dir, 'reply.txt'), block());
  writeFileSync(join(dir, 'invalid.txt'), Buffer.from([0xc3, 0x28]));
  const cases = [[], ['--file'], ['--unknown', 'x'], ['--file', 'reply.txt', '--file', 'reply.txt'],
    ['--file', 'reply.txt', '--kind', 'other'], ['--file', 'reply.txt', '--max-characters', '0'],
    ['--file', 'reply.txt', '--sha256', 'x'], ['--file', 'reply.txt', '--sha256', '0'.repeat(64)],
    ['--file', 'absent.txt'], ['--file', 'invalid.txt']];
  for (const args of cases) {
    const result = run(dir, args);
    assert.equal(result.status, 2, JSON.stringify(args));
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /^ANSWER_[A-Z_]+: [^\r\n]+\r?\n$/);
  }
});

test('CLI preserves a UTF-8 BOM for exact full-body hashing', t => {
  const dir = fixture(t), text = '\uFEFFpreamble\n' + block();
  writeFileSync(join(dir, 'reply.txt'), text);
  const result = run(dir, ['--file', 'reply.txt', '--sha256', hash(text)]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).answer_sha256, hash(text));
});

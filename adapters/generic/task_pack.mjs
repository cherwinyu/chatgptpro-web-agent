/** Local task packaging. Errors: PACK_SPEC_INVALID, PACK_BUDGET_INVALID,
 * PACK_BUDGET_EXCEEDED, PACK_PATH_REFUSED, PACK_LINK_REFUSED,
 * PACK_SOURCE_NOT_FILE, PACK_READ_FAILED, PACK_BINARY_REFUSED,
 * PACK_HEADING_NOT_FOUND, PACK_LINES_OUT_OF_RANGE, PACK_GIT_FAILED,
 * PACK_DIFF_UNTRACKED, PACK_DIFF_EMPTY,
 * PACK_ARGUMENTS_INVALID, PACK_OUTPUT_EXISTS, PACK_WRITE_FAILED, PACK_INTERNAL_ERROR.
 */
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { lstatSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { basename, dirname, isAbsolute, parse, relative, resolve, win32 } from 'node:path';
import { fileURLToPath } from 'node:url';
import { answerFormatText } from './answer_result.mjs';

const hash = text => createHash('sha256').update(text, 'utf8').digest('hex');
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const controls = /[\x00-\x1f\x7f-\x9f]/;
function fail(code, detail) { throw new Error(`${code}: ${detail}`); }

function safeName(input) {
  if (typeof input !== 'string' || !input || controls.test(input) || /[:]/.test(input)
      || isAbsolute(input) || win32.isAbsolute(input)) fail('PACK_PATH_REFUSED', 'relative paths required');
  const parts = input.replaceAll('\\', '/').split('/');
  for (const part of parts) {
    if (part === '..' || /[. ]$/.test(part) && part !== '.'
        || /[<>"|?*]/.test(part) || /~\d/.test(part)
        || /^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)/i.test(part)
        || /^(\.git|node_modules)$/i.test(part) || /^\.env(?:\.|$)/i.test(part)
        || /\.(pem|key)$/i.test(part) || /secret|token|credential|password|id_rsa|apikey|api_key|private_key/i.test(part)) {
      fail('PACK_PATH_REFUSED', 'unsafe or sensitive path component');
    }
  }
  const name = parts.filter(part => part && part !== '.').join('/');
  if (!name) fail('PACK_PATH_REFUSED', 'a source path is required');
  return name;
}

function noLinks(full, missing = false) {
  const ancestors = [];
  for (let item = full;; item = dirname(item)) {
    ancestors.unshift(item);
    if (dirname(item) === item) break;
  }
  let stat;
  for (const item of ancestors) {
    try { stat = lstatSync(item); }
    catch (error) {
      if (missing && error.code === 'ENOENT') return null;
      fail(error.code === 'ENOTDIR' ? 'PACK_SOURCE_NOT_FILE' : 'PACK_READ_FAILED', 'cannot inspect source path');
    }
    if (stat.isSymbolicLink()) fail('PACK_LINK_REFUSED', 'symbolic links and junctions are refused');
    if (item !== full && !stat.isDirectory()) fail('PACK_SOURCE_NOT_FILE', 'ancestor is not a directory');
  }
  return stat;
}

function checkedPath(root, input, missing = false) {
  const path = safeName(input), full = resolve(root, path);
  return { path, full, stat: noLinks(full, missing) };
}

function decode(bytes) {
  if (bytes.includes(0)) fail('PACK_BINARY_REFUSED', 'NUL bytes are refused');
  try { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { fail('PACK_BINARY_REFUSED', 'strict UTF-8 required'); }
}

function read(full) {
  let bytes;
  try { bytes = readFileSync(full); } catch { fail('PACK_READ_FAILED', 'cannot read source file'); }
  return decode(bytes);
}

function select(text, source) {
  if (source.lines === undefined && source.heading === undefined) return { text, selector: 'full' };
  const lines = text.split(/\r\n|\n|\r/);
  if (lines.at(-1) === '') lines.pop();
  if (source.lines !== undefined) {
    const match = /^(\d+)-(\d+)$/.exec(source.lines);
    const first = Number(match?.[1]), last = Number(match?.[2]);
    if (!match || !Number.isSafeInteger(first) || !Number.isSafeInteger(last)
        || first < 1 || last < first || last > lines.length) {
      fail('PACK_LINES_OUT_OF_RANGE', 'line range exceeds source');
    }
    return { text: lines.slice(first - 1, last).join('\n'), selector: `lines:${source.lines}` };
  }
  let start = -1, level = 0, fence = null;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (fence) {
      const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
      if (close && close[1][0] === fence[0] && close[1].length >= fence.length) fence = null;
      continue;
    }
    const open = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (open && !(open[1][0] === '`' && open[2].includes('`'))) { fence = open[1]; continue; }
    const heading = /^ {0,3}(#{1,6})(?:[ \t]+|$)/.exec(line);
    if (start >= 0 && heading && heading[1].length <= level) {
      return { text: lines.slice(start, index).join('\n'), selector: `heading:${source.heading}` };
    }
    if (start < 0 && heading && line.trim() === source.heading.trim()) {
      start = index; level = heading[1].length;
    }
  }
  if (start < 0) fail('PACK_HEADING_NOT_FOUND', 'requested heading not found');
  return { text: lines.slice(start).join('\n'), selector: `heading:${source.heading}` };
}

function git(root, args) {
  const result = spawnSync('git', ['--literal-pathspecs', '-c', 'core.fsmonitor=false', ...args], {
    cwd: root, shell: false, windowsHide: true, timeout: 30000, maxBuffer: 32 * 1024 * 1024,
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GIT_/i.test(key))),
  });
  if (result.error || result.status !== 0) fail('PACK_GIT_FAILED', 'git operation failed');
  return result.stdout;
}

function diffMaterial(root, diff) {
  const paths = diff.paths.map(path => checkedPath(root, path, true).path);
  const top = git(root, ['rev-parse', '--show-toplevel']).toString('utf8').trim();
  // Windows temp paths may use an 8.3 ancestor spelling; compare filesystem paths.
  let sameRoot = false;
  try { sameRoot = relative(realpathSync.native(root), realpathSync.native(top)) === ''; } catch { /* Refuse below. */ }
  if (!sameRoot) fail('PACK_GIT_FAILED', 'repoRoot must be the git worktree root');
  const base = git(root, ['rev-parse', '--verify', `${diff.base}^{commit}`]).toString('ascii').trim();
  if (!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(base)) fail('PACK_GIT_FAILED', 'invalid resolved commit');
  const common = ['diff', '--no-color', '--no-ext-diff', '--no-textconv', '--no-renames', '--ignore-submodules=none'];
  // Raw records expose modes even when Windows checks out a link as a plain file.
  const records = new TextDecoder('utf-8', { fatal: true });
  let untracked;
  try { untracked = records.decode(git(root, ['ls-files', '--others', '--exclude-standard', '-z', '--', ...paths])).split('\0'); }
  catch (error) {
    if (error.message.startsWith('PACK_')) throw error;
    fail('PACK_PATH_REFUSED', 'git paths must be UTF-8');
  }
  if (untracked.pop() !== '') fail('PACK_GIT_FAILED', 'invalid git records');
  for (const path of untracked) {
    if (checkedPath(root, path).path !== path) fail('PACK_PATH_REFUSED', 'ambiguous git path');
  }
  if (untracked.length) {
    fail('PACK_DIFF_UNTRACKED', `untracked files in diff.paths: ${untracked.slice(0, 20).join(', ')}`
      + (untracked.length > 20 ? ` (${untracked.length} total)` : '') + '; run git add first or use sources');
  }
  let entries;
  try { entries = records.decode(git(root, [...common, '--raw', '--no-abbrev', '-z', base, '--', ...paths])).split('\0'); }
  catch (error) {
    if (error.message.startsWith('PACK_')) throw error;
    fail('PACK_PATH_REFUSED', 'git paths must be UTF-8');
  }
  if (entries.pop() !== '') fail('PACK_GIT_FAILED', 'invalid git records');
  for (let i = 0; i < entries.length; i += 2) {
    const header = /^:(\d{6}) (\d{6}) ([a-f0-9]+) ([a-f0-9]+) [A-Z]$/.exec(entries[i]);
    if (!header || entries[i + 1] === undefined) fail('PACK_GIT_FAILED', 'invalid git records');
    if ([header[1], header[2]].some(mode => !['000000', '100644', '100755'].includes(mode))) {
      fail('PACK_LINK_REFUSED', 'git links and submodules are refused');
    }
    const entry = checkedPath(root, entries[i + 1], true);
    if (entry.path !== entries[i + 1]) fail('PACK_PATH_REFUSED', 'ambiguous git path');
    if (entry.stat) {
      if (!entry.stat.isFile()) fail('PACK_SOURCE_NOT_FILE', 'changed source is not a file');
      read(entry.full);
    }
    if (header[1] !== '000000') decode(git(root, ['cat-file', 'blob', header[3]]));
    // The index may contain a staged link hidden by a regular worktree file.
    const staged = git(root, ['ls-files', '--stage', '-z', '--', entry.path]);
    for (const item of staged.toString('utf8').split('\0').filter(Boolean)) {
      if (!/^(100644|100755) [a-f0-9]+ 0\t/.test(item)) fail('PACK_LINK_REFUSED', 'unsafe or unmerged index mode');
    }
  }
  const text = decode(git(root, [...common, '--no-relative', base, '--', ...paths]));
  if (!text) fail('PACK_DIFF_EMPTY', 'base and worktree have no differences in diff.paths; base must be a commit before the changes');
  return { path: `diff:${paths.join(',')}`, selector: `base:${base}`, text };
}

export function pack(spec, { repoRoot = process.cwd() } = {}) {
  if (!object(spec) || !['review', 'analysis'].includes(spec.kind)
      || !['goal', 'question'].every(key => typeof spec[key] === 'string' && spec[key].trim())
      || !Array.isArray(spec.sources) || typeof repoRoot !== 'string' || !repoRoot
      || (spec.constraints !== undefined && (!Array.isArray(spec.constraints) || spec.constraints.some(item => typeof item !== 'string')))
      || spec.sources.some(source => !object(source) || typeof source.path !== 'string'
        || source.heading !== undefined && source.lines !== undefined
        || source.heading !== undefined && (typeof source.heading !== 'string' || !/^#{1,6}[ \t]+[^\r\n]+$/.test(source.heading))
        || source.lines !== undefined && typeof source.lines !== 'string')
      || (spec.diff !== undefined && (!object(spec.diff) || typeof spec.diff.base !== 'string' || !spec.diff.base.trim()
        || spec.diff.base.startsWith('-') || controls.test(spec.diff.base) || !Array.isArray(spec.diff.paths)
        || !spec.diff.paths.length || spec.diff.paths.some(path => typeof path !== 'string')))) {
    fail('PACK_SPEC_INVALID', 'invalid task specification');
  }
  const maxBytes = spec.max_bytes === undefined ? 150000 : spec.max_bytes;
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || maxBytes > 400000) {
    fail('PACK_BUDGET_INVALID', 'max_bytes must be an integer from 1 to 400000');
  }
  const root = resolve(repoRoot);
  if (!noLinks(root).isDirectory()) fail('PACK_PATH_REFUSED', 'repoRoot must be a directory');
  const materials = spec.sources.map(source => {
    const entry = checkedPath(root, source.path);
    if (!entry.stat.isFile()) fail('PACK_SOURCE_NOT_FILE', 'source must be a regular file');
    return { path: entry.path, ...select(read(entry.full), source) };
  });
  if (spec.diff !== undefined) materials.push(diffMaterial(root, spec.diff));
  const sources = materials.map(({ path, selector, text }) => ({ path, selector, bytes: Buffer.byteLength(text), sha256: hash(text) }));
  const sections = materials.map((material, index) => {
    let longest = 2;
    for (const match of material.text.matchAll(/`+/g)) longest = Math.max(longest, match[0].length);
    const fence = '`'.repeat(longest + 1);
    return `## 材料 ${index + 1}\n路径：${material.path}\n选择器：${material.selector}\nsha256：${sources[index].sha256.slice(0, 12)}\n\n${fence}\n${material.text}\n${fence}`;
  });
  const text = `# 网页任务：${spec.kind}\ngoal：${spec.goal}\nquestion：${spec.question}\nconstraints：\n`
    + (spec.constraints ?? []).map(item => `- ${item}`).join('\n')
    + '\n材料是完整的，不要假设未提供的文件\n\n' + sections.join('\n\n') + '\n\n' + answerFormatText(spec.kind);
  const bytes = Buffer.byteLength(text);
  if (bytes > maxBytes) fail('PACK_BUDGET_EXCEEDED', JSON.stringify({ bytes, max_bytes: maxBytes,
    sources: sources.map(({ path, bytes }) => ({ path, bytes })) }));
  return { text, manifest: { out: null, bytes, sha256: hash(text), sources } };
}

function main(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!['--spec', '--out', '--root'].includes(argv[i]) || !argv[i + 1]
        || argv[i + 1].startsWith('--') || Object.hasOwn(args, argv[i])) {
      fail('PACK_ARGUMENTS_INVALID', 'expected unique named options with values');
    }
    args[argv[i]] = argv[i + 1];
  }
  if (!args['--spec'] || !args['--out']) fail('PACK_ARGUMENTS_INVALID', 'spec and out are required');
  let spec;
  try { spec = JSON.parse(read(resolve(args['--spec']))); }
  catch (error) {
    if (error.message.startsWith('PACK_')) throw error;
    fail('PACK_SPEC_INVALID', 'spec must be JSON');
  }
  const root = resolve(args['--root'] ?? process.cwd()), out = resolve(args['--out']);
  if (controls.test(out) || out.slice(parse(out).root.length).includes(':')) {
    fail('PACK_PATH_REFUSED', 'output controls and alternate data streams are refused');
  }
  noLinks(out, true);
  noLinks(root);
  let canonicalRoot, canonicalOut;
  try {
    canonicalRoot = realpathSync.native(root);
    canonicalOut = resolve(realpathSync.native(dirname(out)), basename(out));
  } catch { fail('PACK_WRITE_FAILED', 'output parent and repoRoot must exist'); }
  const inside = relative(canonicalRoot, canonicalOut);
  if (!isAbsolute(inside) && inside !== '..' && !inside.startsWith('..\\') && !inside.startsWith('../')
      && inside.split(/[\\/]/).some(part => /^\.git[. ]*$/i.test(part))) {
    fail('PACK_PATH_REFUSED', 'output cannot be inside .git');
  }
  const result = pack(spec, { repoRoot: root });
  try { writeFileSync(out, result.text, { encoding: 'utf8', flag: 'wx' }); }
  catch (error) { fail(error.code === 'EEXIST' ? 'PACK_OUTPUT_EXISTS' : 'PACK_WRITE_FAILED', 'cannot create task output'); }
  result.manifest.out = out;
  process.stdout.write(JSON.stringify(result.manifest) + '\n');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)); }
  catch (error) {
    const message = /^PACK_[A-Z_]+:/.test(error.message) ? error.message : 'PACK_INTERNAL_ERROR: operation failed';
    process.stderr.write(message.replace(/[\r\n]+/g, ' ') + '\n');
    process.exitCode = 2;
  }
}

import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, realpathSync, rmSync, existsSync, symlinkSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative, resolve, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pack } from '../../adapters/generic/task_pack.mjs';
import { answerFormatText } from '../../adapters/generic/answer_result.mjs';

const cli = fileURLToPath(new URL('../../adapters/generic/task_pack.mjs', import.meta.url));
const hash = value => createHash('sha256').update(value).digest('hex');
const spec = (changes = {}) => ({ kind: 'review', goal: '合成目标', question: '检查材料', sources: [{ path: 'source.txt' }], ...changes });
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'task-pack-'));
  writeFileSync(join(root, 'source.txt'), 'synthetic material\n');
  t.after(() => {
    const path = relative(resolve(tmpdir()), resolve(root));
    assert.ok(path && !path.startsWith('..') && !isAbsolute(path));
    rmSync(root, { recursive: true });
  });
  return root;
}
function run(root, args, env = process.env) {
  return spawnSync(process.execPath, [cli, ...args], { cwd: root, encoding: 'utf8', env, timeout: 20000 });
}
function git(root, args, input) {
  const result = spawnSync('git', args, { cwd: root, input, encoding: 'utf8', shell: false, windowsHide: true,
    env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GIT_/i.test(key))), timeout: 10000 });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}
function repository(t) {
  const root = fixture(t);
  git(root, ['init', '--quiet']);
  git(root, ['config', 'user.name', 'Synthetic Fixture']);
  git(root, ['config', 'user.email', 'fixture@example.invalid']);
  git(root, ['config', 'core.autocrlf', 'false']);
  git(root, ['config', 'core.symlinks', 'false']);
  return root;
}
function snapshot(root) {
  git(root, ['add', '--all']);
  return snapshotIndex(root);
}
function snapshotIndex(root) {
  const tree = git(root, ['write-tree']);
  const commit = git(root, ['commit-tree', tree, '-m', 'Synthetic baseline']);
  git(root, ['update-ref', 'HEAD', commit]);
  return commit;
}
const diffSpec = (base, paths = ['source.txt']) => spec({ sources: [], diff: { base, paths } });

test('pack preserves full contents and computes UTF-8 manifests', t => {
  const repoRoot = fixture(t), content = '\uFEFF合成 😀\r\n';
  writeFileSync(join(repoRoot, 'source.txt'), content);
  const result = pack(spec({ constraints: ['只读材料'] }), { repoRoot });
  assert.ok(result.text.includes(content));
  assert.ok(result.text.includes('材料是完整的，不要假设未提供的文件'));
  assert.ok(result.text.includes('只读材料'));
  assert.deepEqual(result.manifest.sources, [{ path: 'source.txt', selector: 'full', bytes: Buffer.byteLength(content), sha256: hash(content) }]);
  assert.equal(result.manifest.out, null);
  assert.equal(result.manifest.bytes, Buffer.byteLength(result.text));
  assert.equal(result.manifest.sha256, hash(result.text));
  assert.ok(result.text.includes(hash(content).slice(0, 12)));
});

test('answer format is the exact shared suffix for both kinds', t => {
  const repoRoot = fixture(t);
  for (const kind of ['review', 'analysis']) {
    const { text } = pack(spec({ kind }), { repoRoot });
    assert.equal(text.slice(text.lastIndexOf('## 回答格式要求')), answerFormatText(kind));
  }
});

for (const path of ['/absolute.txt', 'X:\\absolute.txt', 'X:relative.txt', '../outside.txt', 'folder/../../outside.txt',
  '\\\\synthetic-server\\share\\file.txt', '//synthetic-server/share/file.txt', 'line\nbreak.txt', 'file\x7f.txt', 'file\x85.txt',
  '.git/config', '.GIT/HEAD', '.env', '.env.local', 'folder/.ENV.test', 'key.pem', 'key.KEY', 'secrets.txt', 'TOKEN.json',
  'credential.txt', 'passwords.txt', 'id_rsa.pub', 'node_modules/pkg/file.txt', 'folder/node_modules/file.txt',
  'api_key.json', 'private_key.txt', '.git./config', 'source.txt:stream', 'NUL.txt', 'ENVFIL~1']) {
  test(`refuses unsafe source path ${JSON.stringify(path)}`, t => {
    const repoRoot = fixture(t);
    assert.throws(() => pack(spec({ sources: [{ path }] }), { repoRoot }), /PACK_PATH_REFUSED/);
  });
}

test('refuses directories and missing sources with stable codes', t => {
  const repoRoot = fixture(t);
  mkdirSync(join(repoRoot, 'directory'));
  assert.throws(() => pack(spec({ sources: [{ path: 'directory' }] }), { repoRoot }), /PACK_SOURCE_NOT_FILE/);
  assert.throws(() => pack(spec({ sources: [{ path: 'missing' }] }), { repoRoot }), /PACK_READ_FAILED/);
});

test('refuses file links', t => {
  const repoRoot = fixture(t);
  try { symlinkSync(join(repoRoot, 'source.txt'), join(repoRoot, 'alias.txt'), 'file'); }
  catch (error) { t.skip(`file symlink unavailable: ${error.code}`); return; }
  assert.throws(() => pack(spec({ sources: [{ path: 'alias.txt' }] }), { repoRoot }), /PACK_LINK_REFUSED/);
});

test('refuses directory links or junctions including linked repo roots', t => {
  const repoRoot = fixture(t), target = join(repoRoot, 'real'), link = join(repoRoot, 'alias');
  mkdirSync(target); writeFileSync(join(target, 'source.txt'), 'synthetic');
  try { symlinkSync(target, link, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { t.skip(`directory link unavailable: ${error.code}`); return; }
  assert.throws(() => pack(spec({ sources: [{ path: 'alias/source.txt' }] }), { repoRoot }), /PACK_LINK_REFUSED/);
  assert.throws(() => pack(spec(), { repoRoot: link }), /PACK_LINK_REFUSED/);
});

for (const [name, bytes] of [['NUL', [65, 0, 66]], ['invalid UTF-8', [0xc3, 0x28]], ['overlong UTF-8', [0xc0, 0xaf]]]) {
  test(`refuses ${name} even outside the selected lines`, t => {
    const repoRoot = fixture(t);
    writeFileSync(join(repoRoot, 'source.txt'), Buffer.concat([Buffer.from('safe\n'), Buffer.from(bytes)]));
    assert.throws(() => pack(spec({ sources: [{ path: 'source.txt', lines: '1-1' }] }), { repoRoot }), /PACK_BINARY_REFUSED/);
  });
}

test('selects line ranges without counting trailing newline as another line', t => {
  const repoRoot = fixture(t);
  writeFileSync(join(repoRoot, 'source.txt'), 'one\r\ntwo\r\nthree\r\n');
  const result = pack(spec({ sources: [{ path: 'source.txt', lines: '2-3' }] }), { repoRoot });
  assert.equal(result.manifest.sources[0].sha256, hash('two\nthree'));
  assert.equal(result.manifest.sources[0].selector, 'lines:2-3');
  for (const lines of ['0-1', '1-4', '3-2', 'x', '1-999999999999999999999']) {
    assert.throws(() => pack(spec({ sources: [{ path: 'source.txt', lines }] }), { repoRoot }), /PACK_LINES_OUT_OF_RANGE/);
  }
});

test('heading selection skips fenced fake headings and stops at peer or parent', t => {
  const repoRoot = fixture(t);
  const selected = ['## Selected', 'text', '```md', '# fake', '```suffix', '# still fake', '```', '~~~', '# fake too', '~~~', '### Child', 'child'].join('\n');
  const content = '````md\n## Selected\n````\n# Parent\n' + selected + '\n## Next\nnext\n';
  writeFileSync(join(repoRoot, 'source.txt'), content);
  const result = pack(spec({ sources: [{ path: 'source.txt', heading: '## Selected' }] }), { repoRoot });
  assert.equal(result.manifest.sources[0].sha256, hash(selected));
  assert.equal(result.manifest.sources[0].selector, 'heading:## Selected');
  assert.throws(() => pack(spec({ sources: [{ path: 'source.txt', heading: '# fake' }] }), { repoRoot }), /PACK_HEADING_NOT_FOUND/);
  const parentBounded = '### Child\nchild';
  assert.equal(pack(spec({ sources: [{ path: 'source.txt', heading: '### Child' }] }), { repoRoot }).manifest.sources[0].sha256, hash(parentBounded));
});

test('heading at end includes remaining content and missing headings fail', t => {
  const repoRoot = fixture(t);
  writeFileSync(join(repoRoot, 'source.txt'), '# Start\n## Last\ntext\n');
  assert.equal(pack(spec({ sources: [{ path: 'source.txt', heading: '## Last' }] }), { repoRoot }).manifest.sources[0].sha256, hash('## Last\ntext'));
  assert.throws(() => pack(spec({ sources: [{ path: 'source.txt', heading: '## Missing' }] }), { repoRoot }), /PACK_HEADING_NOT_FOUND/);
});

test('material fences are longer than every embedded backtick run', t => {
  const repoRoot = fixture(t), content = '```js\nsynthetic\n```\ninline `````` run\n';
  writeFileSync(join(repoRoot, 'source.txt'), content);
  const { text } = pack(spec(), { repoRoot });
  assert.ok(text.includes('```````\n' + content + '\n```````'));
});

test('spec and budget errors are stable', t => {
  const repoRoot = fixture(t);
  for (const data of [null, [], {}, spec({ kind: 'other' }), spec({ goal: '' }), spec({ question: 1 }), spec({ constraints: 'x' }),
    spec({ sources: [null] }), spec({ sources: [{ path: 'source.txt', heading: '## X', lines: '1-2' }] }),
    spec({ sources: [{ path: 'source.txt', heading: 'not a heading' }] }), spec({ sources: [{ path: 'source.txt', lines: 1 }] }),
    spec({ diff: {} }), spec({ diff: { base: 'HEAD', paths: [] } })]) {
    assert.throws(() => pack(data, { repoRoot }), /PACK_SPEC_INVALID/);
  }
  for (const max_bytes of [0, -1, 400001, 1.5, '150000', null]) {
    assert.throws(() => pack(spec({ max_bytes }), { repoRoot }), /PACK_BUDGET_INVALID/);
  }
});

test('budget counts final UTF-8 bytes exactly and lists source sizes', t => {
  const repoRoot = fixture(t), baseline = pack(spec(), { repoRoot });
  assert.equal(pack(spec({ max_bytes: baseline.manifest.bytes }), { repoRoot }).text, baseline.text);
  assert.throws(() => pack(spec({ max_bytes: baseline.manifest.bytes - 1 }), { repoRoot }), error => {
    assert.match(error.message, /^PACK_BUDGET_EXCEEDED:/);
    const detail = JSON.parse(error.message.slice(error.message.indexOf(':') + 1));
    assert.equal(detail.bytes, baseline.manifest.bytes);
    assert.deepEqual(detail.sources, [{ path: 'source.txt', bytes: baseline.manifest.sources[0].bytes }]);
    return true;
  });
});

test('CLI budget failure produces no task file and no stdout', t => {
  const repoRoot = fixture(t);
  writeFileSync(join(repoRoot, 'pack.json'), JSON.stringify(spec({ max_bytes: 1 })));
  const result = run(repoRoot, ['--spec', 'pack.json', '--out', 'task.txt']);
  assert.equal(result.status, 2);
  assert.equal(result.stdout, '');
  assert.match(result.stderr, /^PACK_BUDGET_EXCEEDED: [^\r\n]+\r?\n$/);
  assert.equal(existsSync(join(repoRoot, 'task.txt')), false);
});

test('CLI stdout contains only the manifest and defaults root to cwd', t => {
  const repoRoot = fixture(t);
  writeFileSync(join(repoRoot, 'pack.json'), JSON.stringify(spec()));
  const result = run(repoRoot, ['--spec', 'pack.json', '--out', 'task.txt']);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout.trim().split('\n').length, 1);
  const manifest = JSON.parse(result.stdout), text = readFileSync(join(repoRoot, 'task.txt'), 'utf8');
  assert.deepEqual(Object.keys(manifest), ['out', 'bytes', 'sha256', 'sources']);
  assert.equal(manifest.out, join(repoRoot, 'task.txt'));
  assert.equal(manifest.sha256, hash(text));
  assert.equal(manifest.bytes, Buffer.byteLength(text));
  assert.ok(!result.stdout.includes('synthetic material'));
});

test('CLI refuses existing output and .git output', t => {
  const repoRoot = fixture(t);
  writeFileSync(join(repoRoot, 'pack.json'), JSON.stringify(spec()));
  writeFileSync(join(repoRoot, 'task.txt'), 'preserve');
  mkdirSync(join(repoRoot, '.git'));
  for (const [out, code] of [['task.txt', 'PACK_OUTPUT_EXISTS'], ['.git/task.txt', 'PACK_PATH_REFUSED'],
    ['.git::$INDEX_ALLOCATION/task.txt', 'PACK_PATH_REFUSED'], ['task.txt:stream', 'PACK_PATH_REFUSED']]) {
    const result = run(repoRoot, ['--spec', 'pack.json', '--out', out]);
    assert.equal(result.status, 2);
    assert.equal(result.stdout, '');
    assert.ok(result.stderr.startsWith(code + ':'));
  }
  assert.equal(readFileSync(join(repoRoot, 'task.txt'), 'utf8'), 'preserve');
  assert.equal(existsSync(join(repoRoot, '.git/task.txt')), false);
});

test('CLI refuses output through a directory link', t => {
  const repoRoot = fixture(t), target = join(repoRoot, 'target');
  mkdirSync(target);
  writeFileSync(join(repoRoot, 'pack.json'), JSON.stringify(spec()));
  try { symlinkSync(target, join(repoRoot, 'alias'), process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { t.skip(`directory link unavailable: ${error.code}`); return; }
  const result = run(repoRoot, ['--spec', 'pack.json', '--out', 'alias/task.txt']);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /^PACK_LINK_REFUSED:/);
  assert.equal(existsSync(join(target, 'task.txt')), false);
});

test('CLI .git refusal compares canonical root and output parent spellings', t => {
  const repoRoot = fixture(t), canonicalRoot = realpathSync.native(repoRoot);
  mkdirSync(join(repoRoot, '.git'));
  writeFileSync(join(repoRoot, 'pack.json'), JSON.stringify(spec()));
  const result = run(repoRoot, ['--spec', 'pack.json', '--root', canonicalRoot, '--out', join(repoRoot, '.git', 'task.txt')]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /^PACK_PATH_REFUSED:/);
  assert.equal(existsSync(join(repoRoot, '.git', 'task.txt')), false);
});

test('CLI explicit root and argument errors', t => {
  const repoRoot = fixture(t), child = join(repoRoot, 'child');
  mkdirSync(child);
  writeFileSync(join(child, 'pack.json'), JSON.stringify(spec()));
  const result = run(child, ['--spec', 'pack.json', '--out', 'task.txt', '--root', repoRoot]);
  assert.equal(result.status, 0, result.stderr);
  for (const args of [[], ['--spec'], ['--unknown', 'x'], ['--spec', 'pack.json', '--spec', 'pack.json'], ['--out', 'task.txt']]) {
    const result = run(child, args);
    assert.equal(result.status, 2);
    assert.equal(result.stdout, '');
    assert.match(result.stderr, /^PACK_ARGUMENTS_INVALID:/);
  }
  writeFileSync(join(child, 'broken.json'), '{');
  const invalid = run(child, ['--spec', 'broken.json', '--out', 'other.txt']);
  assert.equal(invalid.status, 2);
  assert.match(invalid.stderr, /^PACK_SPEC_INVALID:/);
});

test('diff includes staged and unstaged changes with a verified commit hash', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'source.txt'), 'updated synthetic\n');
  const result = pack(diffSpec('HEAD'), { repoRoot });
  assert.ok(result.text.includes('-synthetic material'));
  assert.ok(result.text.includes('+updated synthetic'));
  assert.equal(result.manifest.sources[0].selector, `base:${base}`);
  git(repoRoot, ['add', 'source.txt']);
  assert.ok(pack(diffSpec(base), { repoRoot }).text.includes('+updated synthetic'));
});

test('diff base validation rejects options, controls and invalid revisions', t => {
  const repoRoot = repository(t); snapshot(repoRoot);
  for (const base of ['--help', '-HEAD', 'HEAD\n', 'HEAD\x7f']) {
    assert.throws(() => pack(diffSpec(base), { repoRoot }), /PACK_SPEC_INVALID/);
  }
  assert.throws(() => pack(diffSpec('no-such-revision'), { repoRoot }), /^Error: PACK_GIT_FAILED: git operation failed$/);
});

test('diff refuses HEAD with no changes', t => {
  const repoRoot = repository(t); snapshot(repoRoot);
  assert.throws(() => pack(diffSpec('HEAD'), { repoRoot }),
    /PACK_DIFF_EMPTY: base and worktree have no differences in diff.paths; base must be a commit before the changes/);
});

test('diff refuses HEAD after changes have already been committed', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'source.txt'), 'committed synthetic change\n');
  snapshot(repoRoot);
  assert.throws(() => pack(diffSpec('HEAD'), { repoRoot }), /PACK_DIFF_EMPTY/);
  assert.ok(pack(diffSpec(base), { repoRoot }).text.includes('+committed synthetic change'));
});

test('diff refuses untracked files before checking for an empty diff', t => {
  const repoRoot = repository(t); snapshot(repoRoot);
  mkdirSync(join(repoRoot, 'folder'));
  writeFileSync(join(repoRoot, 'folder', 'new file.txt'), 'synthetic addition\n');
  for (const paths of [['folder'], ['folder/new file.txt']]) {
    assert.throws(() => pack(diffSpec('HEAD', paths), { repoRoot }),
      /^Error: PACK_DIFF_UNTRACKED: untracked files in diff.paths: folder\/new file.txt; run git add first or use sources$/);
  }
});

test('diff ignores untracked files excluded by gitignore', t => {
  const repoRoot = repository(t);
  mkdirSync(join(repoRoot, 'folder'));
  writeFileSync(join(repoRoot, '.gitignore'), 'folder/ignored.txt\n');
  writeFileSync(join(repoRoot, 'folder', 'tracked.txt'), 'before\n');
  const base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'folder', 'tracked.txt'), 'after\n');
  writeFileSync(join(repoRoot, 'folder', 'ignored.txt'), 'ignored synthetic\n');
  const { text } = pack(diffSpec(base, ['folder']), { repoRoot });
  assert.ok(text.includes('+after'));
  assert.ok(!text.includes('ignored synthetic'));
});

test('diff includes a new file after git add', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'new.txt'), 'staged synthetic addition\n');
  git(repoRoot, ['add', 'new.txt']);
  assert.ok(pack(diffSpec(base, ['new.txt']), { repoRoot }).text.includes('+staged synthetic addition'));
});

test('diff ignores untracked files outside the selected paths', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'outside.txt'), 'outside synthetic\n');
  writeFileSync(join(repoRoot, 'source.txt'), 'selected synthetic\n');
  assert.ok(pack(diffSpec(base), { repoRoot }).text.includes('+selected synthetic'));
});

test('diff limits untracked diagnostics to 20 paths and reports the total', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  mkdirSync(join(repoRoot, 'folder'));
  for (let i = 0; i < 21; i++) {
    writeFileSync(join(repoRoot, 'folder', `file${String(i).padStart(2, '0')}.txt`), 'synthetic\n');
  }
  assert.throws(() => pack(diffSpec(base, ['folder']), { repoRoot }), error => {
    assert.match(error.message, /^PACK_DIFF_UNTRACKED:/);
    assert.equal((error.message.match(/folder\/file\d{2}\.txt/g) ?? []).length, 20);
    assert.ok(error.message.includes('folder/file19.txt'));
    assert.ok(!error.message.includes('folder/file20.txt'));
    assert.ok(error.message.includes('(21 total)'));
    return true;
  });
  writeFileSync(join(repoRoot, 'folder', 'zz-secret.txt'), 'synthetic\n');
  assert.throws(() => pack(diffSpec(base, ['folder']), { repoRoot }), /PACK_PATH_REFUSED/);
});

test('diff checks untracked paths for links during directory expansion', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  mkdirSync(join(repoRoot, 'folder'));
  try { symlinkSync(join(repoRoot, 'source.txt'), join(repoRoot, 'folder', 'alias.txt'), 'file'); }
  catch (error) { t.skip(`file symlink unavailable: ${error.code}`); return; }
  assert.throws(() => pack(diffSpec(base, ['folder']), { repoRoot }), /PACK_LINK_REFUSED/);
});

test('diff rejects escaping and sensitive paths, including directory expansion', t => {
  const repoRoot = repository(t);
  mkdirSync(join(repoRoot, 'folder')); writeFileSync(join(repoRoot, 'folder', '.env'), 'synthetic');
  const base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'folder', '.env'), 'changed');
  for (const path of ['../outside', 'X:\\outside', '\\\\synthetic-server\\share', '.git/config', 'folder']) {
    assert.throws(() => pack(diffSpec(base, [path]), { repoRoot }), /PACK_PATH_REFUSED/);
  }
});

test('diff checks old blobs for binary even when deleted or replaced with text', t => {
  const repoRoot = repository(t);
  writeFileSync(join(repoRoot, 'source.txt'), Buffer.from([0xff, 0]));
  const base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'source.txt'), 'safe new text');
  assert.throws(() => pack(diffSpec(base), { repoRoot }), /PACK_BINARY_REFUSED/);
  unlinkSync(join(repoRoot, 'source.txt'));
  assert.throws(() => pack(diffSpec(base), { repoRoot }), /PACK_BINARY_REFUSED/);
});

test('diff checks new worktree text strictly', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'source.txt'), Buffer.from([0xc3, 0x28]));
  assert.throws(() => pack(diffSpec(base), { repoRoot }), /PACK_BINARY_REFUSED/);
});

test('diff rejects old symlink blobs without requiring filesystem symlink support', t => {
  const repoRoot = repository(t);
  git(repoRoot, ['add', 'source.txt']);
  const blob = git(repoRoot, ['hash-object', '-w', '--stdin'], 'synthetic-target');
  git(repoRoot, ['update-index', '--add', '--cacheinfo', `120000,${blob},alias.txt`]);
  const base = snapshotIndex(repoRoot);
  git(repoRoot, ['update-index', '--force-remove', 'alias.txt']);
  assert.throws(() => pack(diffSpec(base, ['alias.txt']), { repoRoot }), /PACK_LINK_REFUSED/);
});

test('diff rejects staged symlinks hidden by regular worktree files', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  const blob = git(repoRoot, ['hash-object', '-w', '--stdin'], 'synthetic-target');
  git(repoRoot, ['update-index', '--add', '--cacheinfo', `120000,${blob},source.txt`]);
  writeFileSync(join(repoRoot, 'source.txt'), 'regular synthetic text');
  assert.throws(() => pack(diffSpec(base), { repoRoot }), /PACK_LINK_REFUSED/);
});

test('diff rejects submodule modes', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  git(repoRoot, ['update-index', '--add', '--cacheinfo', `160000,${base},nested`]);
  mkdirSync(join(repoRoot, 'nested'));
  assert.throws(() => pack(diffSpec(base, ['nested']), { repoRoot }), /PACK_LINK_REFUSED/);
});

test('diff strips inherited GIT variables and disables external diff and textconv', t => {
  const repoRoot = repository(t);
  writeFileSync(join(repoRoot, '.gitattributes'), 'source.txt diff=synthetic\n');
  const base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'source.txt'), 'updated synthetic\n');
  git(repoRoot, ['config', 'diff.external', 'nonexistent-synthetic-command']);
  git(repoRoot, ['config', 'diff.synthetic.command', 'nonexistent-synthetic-command']);
  git(repoRoot, ['config', 'diff.synthetic.textconv', 'nonexistent-synthetic-command']);
  git(repoRoot, ['config', 'core.fsmonitor', 'nonexistent-synthetic-command']);
  writeFileSync(join(repoRoot, 'pack.json'), JSON.stringify(diffSpec(base)));
  const result = run(repoRoot, ['--spec', 'pack.json', '--out', 'task.txt'], { ...process.env,
    GIT_DIR: join(repoRoot, 'missing'), GIT_WORK_TREE: join(repoRoot, 'missing'),
    GIT_CONFIG_COUNT: '1', GIT_CONFIG_KEY_0: 'diff.external', GIT_CONFIG_VALUE_0: 'nonexistent-synthetic-command' });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(readFileSync(join(repoRoot, 'task.txt'), 'utf8').includes('+updated synthetic'));
});

test('literal pathspecs do not expand wildcard-looking input', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  writeFileSync(join(repoRoot, 'source.txt'), 'updated');
  writeFileSync(join(repoRoot, '[s]ource.txt'), 'literal synthetic\n');
  assert.throws(() => pack(diffSpec(base, ['[s]ource.txt']), { repoRoot }), /PACK_DIFF_UNTRACKED/);
  git(repoRoot, ['--literal-pathspecs', 'add', '--', '[s]ource.txt']);
  const result = pack(diffSpec(base, ['[s]ource.txt']), { repoRoot });
  assert.ok(result.text.includes('+literal synthetic'));
  assert.ok(!result.text.includes('+updated'));
  assert.throws(() => pack(diffSpec(base, [':(glob)*']), { repoRoot }), /PACK_PATH_REFUSED/);
});

test('no-change diffs and nested worktree roots are refused', t => {
  const repoRoot = repository(t), base = snapshot(repoRoot);
  assert.throws(() => pack(diffSpec(base), { repoRoot }), /PACK_DIFF_EMPTY/);
  mkdirSync(join(repoRoot, 'child'));
  assert.throws(() => pack(diffSpec(base), { repoRoot: join(repoRoot, 'child') }), /PACK_GIT_FAILED/);
});

/** Local file/process transport. No browser, network, credentials or shell. */
import { spawnSync } from 'node:child_process';
import { writeFileSync, readFileSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';

export function localTransport({ root, ledger, evidenceDirectory, receiptPath, python = 'py', pythonArgs = ['-3.12'] }) {
  if (![root, ledger, evidenceDirectory, receiptPath].every(x => typeof x === 'string' && x.length)) throw Error('EXPLICIT_PATHS_REQUIRED');
  const run = async (command, args = [], files = {}) => {
    const extra = [];
    for (const [kind, value] of Object.entries(files)) {
      if (!['observation', 'result'].includes(kind)) throw Error('INVALID_FILE_KIND');
      const path = join(resolve(evidenceDirectory), `${kind}.json`);
      writeFileSync(path, JSON.stringify(value), 'utf8');
      extra.push(kind === 'observation' ? '--browser-observation' : '--result', path);
    }
    const process = spawnSync(python, [...pythonArgs, '-B', join(resolve(root), 'adapters/generic/run_bridge.py'),
      command, '--ledger', resolve(ledger), ...args, ...extra], { encoding: 'utf8', timeout: 30000,
      maxBuffer: 8 * 1024 * 1024, windowsHide: true, shell: false });
    if (process.error) throw process.error;
    if (command === 'prompt' && process.status === 0) return { code: 0, value: process.stdout };
    let value;
    try { value = JSON.parse(process.stdout); } catch { throw Error(`BRIDGE_OUTPUT_INVALID:${process.status}`); }
    return { code: process.status, value };
  };
  run.receiptPath = resolve(receiptPath);
  return run;
}

export function loadPrepared(receiptPath) {
  const prepared = JSON.parse(readFileSync(receiptPath, 'utf8').replace(/^\uFEFF/, ''));
  if (!prepared.receipt || prepared.error || !prepared.prompt || !prepared.browser_target) throw Error('ORIGINAL_PREPARE_REQUIRED');
  return prepared;
}

/** Read only the original task's collected reply, with a bounded context excerpt.
 * Full text stays in the ledger. Reading never resumes a browser or sends a task.
 */
export async function readCollected(run, { maxCharacters = 4000 } = {}) {
  if (!run.receiptPath || !Number.isSafeInteger(maxCharacters) || maxCharacters < 1 || maxCharacters > 65536) {
    throw Error('INVALID_RESULT_READ');
  }
  const response = await run('status', ['--receipt', run.receiptPath]);
  const task = response.value;
  const result = task?.browser_result;
  if (response.code !== 0 || task.state !== 'RESULT_SEEN' || task.send_attempts !== 1 ||
      result?.status !== 'PAGE_COMPLETE' || result.task_id !== task.task_id || result.nonce !== task.nonce ||
      result.request_sha256 !== task.request_sha256 || result.url !== task.session_url ||
      typeof result.text !== 'string' || !result.text.trim()) throw Error('COLLECTED_RESULT_NOT_AVAILABLE');
  const sha256 = createHash('sha256').update(result.text, 'utf8').digest('hex');
  if (sha256 !== result.sha256) throw Error('COLLECTED_RESULT_HASH_MISMATCH');
  return { task_id: task.task_id, text: result.text.slice(0, maxCharacters),
    characters: result.text.length, truncated: result.text.length > maxCharacters, sha256,
    evidence_origin: 'host_browser', review_approved: false, cost_claim: 'NOT_OBSERVABLE' };
}

/** Create one new original receipt. Existing receipts always require recovery. */
export async function prepareOnline({ run, observation, evidenceDirectory, repository, sourceSha,
  sourceRef, taskFile, orchestration, deadlineSeconds = 3600 }) {
  if (!run.receiptPath || existsSync(run.receiptPath)) throw Error('RECEIPT_EXISTS_USE_RECOVERY');
  const target = Object.fromEntries(['mode', 'project_id', 'conversation_id', 'browser_context',
    'tab_id', 'model_label', 'effort_label'].map(k => [k, observation[k]]));
  const targetPath = join(resolve(evidenceDirectory), 'target.json');
  const selectionPath = join(resolve(evidenceDirectory), 'selection.json');
  writeFileSync(targetPath, JSON.stringify(target), { encoding: 'utf8', flag: 'wx' });
  writeFileSync(selectionPath, JSON.stringify(observation.selection_evidence), { encoding: 'utf8', flag: 'wx' });
  const result = await run('prepare', ['--task-type', 'READ_ONLY_REVIEW', '--repository', repository,
    '--source-sha', sourceSha, '--source-ref', sourceRef, '--orchestration', orchestration,
    '--delivery', 'browser_response', '--task-file', resolve(taskFile), '--browser-target', targetPath,
    '--selection-evidence', selectionPath, '--deadline-seconds', String(deadlineSeconds)]);
  if (result.code !== 0 || !result.value.receipt) throw Error('PREPARE_FAILED_NO_SEND');
  writeFileSync(run.receiptPath, JSON.stringify(result.value), { encoding: 'utf8', flag: 'wx' });
  return result.value;
}

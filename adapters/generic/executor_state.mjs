/** Page-executor lifecycle for a non-CUA host.
 *
 * starting and running reject a second executor in the same document.
 * failed and stopped may be replaced by a new executor. Replacement uses a new
 * token and a new session. Reloading the document after a send claim changes
 * the bound session and must be refused; re-inject without reload instead.
 * This module does not inject a page script and does not send.
 */
export const EXECUTOR_STATES = Object.freeze(['starting', 'running', 'failed', 'stopped']);

export const EXECUTOR_REINJECTION = Object.freeze({
  activeStates: Object.freeze(['starting', 'running']),
  replaceableStates: Object.freeze(['failed', 'stopped']),
  replacementRequiresNewToken: true,
  replacementRequiresNewSession: true,
  reloadAfterClaimRefused: true,
});

const NEXT = Object.freeze({
  starting: Object.freeze({ run: 'running', fail: 'failed', stop: 'stopped' }),
  running: Object.freeze({ fail: 'failed', stop: 'stopped' }),
  failed: Object.freeze({}),
  stopped: Object.freeze({}),
});

export function admitExecutor(current) {
  if (current == null) return { admit: true, next: 'starting', replaced: false };
  if (!EXECUTOR_STATES.includes(current)) return { admit: false, reason: 'EXECUTOR_STATE_INVALID' };
  if (EXECUTOR_REINJECTION.replaceableStates.includes(current)) {
    return { admit: true, next: 'starting', replaced: true };
  }
  return { admit: false, reason: 'EXECUTOR_ALREADY_ACTIVE' };
}

export function transitionExecutor(current, event) {
  const next = NEXT[current]?.[event];
  if (!next) throw Error('EXECUTOR_TRANSITION_REFUSED');
  return next;
}

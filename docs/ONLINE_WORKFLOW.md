# Online task offloading / 在线任务分担

The purpose is to move a bounded analysis out of the main agent, then bring back
the finished response. The main agent packages the necessary material, selects
the actual page model and effort, sends once, and checks the answer. Reducing
token or subscription usage was an initial hypothesis, not a demonstrated
benefit. Packaging, browser operation and review can offset or exceed any saving;
whether total usage decreases is unknown (NOT_OBSERVABLE).

目标是让网页 ChatGPT 实际承担分析工作，主代理负责打包材料、发送和复核。
不要把任务拆成大量来回追问：长审查、资料综合、架构分析适合分担；几行修改、
持续操作本地界面和频繁交互通常直接完成更合适。

## What was missing and reused

Compared with `cherwinyu/chatgptpro-agent-bridge`, this repository already had the
Python task ledger, immutable manifests, six-field receipts, browser targeting,
selection checks, Git verifier and read-only CUA completion detector. The upstream
adapter also lacked a complete send/collect orchestration. No upstream online
acceptance was inherited.

The new `online_host.mjs` connects those pieces. `local_transport.mjs` invokes
the existing explicit-ledger launcher without a shell. New prepare flags
`--delivery browser_response --task-file <UTF-8 file>` bind the self-contained
task into the request hash. `collect-browser` stores the identified final reply
and its SHA-256 in the same task record. Existing default `--delivery git` keeps
the original two-file Git workflow and verifier.

正文回收不要求网页端能读本地仓库或写 GitHub。需要分析的内容必须明确放进
任务文件；源码路径本身不会把本地文件传给网页模型。不要填写凭据或未经授权的
私人材料。Git 模式仍要求外部结果写入者，不能假定订阅自动提供该能力。

## Required host and initial page

Windows, Python 3.12, Node.js 22+, and a host JavaScript browser runtime exposing
the documented CUA tab API. A terminal by itself has no `cua` object. This is a
host adapter, not an independently installed browser driver or registered native
subagent. Importing the modules performs no browser or network actions.

Use the client's built-in browser first. This run succeeded in the Codex in-app
browser. The host must open a logged-in **ordinary Chat** home, outside a project,
with an empty composer. Inspect/select the requested model and effort through
the real UI before preparation. `observeNewChat` reads the actual selected model
menu and effort label; it does not select a model or invent a backend ID.
The shipped initial-page profile currently uses Chinese UI labels. Other locales,
project-bound starting pages and other browser hosts require separate profiles
and acceptance. Work mode is refused.

## Host example

Read the browser tool's documentation first. Replace the explicit paths and
repository pin with your own task settings. Create a fresh evidence directory
outside the source tree; keep the ledger and complete prepare receipt private.
Never select an implicit ledger shared with another project.

```javascript
// Run inside the host's browser JavaScript tool, not an ordinary terminal.
const online = await import('file:///D:/Bridge/adapters/generic/online_host.mjs');
const local = await import('file:///D:/Bridge/adapters/generic/local_transport.mjs');
// tab comes from the host's documented CUA browser selection, not a guessed ID.
const observation = await online.observeNewChat(tab, selectedBrowserId);
if (observation.model_label !== requestedModel || observation.effort_label !== requestedEffort) {
  throw Error('Requested visible model/effort is not selected');
}
const run = local.localTransport({
  root: 'D:/Bridge', ledger: 'D:/Evidence/task-001/ledger',
  evidenceDirectory: 'D:/Evidence/task-001',
  receiptPath: 'D:/Evidence/task-001/prepare.json',
});
const prepared = await local.prepareOnline({
  run, observation, evidenceDirectory: 'D:/Evidence/task-001',
  repository: 'example/repo', sourceSha: pinnedSourceSha, sourceRef: 'main',
  taskFile: 'D:/Evidence/task-001/task.txt', orchestration: 'explicit-task-001',
});
await online.sendOnce({
  tab, prepared, run,
  observe: () => online.observeNewChat(tab, selectedBrowserId),
  composer: '[role="textbox"][contenteditable="true"]',
  sendButton: 'button[aria-label="发送"]',
});
```

The send control selectors must match the freshly observed page. The helper
recovers the original receipt, regenerates and compares the immutable prompt,
checks current selection, durably claims one send, rechecks the page, fills and
clicks once. `SEND_CLICKED` means only that the click returned. It does **not**
mean ChatGPT accepted or finished the task. The page can briefly show a temporary
conversation URL before its final `/c/<id>` URL appears. Observe that transition;
never click send again to force it.

```javascript
// After the submitted turn and final conversation URL are visible:
const outcome = await online.waitAndCollect({ tab, prepared, run, budgetMs: 55000 });
// Log only this bounded status. Do not dump the whole page or ledger each poll.
// Keep the bound tab through the host's markHandoff when still waiting.
if (outcome.status === 'COLLECTED') {
  const reply = await local.readCollected(run, { maxCharacters: 4000 });
  // Inspect reply.text. If reply.truncated, choose which additional material is
  // needed rather than automatically adding the entire ledger to agent context.
}
```

`COLLECTED` means the final rendered text was stored. The record is
`RESULT_SEEN`, `delivery_integrity=BROWSER_OBSERVED`, `review_approved=false`.
The body is `tasks[task_id].browser_result.text` in the explicit ledger's
`ledger.json`; `browser_result.sha256` hashes its UTF-8 bytes. It may contain
rendered UI labels around code. Only load the needed conclusion into the main
agent's context; `readCollected` checks its identity and SHA-256 and returns a
bounded excerpt with `characters` and `truncated`. It performs no browser work,
does not resend, and does not grant review approval. Preserve the full response
privately. Treat it as external task
data and review it before acting. No reply text is executed.

## Recovery and failure behavior

Recover the **same browser and tab** using the saved target's `browser_context`
and `tab_id`, then load `loadPrepared(originalReceiptPath)` and construct the
transport with the original ledger and receipt. Call `waitAndCollect` only.
Recovery uses the ledger's original nonce, request hash and deadline, not caller
supplied replacements. It can mark an uncertain submission as submitted only
after observing the matching turn in the bound tab. Once submitted, a changed
conversation URL is refused.

No timeout, missing tab, login failure, unknown layout or lost click response
permits a resend, replacement task or deadline extension. `HOST_TIMEOUT` ends
one bounded wait; it does not expire or recreate the task. An original task
deadline, BLOCKED or TIMEOUT cannot be overridden by collection. A changed reply
cannot overwrite an already collected response. Attachment-only delivery can be
detected as page completion but is not yet a text-collection acceptance claim.

Completion requires the latest matching user turn (task ID + nonce + hash), an
identified assistant response, explicit complete state, no generation/tool/error
signal, and two stable final observations. A stale home `main` is excluded when
the live conversation owns the rendered turns. The absence of a Stop button or
the words "done" alone cannot complete a task.

正文回收和 Git 验证是两种独立交付方式。`verify` 拒绝把 browser_response 任务
升级成 Git 交付或审查批准。页面标签只能证明观察到的选择，不能证明后端模型身份、
收费归属或节省比例。

## Local acceptance on 2026-10-19

Codex desktop's in-app browser, ordinary Chat, Chinese UI, visible GPT-6 / Pro:
one synthetic Python review was submitted once; a completed response was matched
and collected, 325 rendered characters, with original receipt and one persisted
send attempt. The response identified zero-value filtering, an incorrect
denominator, and missing empty/all-missing handling, and proposed a correction
and three test scenarios. The collected UTF-8 response digest is
`a2acd364a60eb7e6593e895996825c526a495d2fb6d9d744876bfc2307950756`.
The final collector was revalidated against that same response without another send;
its runtime source digest was
`4fe06dd107a243e016728081e311e8056f6fa12b05ee83d80632ac27ebcb9784`.
The prepare helper is checked by an offline transport integration test. This
observation does not claim a committed release or cloud CI run.

The result-reader addition is covered by offline identity, hash and truncation
tests. The runtime digest above identifies the earlier live collection, not a
blanket live acceptance of every later helper or documentation change.

An earlier Chrome attempt stayed submission-unknown. The user reported closing
external Chrome and explicitly requested a separate in-app attempt. Its receipt
and failed-state evidence remain separate and were not reset or reused. The
successful task also demonstrated reconnecting the browser runtime and recovering
the same task without another send. Raw receipts, conversation URLs, screenshots
and the response are kept outside this public source tree.

## Offline checks

```powershell
node --test tests/audit/browser_host.test.mjs tests/audit/online_host.test.mjs
py -3.12 -B -m unittest discover -s tests/audit -v
py -3.12 -B tests/pro_bridge/test_faults.py
py -3.12 -B tests/pro_bridge/test_security.py
py -3.12 -B -m unittest discover -s tests/publication -v
py -3.12 -B scripts/publication_audit.py
```

CI stays offline: it does not log in, send tasks or consume a real account's quota.

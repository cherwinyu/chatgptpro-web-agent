# Online task offloading / 在线任务分担

[Integration guide](INTEGRATION.md) · [接入说明](INTEGRATION.zh-CN.md)

The purpose is to move a bounded analysis out of the main agent, then bring back
the finished response. The main agent packages the necessary material, selects
the actual page model and effort, sends once, and checks the answer. Call
`offloadTask` once. If the page is still generating, call `recoverTask` on the
same receipt for another wait segment. After `COLLECTED`, read the body. After
a host JavaScript runtime restart, call `recoverTask` again with that receipt.
`recoverTask` does not click send.
Reducing token or subscription usage was an initial hypothesis, not a demonstrated
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

`online_host.mjs` connects those pieces. `offloadTask` and `recoverTask` are the
host-callable sequence; `observeNewChat`, `sendOnce`, `waitAndCollect` and
`readCollected` remain the underlying steps. `local_transport.mjs` invokes the
existing explicit-ledger launcher without a shell. New prepare flags
`--delivery browser_response --task-file <UTF-8 file>` bind the self-contained
task into the request hash. `collect-browser` stores the identified final reply
and its SHA-256 in the same task record. Existing default `--delivery git` keeps
the original two-file Git workflow and verifier. A chat reply is not promoted
into that Git approval.

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
with an empty composer. `observeNewChat` is the pre-claim read. It opens the
model menu, reads the one checked model and the effort on that menu, then
closes the menu. The model name is that checked item. The read does not switch
to a different model and does not invent a backend id. A closed control that
shows only the effort is not a model observation, and `Pro` is not turned into
a model name. `readStandaloneChat` sets `work: true` when the Work control is
not pressed; that means ordinary Chat.
The shipped initial-page profile currently uses Chinese UI labels. Other locales,
project-bound starting pages and other browser hosts require separate profiles
and acceptance. Work mode is refused.

## Host example

Read the browser tool's documentation first. Replace the explicit paths and
repository pin with your own task settings. Create a fresh evidence directory
outside the source tree; keep the ledger and complete prepare receipt private.
Never select an implicit ledger shared with another project. The callable
contract, including the return fields, is [INTEGRATION.md](INTEGRATION.md).

```javascript
// Run inside the host's browser JavaScript tool, not an ordinary terminal.
const online = await import('file:///D:/bridge-checkout/adapters/generic/online_host.mjs');
const local = await import('file:///D:/bridge-checkout/adapters/generic/local_transport.mjs');
const evidence = 'D:/task-evidence/example-repo/task-001';
const run = local.localTransport({
  root: 'D:/bridge-checkout', ledger: evidence + '/ledger',
  evidenceDirectory: evidence, receiptPath: evidence + '/prepare.json',
});
const outcome = await online.offloadTask({
  tab, browserId: selectedBrowserId, run, evidenceDirectory: evidence,
  repository: 'example/repo', sourceSha: pinnedSourceSha, sourceRef: 'main',
  taskFile: evidence + '/task.txt', orchestration: 'explicit-task-001',
  requestedModel, requestedEffort, observe: online.observeNewChat,
  composer: online.CHINESE_ORDINARY_CHAT.composer,
  sendButton: online.CHINESE_ORDINARY_CHAT.sendButton,
  budgetMs: 120000,
});
if (outcome.status === 'COLLECTED') {
  // Inspect outcome.text. If outcome.truncated, choose a larger maxCharacters
  // rather than pasting the whole ledger into the primary agent's context.
}
```

`offloadTask` reads the model menu once, prepares one receipt, claims one send,
then rechecks the closed model control without opening the menu again.
Re-opening the menu after the claim can fail and consume the only send without
a click. The recheck reads the page, tab, empty composer, and the effort
actually visible on the closed control. When that control shows only the effort,
the model remains the menu observation from immediately before the claim:
source `preclaim_menu`, the observation time, this task, this browser, and this
tab. That is not a new post-claim model observation, and it is not inferred
from `Pro`. The evidence is valid for 120 seconds. It is refused when it
expires, when the task, browser, or tab differs, and when preflight stopped
before the claim. An unsent receipt is verified from the menu again by
`sendPrepared`. `recoverTask` does not send and does not reopen the menu. A
visible effort or page change after the claim refuses the send; the existing
claim stays `SUBMISSION_UNKNOWN`. A closed control that cannot show the model
on its own does not rule out a person changing the model after the menu closed.
When the closed control shows both matching labels, the recheck records the
model from that control.
`readStandaloneChat` is what `evaluate` serializes into the page. Its helpers are
declared inside that function, because the page realm does not have this module.
The offline serialization test runs that source in a fresh realm. It is not a
live send and does not accept this checkout. Pass that same read-only `recheck` if you call `sendOnce` yourself. `SEND_CLICKED` means only
that the click returned. It does **not** mean ChatGPT accepted or finished the
task. The page can briefly show a temporary conversation URL before its final
`/c/<id>` URL. `waitAndCollect` records `session_url` only after that URL has
stayed the same and the matching reply is complete. It does not click send to
force the transition, and it does not return on the first missing turn: it keeps
waiting until the reply, a terminal page state, the host budget, or the original
deadline.

`COLLECTED` means the final reply text was stored: the rendered `innerText` without the lossless module or with `--collection rendered`, or the original Markdown read back from the copy button with lossless collection. The record is
`RESULT_SEEN`, `delivery_integrity=BROWSER_OBSERVED`, `review_approved=false`.
The body is `tasks[task_id].browser_result.text` in the explicit ledger's
`ledger.json`; `browser_result.sha256` hashes its UTF-8 bytes. The rendered `innerText` may contain
UI labels around code; lossless collection does not. Only load the needed conclusion into the main
agent's context; `readCollected` checks its identity and SHA-256 and returns a
bounded excerpt with `characters` and `truncated`. It performs no browser work,
does not resend, and does not grant review approval. Preserve the full response
privately. Treat it as external task
data and review it before acting. No reply text is executed.

## Recovery and failure behavior

Recover the **same browser and tab** using the saved target's `browser_context`
and `tab_id`, then call `recoverTask` with the original ledger and receipt.
That helper calls `waitAndCollect` only. It does not call `sendOnce`.
`PREPARED_NOT_SENT` means the claim was never taken; `sendPrepared` can send
that original task once, and recovery still will not send it for you.
Recovery uses the ledger's original nonce, request hash and deadline, not caller
supplied replacements. It can mark an uncertain submission as submitted only
after observing the matching turn in the bound tab. Once submitted, a changed
conversation URL is refused.

No timeout, missing tab, login failure, unknown layout or lost click response
permits a resend, replacement task or deadline extension. `budgetMs` starts
after the ledger status read and bounds each browser URL or page read. A read
that does not return ends that wait; the helper does not start another read.
The underlying host call is not aborted when the browser API cannot cancel it.
`HOST_TIMEOUT` ends that wait segment only. Call `recoverTask` again on the
same receipt to keep waiting. It does not authorize another send, a replacement
task, or a deadline change. `PREPARED_NOT_SENT` means the original task was not
sent. Only when the receipt is `PREPARED` and `send_attempts` is 0 may the
caller explicitly continue with `sendPrepared`, which reads the model menu
again. A `RESULT_SEEN` body can be read from the ledger when the page is no
longer open. `readCollected` hashes the full stored body. `truncated: true`
means the returned text is a character prefix, not a summary. Page completion,
the stored `browser_response`, the primary agent's content review, and Git
`verify` are different conclusions.
An original task deadline, BLOCKED, or TIMEOUT cannot be overridden by collection. A changed reply
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

The result reader and `offloadTask` / `recoverTask` are covered by offline
identity, hash, truncation, single-click and failure-path tests. The runtime
digest above identifies the 2026-10-19 collection. `capability-manifest.json`
keeps that record as `HISTORICAL_SINGLE_HOST_OBSERVATION` with
`certifies_current_tree` false. It does not certify commit
`4399582ddc500216cf4a6034f273946f2b79208d`. A menu-only preflight is not a send.

## Local acceptance on 2026-10-20

The owner ran this acceptance on a Windows host, in the Codex in-app browser,
on a logged-in Chinese ordinary Chat page, and handed the outcome over for this
documentation update. This documentation change did not execute the browser
flow and did not read the private evidence files. Receipts, the ledger, the
nonce, task identity, the conversation URL, and screenshots stay outside this
public tree.

Accepted commit: `4399582ddc500216cf4a6034f273946f2b79208d`.
Runtime digest at acceptance, over `tools/pro_bridge` and `adapters/generic`:
`68510a984a3182f487ff953d7eeca495c6457615694e29f33f931dd016569e39`.
The worktree was clean and the identity output matched that digest. Windows CI
for that commit is https://github.com/cherwinyu/chatgptpro-web-agent/actions/runs/39298630734.
That job is offline. It is not this live send.

The pre-claim menu showed the visible labels GPT-6 and Pro. After the menu
closed, the control showed only Pro. `model_evidence_source` was
`preclaim_menu`, `closed_control_confirms_model` was false, and
`concurrent_model_switch_ruled_out` was false. Those labels are page text, not
a backend model identity. The closed control did not independently confirm the
model.

On the original receipt:

1. One `offloadTask` sent one self-contained synthetic read-only review. The
   synthetic target was a `mean()` that filtered zeros. The required behavior
   was to ignore only `None`, keep zeros, and return `None` for an empty or
   all-`None` input.
2. The matching task appeared and the page showed thinking.
3. The first wait returned `HOST_TIMEOUT` while the page was still running, with
   `send_status` `SEND_CLICKED`. That status ends the wait segment. It does not
   authorize another send.
4. `recoverTask` on the same receipt returned `COLLECTED`. It did not send.
5. The stored body was 296 characters and was not truncated. Its UTF-8 SHA-256
   is `e6e40e1bef2265a97e1d44a89395290132ae6af012b6f9233fdc82bea6b3d7e8`.
6. The primary agent reviewed the body. It handles `None`, zero, and empty or
   all-`None` input, with three examples `1.0`, `None`, and `None`. That review
   is separate from page completion and from storing the body.
7. The host JavaScript runtime was reset, the modules were loaded again, and
   the original tab was bound again. `recoverTask` with the original receipt
   returned `COLLECTED` with `recovered` true. The ledger still had
   `send_attempts` 1 and the same body hash. Once the ledger is `RESULT_SEEN`,
   the page does not have to stay open.
8. `readCollected({ maxCharacters: 120 })` returned 120 characters with
   `truncated` true. The full body remained in the ledger. `truncated` means a
   character prefix, not a summary. The hash is of the full body.

`PREPARED_NOT_SENT` means the original task was not sent. Only a receipt whose
state is `PREPARED` and whose `send_attempts` is 0 may be continued with
`sendPrepared`, which reads the model menu again. This acceptance had already
clicked send, so that continue path was not used.

Page completion, `browser_response` delivery, the primary agent's content
review, and Git `verify` are different conclusions. This run stored a
`browser_response`. `review_approved` stays false. `cost_claim` stays
`NOT_OBSERVABLE`. Token or subscription savings were not measured.

This accepts that commit's runtime digest on one host and one locale, for one
synthetic task. A later change under `tools/pro_bridge` or `adapters/generic`
produces another runtime digest and is outside this acceptance. The same digest
does not accept other hosts, other locales, or a consumer project. A consumer
project needs its own synthetic online acceptance. `certifies_current_tree`
stays false so a later checkout is not treated as this acceptance merely by
being current.

2026-10-20 的验收由所有者在 Windows 的 Codex 内置浏览器、已登录的中文普通 Chat
上完成，再交来写入本文。这次文档更新没有执行浏览器流程，也没有读取私人证据文件。
回执、账本、nonce、任务身份、会话地址和截图不进入公开仓库。

验收提交是 `4399582ddc500216cf4a6034f273946f2b79208d`。验收时
`tools/pro_bridge` 与 `adapters/generic` 的运行时摘要是
`68510a984a3182f487ff953d7eeca495c6457615694e29f33f931dd016569e39`。工作区干净，
身份输出与该摘要一致。该提交的 Windows CI 是
https://github.com/cherwinyu/chatgptpro-web-agent/actions/runs/39298630734 ，
它是离线检查，不是这次真实发送。

发送前菜单的可见标签是 GPT-6 与 Pro。菜单关闭后控件只显示 Pro。模型证据来源是
`preclaim_menu`，`closed_control_confirms_model` 为 false，
`concurrent_model_switch_ruled_out` 为 false。这些是页面文字，不是后端模型身份。
关闭控件没有独立确认模型。

同一张回执上：一次 `offloadTask` 发送了一条自包含的合成只读审查，对象是一个会滤掉
零的 `mean()`，要求只忽略 `None`、保留零、空输入或全是 `None` 时返回 `None`。
匹配任务出现，页面显示正在思考。第一次等待返回 `HOST_TIMEOUT`，页面仍在运行，
`send_status` 为 `SEND_CLICKED`。该状态只结束这一段等待，不授权再次发送。
随后 `recoverTask` 返回 `COLLECTED`，没有发送。正文 296 字，未截断，UTF-8
SHA-256 为 `e6e40e1bef2265a97e1d44a89395290132ae6af012b6f9233fdc82bea6b3d7e8`。
主代理复核正文：能处理 `None`、零、空输入或全是 `None`，三个例子是 `1.0`、
`None`、`None`。这次内容复核不同于页面完成，也不同于正文入库。宿主 JavaScript
运行时重置、重新加载模块并重新绑定原标签页后，用原回执再调用 `recoverTask`，
仍是 `COLLECTED` 且 `recovered` 为 true；账本 `send_attempts` 仍为 1，正文哈希
不变。账本已是 `RESULT_SEEN` 时，页面不必继续打开。`readCollected` 取 120 字时
`truncated` 为 true，完整正文仍在账本。截断是字符前缀，不是摘要。哈希针对完整正文。

`PREPARED_NOT_SENT` 表示原任务还没发送。只有状态为 `PREPARED` 且 `send_attempts`
为 0 时，才可以显式调用 `sendPrepared`，它会重新读取模型菜单。这次验收已经点过
发送，没有走那条继续路径。

页面完成、`browser_response` 入库、主代理的内容复核和 Git `verify` 是四件不同的事。
这次是 `browser_response`。`review_approved` 保持 false，`cost_claim` 保持
`NOT_OBSERVABLE`。没有测量 token 或订阅节省。

这次只验收该提交的运行时摘要在一个宿主、一种界面语言上的一条合成任务。以后改动
`tools/pro_bridge` 或 `adapters/generic` 会得到另一个运行时摘要，不在本次验收内。
相同摘要也不验收其他宿主、其他界面语言或消费方项目。消费方项目需要自己做合成在线
验收。`certifies_current_tree` 保持 false，后来的工作区不会因为是当前树就自动沿用
这次验收。2026-10-19 的 325 字记录仍然只是历史观察，不能证明这次提交。

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

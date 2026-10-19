# Integrating task offload

[简体中文](INTEGRATION.zh-CN.md) · [Online behavior](ONLINE_WORKFLOW.md) · [Home](../README.md)

This page is for a primary agent in another repository. The helper hands one bounded analysis to ordinary web ChatGPT and returns the reply text. It does not decide whether that saves tokens. Packaging, browsing, waiting and review all cost something; total usage may stay the same or increase (`NOT_OBSERVABLE`).

## What you need

- Windows, Python 3.12, Git 2.32+, and Node.js 22+.
- A host JavaScript runtime with the documented CUA browser API (`listBrowsers`, `listTabs`, `getTab`, and a Playwright-like `tab.playwright`). A terminal does not provide `cua`.
- A logged-in ordinary Chat page, outside a project, with the model and effort already selected in the UI. The shipped reader understands the Chinese ordinary-Chat labels. Other locales need their own observer and send-button selector, then their own acceptance.
- An evidence directory **outside** this checkout. Put the ledger inside that directory. One ledger belongs to one source repository. Do not point two projects at the same ledger, and do not use an implicit ledger discovered from a parent Git directory.

No model API key is stored here. Do not put credentials, cookies or unauthorized private material in the task file.

## What the primary agent calls

Import `adapters/generic/online_host.mjs` inside the host browser runtime. `offloadTask` prepares the ledger receipt, checks the visible model and effort, claims one send, clicks once, waits, and returns a bounded excerpt. `recoverTask` reloads that same receipt and never clicks send.

```javascript
const online = await import('file:///D:/bridge-checkout/adapters/generic/online_host.mjs');
const transport = await import('file:///D:/bridge-checkout/adapters/generic/local_transport.mjs');
const evidence = 'D:/task-evidence/example-repo/task-001';
const run = transport.localTransport({
  root: 'D:/bridge-checkout',
  ledger: evidence + '/ledger',
  evidenceDirectory: evidence,
  receiptPath: evidence + '/prepare.json',
});
const tab = await cua.getTab(selectedTabId, { browser: selectedBrowserId });
const outcome = await online.offloadTask({
  tab, browserId: selectedBrowserId, run,
  evidenceDirectory: evidence,
  repository: 'example/consumer',
  sourceSha: pinnedCommit, sourceRef: 'main',
  taskFile: evidence + '/task.txt',
  orchestration: 'consumer-task-001',
  requestedModel: 'GPT-6', requestedEffort: 'Pro',
  observe: online.observeNewChat,
  composer: online.CHINESE_ORDINARY_CHAT.composer,
  sendButton: online.CHINESE_ORDINARY_CHAT.sendButton,
  budgetMs: 120000,
});
```

`requestedModel` and `requestedEffort` must already be the labels on the page. The helper reads them. It does not switch models, and a mismatch throws before any receipt or send.

Put the material itself in `task.txt`. A path written inside the prompt is only text; it does not upload the file.

On `outcome.status === 'COLLECTED'`, review `outcome.text`. `outcome.truncated` means the ledger still holds the rest and `readCollected` can return a larger excerpt, up to 65536 characters. `outcome.sha256` is the SHA-256 of the full UTF-8 reply. `review_approved` stays false. `cost_claim` stays `NOT_OBSERVABLE`. The helper does not apply the reply or run it as code.

## Recovery

Keep the evidence directory and `prepare.json`. After a crash, call only:

```javascript
const outcome = await online.recoverTask({ tab, run, budgetMs: 120000 });
```

- `COLLECTED` with `recovered: true` is the saved reply. The page is not required when the ledger already has `RESULT_SEEN`.
- `PREPARED_NOT_SENT` means the receipt exists and nothing was claimed. Recovery will not send it. Call `sendPrepared` once, then `recoverTask`.
- `SUBMISSION_UNKNOWN`, `HOST_TIMEOUT`, `TASK_DEADLINE`, `LOGIN_REQUIRED`, `PAGE_ERROR`, `TAB_GONE`, `TARGET_CHANGED`, `RESPONSE_CHANGED`, and `COLLECTION_REFUSED` all set `doNotResend: true`.

`doNotResend: true` means the next step is to read the original receipt or wait on that same task. It does not mean you should prepare a replacement, click send again, or extend `deadline_at`.

`budgetMs` bounds browser URL reads, page reads, and the pauses between them. It starts after the ledger status read, so time spent opening the local ledger is not reported as `HOST_TIMEOUT` before the tab is checked. Each `tab.url()` and page read is limited by the time still left in that budget, the original `deadline_at`, and cancellation. When that time is gone, the helper does not start another read, does not click send, and does not change the deadline. If the host browser call cannot be cancelled, the call may continue in the background after this wait has already stopped.

The model is verified before the claim. `observeNewChat` opens the model menu, reads the one checked model and the effort on that menu, then closes the menu. The requested names are compared with those labels. A request, a default, or an older task is not a substitute for that menu read. The claim stores the menu observation, including the model name.

After the claim, the helper reads the closed model control and does not click it, reopen the menu, or change the model or effort. On the current Chinese ordinary Chat page that control shows the effort, often only `Pro`. The visible effort is the post-claim observation. The model stays the menu observation bound to this task, browser, and tab. The helper records that source and does not present the carried model as a new closed-control observation, and it does not infer a model from `Pro`. `readStandaloneChat` reports `work: true` when the Work control is not pressed; that means ordinary Chat. A closed control that shows only the effort does not independently confirm the model, and the check does not rule out a person changing the model after the menu closed. When the closed control shows both the same model and the same effort, that pair is a post-claim model observation.

Menu evidence expires after 120 seconds. It is refused for another task, browser, or tab, and when preflight stopped before the evidence was bound. An unsent task is read from the menu again by `sendPrepared`. When a claim already exists, a visible effort, page, tab, or browser change leaves the task `SUBMISSION_UNKNOWN`. `recoverTask` does not send and does not reopen the menu. Missing, ambiguous, or expired model evidence refuses the send.

The evidence directory must be strictly outside the checkout on both the path you pass and the path after resolving symlinks or Windows junctions. The checkout itself, a subdirectory, and a link that lands inside the checkout are refused. A sibling whose name only shares a prefix is outside. The ledger must be inside the evidence directory on both paths. One ledger still belongs to one source repository.

## Body text and Git delivery

| Path | Purpose | Limit |
| --- | --- | --- |
| `browser_response` (`offloadTask` / `recoverTask`) | The reply rendered in the chat is stored in the explicit ledger. The primary agent reads that text locally. | The web page does not need to read your repository or write GitHub. The text can include UI labels. It is not review approval. |
| `git` (`verify`) | An external writer puts the agreed result files on the result branch. The existing verifier checks those Git objects. | This helper does not write those files. `verify` refuses to treat a `browser_response` task as Git approval. |

Use `browser_response` for a self-contained analysis. Use Git delivery only when a separate writer is actually available. Do not chain them to upgrade a chat reply into an approved change.

## Accepted and not accepted

Covered by offline tests: one claim, one click, stable conversation URL before the session is recorded, original-receipt recovery, hash-checked bounded read, refusal to mix repositories in one ledger, refusal to treat the checkout or a link into it as the evidence directory, a pre-claim menu read of the model and effort, a post-claim recheck of the visible effort that carries the model only from that recent menu evidence, refusal of expired or cross-task evidence, a browser wait budget that does not include the ledger status read, a URL or page read that stops at the remaining budget, and refusal to resend after an uncertain click, login wall, missing tab, timeout, deadline, identity mismatch or changed body. Offline tests do not log in or send to ChatGPT. They do not accept this checkout.

The 2026-10-19 Codex in-app-browser run is historical. Its runtime digest is recorded in `capability-manifest.json`. That digest does not match later source trees, so the run does not certify this checkout, other hosts, other locales, or a consumer project.

Not accepted: token or subscription savings, backend model identity, who is billed, unattended production use, a universal browser driver, credential custody, and turning a chat reply into a GitHub write.

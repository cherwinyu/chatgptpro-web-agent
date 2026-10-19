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

`doNotResend: true` means the next step is to read the original receipt or wait on that same task. It does not mean you should prepare a replacement, click send again, or extend `deadline_at`. A host wait budget (`budgetMs`) only stops this wait. It does not change the task deadline stored at prepare time.

## Body text and Git delivery

| Path | Purpose | Limit |
| --- | --- | --- |
| `browser_response` (`offloadTask` / `recoverTask`) | The reply rendered in the chat is stored in the explicit ledger. The primary agent reads that text locally. | The web page does not need to read your repository or write GitHub. The text can include UI labels. It is not review approval. |
| `git` (`verify`) | An external writer puts the agreed result files on the result branch. The existing verifier checks those Git objects. | This helper does not write those files. `verify` refuses to treat a `browser_response` task as Git approval. |

Use `browser_response` for a self-contained analysis. Use Git delivery only when a separate writer is actually available. Do not chain them to upgrade a chat reply into an approved change.

## Accepted and not accepted

Accepted by offline tests: one claim, one click, stable conversation URL before the session is recorded, original-receipt recovery, hash-checked bounded read, refusal to mix repositories in one ledger, and refusal to resend after an uncertain click, login wall, missing tab, timeout, deadline, identity mismatch or changed body.

The 2026-10-19 Codex in-app-browser run is historical. Its runtime digest is recorded in `capability-manifest.json`. That digest does not match later source trees, so the run does not certify this checkout, other hosts, other locales, or a consumer project.

Not accepted: token or subscription savings, backend model identity, who is billed, unattended production use, a universal browser driver, credential custody, and turning a chat reply into a GitHub write.

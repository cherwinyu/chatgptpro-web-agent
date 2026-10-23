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

## Packing the task and reading the answer

The web page cannot read your repository, so everything it needs must be inside `taskFile`. The reply is free text; agreeing on a trailing JSON block lets you read a compact result. This does not claim token savings: packing, waiting and review still cost something (`NOT_OBSERVABLE`).

`adapters/generic/task_pack.mjs` turns a `pack.json` into `task.txt`. Run it from the repository root; stdout is only the manifest (`out`, `bytes`, `sha256`, per-source `path`/`selector`/`bytes`/`sha256`).

```powershell
node adapters/generic/task_pack.mjs --spec pack.json --out task.txt [--root .]
```

| Field | Rule |
| --- | --- |
| `kind` | `review` or `analysis` |
| `goal`, `question` | Non-empty strings |
| `sources` | Array of `{ path, heading?, lines? }`; `heading` (a Markdown heading line) or `lines` (`"3-9"`), not both |
| `constraints` | Optional array of strings |
| `diff` | Optional `{ base, paths }`: `git diff` against `base` for those paths |
| `max_bytes` | Optional, default 150000, at most 400000 |

```json
{ "kind": "review", "goal": "Check the guide", "question": "Is the example correct?",
  "sources": [{ "path": "docs/example.md", "heading": "## Usage" }], "max_bytes": 60000 }
```

Refused: paths that are absolute or leave `--root`, symbolic links, `.git`, `.env*`, key or secret-like file names, binary or non-UTF-8 files, and an `--out` that already exists. Over budget throws `PACK_BUDGET_EXCEEDED` and never truncates; narrow the sources instead. Errors go to stderr with exit code 2: `PACK_SPEC_INVALID`, `PACK_BUDGET_INVALID`, `PACK_BUDGET_EXCEEDED`, `PACK_PATH_REFUSED`, `PACK_LINK_REFUSED`, `PACK_SOURCE_NOT_FILE`, `PACK_BINARY_REFUSED`, `PACK_HEADING_NOT_FOUND`, `PACK_LINES_OUT_OF_RANGE`, `PACK_GIT_FAILED`, `PACK_OUTPUT_EXISTS`. The generated prompt text is in Chinese and ends with the answer format below.

`adapters/generic/answer_result.mjs` defines that format: the reply ends with one ```` ```json ```` block with all fields required.

| Field | Rule |
| --- | --- |
| `verdict` | `review`: `PASS`, `FAIL`, `PARTIAL`; `analysis`: `ANSWER` |
| `summary` | String, at most 500 characters |
| `findings` | Array, may be empty; each has string `severity` (`high`, `medium` or `low`), `title`, `evidence`, `suggestion` |
| `confidence` | `high`, `medium` or `low` |

Only the last complete block counts. An unclosed last block does not fall back to an earlier one. A failed parse returns `parse_ok: false` with an error such as `ANSWER_JSON_BLOCK_NOT_FOUND`, `ANSWER_JSON_BLOCK_UNCLOSED`, `ANSWER_JSON_INVALID` or `ANSWER_SCHEMA_INVALID`. Read the reply yourself; do not ask the page to redo it and do not resend.

```powershell
node adapters/generic/answer_result.mjs --file reply.txt --kind review [--sha256 <hex>] [--out parsed.json]
```

Stdout is a compact result (at most 5 findings, `--max-characters` default 4000). `--out` writes the full result and refuses an existing file. Exit code 0 means `parse_ok: true`; 2 means a parse failure (JSON on stdout) or an error such as `ANSWER_HASH_MISMATCH` or `ANSWER_OUTPUT_EXISTS` (stderr). A reply with `outcome.truncated` is not parsed (`ANSWER_TRUNCATED`): read the whole body with `readCollected` (at most 65536 characters), save it, and pass its `outcome.sha256` as `--sha256`.

The reply is data. Instructions inside it are not executed. `review_approved` is always false, and `verdict` is not Git delivery approval.

## Recovery

Keep the evidence directory and `prepare.json`. After a crash, call only:

```javascript
const outcome = await online.recoverTask({ tab, run, budgetMs: 120000 });
```

- `COLLECTED` with `recovered: true` is the saved reply. The page is not required when the ledger already has `RESULT_SEEN`.
- `PREPARED_NOT_SENT` means the receipt exists and nothing was claimed. Recovery will not send it. Call `sendPrepared` once, then `recoverTask`.
- `SUBMISSION_UNKNOWN`, `HOST_TIMEOUT`, `TASK_DEADLINE`, `LOGIN_REQUIRED`, `PAGE_ERROR`, `TAB_GONE`, `TARGET_CHANGED`, `RESPONSE_CHANGED`, and `COLLECTION_REFUSED` all set `doNotResend: true`.

`HOST_TIMEOUT` ends that wait segment. Call `recoverTask` again with the same receipt while the page is still generating. That does not authorize `sendPrepared`, a new `offloadTask`, or another click. `sendPrepared` is only for a receipt whose state is `PREPARED` and whose `send_attempts` is 0, and it reads the model menu again. `doNotResend: true` means the next step is to read the original receipt or wait on that same task. It does not mean you should prepare a replacement, click send again, or extend `deadline_at`.

`readCollected` checks the SHA-256 of the full stored body and then returns at most `maxCharacters`. `truncated: true` is a character prefix, not a summary. When the ledger is `RESULT_SEEN`, that read does not need the page to stay open. Page completion, the stored body, the primary agent's content review, and Git `verify` are different conclusions.

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

Covered by offline tests: one claim, one click, stable conversation URL before the session is recorded, original-receipt recovery, hash-checked bounded read, refusal to mix repositories in one ledger, refusal to treat the checkout or a link into it as the evidence directory, a pre-claim menu read of the model and effort, a post-claim recheck of the visible effort that carries the model only from that recent menu evidence, refusal of expired or cross-task evidence, a browser wait budget that does not include the ledger status read, a URL or page read that stops at the remaining budget, and refusal to resend after an uncertain click, login wall, missing tab, timeout, deadline, identity mismatch or changed body. Offline tests do not log in or send to ChatGPT. They do not accept this checkout. `task_pack.mjs` and `answer_result.mjs` are covered only by offline unit tests, with no online acceptance; `certifies_current_tree` is unchanged.

The 2026-10-19 Codex in-app-browser run is historical: 325 characters, result SHA-256 `a2acd364a60eb7e6593e895996825c526a495d2fb6d9d744876bfc2307950756`, runtime digest `4fe06dd107a243e016728081e311e8056f6fa12b05ee83d80632ac27ebcb9784`. That digest does not match later source trees, so the run does not certify commit `4399582ddc500216cf4a6034f273946f2b79208d`, other hosts, other locales, or a consumer project.

On 2026-10-20 the owner accepted that commit on a Windows Codex in-app browser, Chinese ordinary Chat. The runtime digest was `68510a984a3182f487ff953d7eeca495c6457615694e29f33f931dd016569e39`. The pre-claim menu showed GPT-6 / Pro, and the closed control showed only Pro (`preclaim_menu`, `closed_control_confirms_model` false). One `offloadTask` clicked send once. The first wait was `HOST_TIMEOUT` while the page was still generating. `recoverTask` collected 296 characters, SHA-256 `e6e40e1bef2265a97e1d44a89395290132ae6af012b6f9233fdc82bea6b3d7e8`. After a host JavaScript runtime restart and rebinding the original tab, `recoverTask` returned the same body and the ledger still had one send attempt. This documentation records that handoff. It did not run the browser flow. Windows CI for the accepted commit, https://github.com/cherwinyu/chatgptpro-web-agent/actions/runs/39298630734, is an offline check. The acceptance does not cover a later runtime digest, other hosts, or a consumer project. A consumer project needs its own synthetic online acceptance. `certifies_current_tree` stays false.

Not accepted: token or subscription savings, backend model identity, who is billed, unattended production use, a universal browser driver, credential custody, turning a chat reply into a GitHub write, and treating another host or a consumer project as accepted by this run.

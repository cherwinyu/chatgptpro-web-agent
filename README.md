# chatgptpro-web-agent

[简体中文](README.zh-CN.md) · [Integrate a primary agent](docs/INTEGRATION.md) · [Online workflow](docs/ONLINE_WORKFLOW.md) · [Quick start](docs/QUICKSTART.md) · [Research boundaries](docs/RESEARCH_USE.md) · [License](LICENSE) · [Security](SECURITY.md)

**Lets a local coding agent hand one bounded, self-contained task to a logged-in ordinary ChatGPT web conversation, send it at most once, and bring the reply back locally.** An unofficial, noncommercial Windows research preview, not an OpenAI product.

- **What it is for**: a second opinion, an option comparison, or an analysis whose material fits completely in one task file. Not for work that needs the whole repository, GitHub writes, or unattended runs.
- **Core idea**: pack the material → send once → recover from the original receipt → parse a structured result. The primary agent keeps its context and the final decision.
- **Status**: Pre-release `v0.1.0-rc.6`. The local protocol and adapters are implemented and covered by offline tests and Windows CI. Online evidence comes only from single-host synthetic runs, and the current runtime digest has no formal online acceptance. Whether this reduces token or subscription usage has not been measured.

## What it does

A primary agent working locally (for example, a coding agent in Codex or Cursor) packs a bounded, self-contained task. The helper puts that task into a logged-in ordinary ChatGPT web conversation, sends it once, waits, stores the reply in a local ledger, and can parse it into a compact structured result. The goal is to make "give this to web ChatGPT" repeatable, inspectable, and recoverable, without accidental duplicate sends.

Good fits are tasks whose material fits completely in the task file:

- a second opinion on a code review or a diff;
- a comparison of architecture options;
- a synthesis or analysis of supplied material.

Poor fits:

- tasks that need to read a whole repository;
- tasks that need to write to GitHub (the reply is data, not a Git change);
- unattended or production runs.

## Core idea

1. **Pack**: `adapters/generic/task_pack.mjs` turns a `pack.json` (selected files or file sections, optionally a `git diff`) into one self-contained task file. It has a byte budget and refuses an over-budget pack instead of truncating it.
2. **Send once**: `offloadTask` in `adapters/generic/online_host.mjs` checks the visible model and effort labels, records a durable single-send claim in the local ledger, clicks send once, waits, and returns a bounded excerpt.
3. **Recover from the original receipt**: `recoverTask` reloads the same receipt to keep waiting or to read the stored reply. It never clicks send.
4. **Parse**: `adapters/generic/answer_result.mjs` reads the agreed trailing JSON block, including its rendered-page text form, into a verdict, summary, and findings.

Three design choices follow from this flow:

- **At most once.** A click whose outcome is unknown may already have reached the page, so a resend could submit the same task twice. The send claim must be on disk before the click. After an uncertain click (`SUBMISSION_UNKNOWN`), a timeout, a login wall, or a lost tab, the helper never resends automatically; the next step is to recover from the original receipt, not to prepare a replacement task. This is local at-most-once, not distributed exactly-once.
- **The web page cannot read your repository.** The conversation sees only the text of the task file. Everything it needs must be packed into that file; a file path written in a prompt is only text.
- **The primary agent keeps the final decision.** The reply is stored as data. The helper does not run or apply it, `review_approved` is always false, and a parsed `verdict` is not Git delivery approval. The separate Git verification path (`tools/pro_bridge/cli.py verify`) refuses to treat a chat reply as Git approval.

The caller contract and recovery statuses are in the [integration guide](docs/INTEGRATION.md); selectors and failure behavior are in the [online workflow](docs/ONLINE_WORKFLOW.md).

## Status

The current Pre-release is [`v0.1.0-rc.6`](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6). The source is publicly available, but this is not a formal stable Release, and it is not an official OpenAI integration.

### Implemented, with offline tests

| Part | Code | Offline tests |
| --- | --- | --- |
| Local ledger and receipts: immutable task manifests, original receipts, single-send claims, recovery checks, Windows ledger persistence | `tools/pro_bridge/`, launched by `adapters/generic/run_bridge.py` | `tests/pro_bridge/test_faults.py`, `tests/pro_bridge/test_security.py`, `tests/audit/` |
| Online offload and recovery (`offloadTask`, `recoverTask`): model/effort observation from the model menu, the single-send claim, one send, waiting, and collection; plus the ledger transport with the hash-checked bounded reader `readCollected` | `adapters/generic/online_host.mjs`, `adapters/generic/local_transport.mjs` | `tests/audit/online_host.test.mjs` |
| Read-only CUA browser adapter: browser and tab binding, page observation, completion detection, and the reply text only when explicitly requested. It does not send and does not read the model menu | `adapters/generic/browser_host.mjs` | `tests/audit/browser_host.test.mjs` |
| Task packing with a byte budget and `git diff` guards | `adapters/generic/task_pack.mjs` | `tests/audit/task_pack.test.mjs` |
| Answer parsing, including the rendered-page text form | `adapters/generic/answer_result.mjs` | `tests/audit/answer_result.test.mjs` |
| Separate Git result verification | `tools/pro_bridge/cli.py verify` | `tests/pro_bridge/test_security.py`, `tests/audit/test_browser_delivery.py` |
| Slim Runtime package and `identify` provenance check | `tools/pro_bridge/` | `tests/pro_bridge/test_runtime_package.py` |
| Experimental Cursor page-channel example (Source only) | `examples/cursor_page_channel/` | `tests/audit/cursor_page_channel.test.mjs` |

Windows CI runs these offline checks on every push and pull request. The tests use synthetic fixtures; they do not log in, open ChatGPT, or send anything. Windows CI is an offline check only.

### Single-host synthetic online evidence

Recorded in `capability-manifest.json` and in [Accepted and not accepted](docs/INTEGRATION.md#accepted-and-not-accepted). Every run used synthetic read-only tasks on one host with a logged-in Chinese ordinary Chat page. Visible model and effort labels are page text, not backend model identity.

| Date | Host | What ran | Formal acceptance | Covers the current runtime digest |
| --- | --- | --- | --- | --- |
| 2026-10-19 | Codex desktop in-app browser; visible GPT-6 / Pro | One synthetic review, sent once, reply collected | No: historical observation (`online_offload`) | No. Its runtime digest does not match later trees, so it does not certify this checkout. |
| 2026-10-20 | Windows Codex in-app browser; visible GPT-6 / Pro | One synthetic review, sent once. The first wait ended in `HOST_TIMEOUT`; `recoverTask` on the same receipt collected the reply, and did so again after a host runtime restart, still with one send attempt | Yes: the owner's single-host synthetic acceptance (`synthetic_online_acceptance`) | No. It covers only the commit and runtime digest recorded there. |
| 2026-10-25 | Windows Cursor IDE browser; not the Pro tier | The Cursor page-channel example: three attempts, two real sends. task1 was refused before sending because the requested label did not match the page; no receipt was written. task2 and task3 used task files generated by `task_pack.mjs`. task2's reply was collected but did not parse in its rendered form; after the parser fix it parsed offline. task3's reply was collected, parsed, and recovered from the same receipt without a resend | No: observation only (`cursor_page_channel_observation`) | No. The last run (task3) recorded the same runtime digest as this tree (derived from the manifest record), but `formal_acceptance` and `certifies_current_tree` are both false, so it is not an acceptance. |

The current runtime digest has no formal online acceptance. A consumer project needs its own synthetic online acceptance.

### Not verified or still open

- **Token or subscription usage.** The initial hypothesis was that offloading suitable work **might** reduce the primary agent's usage. It has not been measured (`NOT_OBSERVABLE`). Packing, browsing, waiting, and review all cost something; total usage may stay the same or increase.
- **Backend model identity and billing.** Page labels do not show which backend model answered or who is billed.
- **Other hosts, UI languages, and consumer projects.** The shipped page profile reads Chinese ordinary-Chat labels; other UI languages need their own observer, send-button selector, and acceptance.
- **Large tasks.** A task-size limit for the page fill path has not been measured; see the [packing guidance](docs/INTEGRATION.md#packing-the-task-and-reading-the-answer).
- **Attachment-only replies.** They can be detected as page completion but are not accepted as text collection.
- **Not accepted at all:** unattended or production use, credential custody, and turning a chat reply into a GitHub write.

## Download and start

You need only one ZIP from the [`v0.1.0-rc.6` Release page](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6).

| Goal | Package | Extracts to |
| --- | --- | --- |
| Offline demo, or install the runtime for a host | Slim Runtime: `chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim.zip` | `chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim` |
| Tests, modification, research, or the Cursor page-channel example | Source: `chatgptpro-web-agent-v0.1.0-rc.6-source.zip` | `chatgptpro-web-agent-v0.1.0-rc.6-source` |

Slim Runtime contains the runtime code (including the online offload), example schemas, the user documentation (not the migration notes), and a `release-manifest.json`. It has no `tests/`, `scripts/`, `examples/`, or CI. Source is the full public tree listed in `PUBLIC_FILELIST.txt`, including tests, scripts, and examples. It has no release manifest, so a Manifest Pin does not apply to it. The Cursor page-channel example (`examples/cursor_page_channel/`) is only in Source.

1. Before extracting, hash the ZIP with `Get-FileHash -Algorithm SHA256` and compare it with the SHA-256 on the Release page. The Release page is the reference; a hash inside the ZIP is not the trust root.
2. For Slim Runtime, copy the Manifest Pin from the Release page and, in the extracted folder, run `py -3.12 adapters/generic/run_bridge.py identify --trusted-manifest-sha256 <pin>`. Require `provenance_status` `VERIFIED`.
3. Run the credential-free offline demo (Windows, Python 3.12, Git 2.32+):

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

Both commands stay offline (`network_attempted` false, `web_send_count` 0), and the preview always reports `send_authorized` false. A successful offline demo does not show that an online send works on any host. Full commands are in the [quick start](docs/QUICKSTART.md).

To call the online offload, the host also needs Node.js 22+, a host JavaScript runtime with the documented CUA browser API, and a logged-in ordinary Chat page. A terminal alone cannot drive the browser. Start from the [integration guide](docs/INTEGRATION.md).

## Version history

All are Pre-releases; none is a formal stable Release.

- `v0.1.0-rc.6`: rc.5 plus documentation updates; runtime code unchanged.
- `v0.1.0-rc.5`: adds `task_pack.mjs`, `answer_result.mjs`, and the experimental Cursor page-channel example (Source only).
- `v0.1.0-rc.4`: the first Pre-release with the ordinary ChatGPT web offload from PR #8: `offloadTask` / `recoverTask` in `online_host.mjs` and `readCollected` in `local_transport.mjs`.
- `v0.1.0-rc.3`: the offline research preview; it does not include the online offload.

## Boundaries, license, and rights

- Unofficial and noncommercial. The [PolyForm Noncommercial License 1.0.0](LICENSE) is reproduced unaltered. It is **not** an OSI-approved open-source license, covers only rights the licensor controls, grants no permission for third-party code, hosted services, or brands, and does not mean third-party rights have been cleared ([scope notice](docs/legal/LICENSE_SCOPE.md)).
- Not production-ready. The not-for-production statement is a support recommendation, not an added license condition.
- It is not a universal browser driver or a model API client, and it does not hold credentials. Use only accounts and services you are authorized to use; see [research boundaries](docs/RESEARCH_USE.md).

## Contributing and security

Use synthetic fixtures, scoped changes, and independently reproducible tests. Read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md) before filing issues; do not post receipts, credentials, or private task data.

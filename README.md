# chatgptpro-web-agent

[简体中文](README.zh-CN.md) · [Integrate a primary agent](docs/INTEGRATION.md) · [Online workflow](docs/ONLINE_WORKFLOW.md) · [Quick start](docs/QUICKSTART.md) · [Research boundaries](docs/RESEARCH_USE.md) · [License](LICENSE) · [Security](SECURITY.md)

**Lets a local coding agent hand one bounded, self-contained task to a logged-in ordinary ChatGPT web conversation, send it once, and bring the reply back locally.** An unofficial, noncommercial Windows research preview.

## What it does

A primary agent working locally (for example, a coding agent in Codex or Cursor) packs a bounded, self-contained task and hands it to a logged-in ordinary ChatGPT web session. The helper sends it exactly once, waits, recovers from the original receipt after an interruption, stores the reply locally, and can parse it into a compact structured result.

1. **Pack**: `adapters/generic/task_pack.mjs` turns a `pack.json` into one self-contained task file. It has a byte budget and refuses an over-budget pack instead of truncating it.
2. **Send once**: `offloadTask` in `adapters/generic/online_host.mjs` checks the visible model and effort labels, records a durable single-send claim, clicks send once, waits, and returns a bounded excerpt.
3. **Recover if needed**: `recoverTask` reloads the same receipt to keep waiting or to read the stored reply. It never clicks send.
4. **Parse**: `adapters/generic/answer_result.mjs` reads the agreed trailing JSON block, including its rendered-page text form, into a verdict, summary, and findings. `review_approved` is always false.

The web page cannot read your repository. Everything it needs must be packed into the task file; a file path written in a prompt is only text.

## What it is for

Good fits are tasks whose material fits completely in the task file:

- a second opinion on a code review or a diff;
- a comparison of architecture options;
- a synthesis or analysis of supplied material.

Poor fits:

- tasks that need to read a whole repository;
- tasks that need to write to GitHub (the reply is data, not a Git change);
- unattended or production runs.

## Why

The goal is to make "give this to web ChatGPT" repeatable, inspectable, and recoverable, without accidental duplicate sends. The primary agent keeps its context, its local tools, and the final decision. The initial hypothesis was that offloading suitable work **might** reduce the primary agent's token or subscription usage. That is still an open question to evaluate, not a project result. Packing, browsing, waiting, and review all cost something; total usage may stay the same or increase.

## Status

The current Pre-release is [`v0.1.0-rc.6`](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6). The source is publicly available, but this is not a formal stable Release, and it is not an official OpenAI product or integration.

Online evidence, recorded in `capability-manifest.json` and in [Accepted and not accepted](docs/INTEGRATION.md#accepted-and-not-accepted):

- **2026-10-19**: a historical run in the Codex in-app browser. Its runtime digest does not match later trees, so it does not certify this checkout.
- **2026-10-20**: the owner's single-host synthetic acceptance in the Windows Codex in-app browser, Chinese ordinary Chat (`synthetic_online_acceptance`). It covers only the commit and runtime digest recorded there.
- **2026-10-25**: a single-host synthetic observation of the Cursor page-channel example in the Cursor IDE browser, Chinese ordinary Chat, not the Pro tier (`cursor_page_channel_observation`). It is not a formal acceptance.

The current runtime digest has no formal online acceptance. A consumer project needs its own synthetic online acceptance. Windows CI is an offline check only.

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

To call the online offload, the host also needs Node.js 22+, a host JavaScript runtime with the documented CUA browser API, and a logged-in ordinary Chat page. The [integration guide](docs/INTEGRATION.md) is the caller contract for `offloadTask` and `recoverTask`; the [online workflow](docs/ONLINE_WORKFLOW.md) covers selectors and failure behavior. A terminal alone cannot drive the browser.

## What is included

- A local Python ledger and receipts under `tools/pro_bridge/`: immutable task manifests, original receipts, single-send claims, and recovery checks, launched by `adapters/generic/run_bridge.py`.
- `adapters/generic/online_host.mjs`: `offloadTask` and `recoverTask`.
- `adapters/generic/local_transport.mjs`: the ledger transport, including the hash-checked bounded reader `readCollected`.
- `adapters/generic/browser_host.mjs`: the optional CUA browser-host adapter for model/effort observation, one send, completion detection, and collection.
- `adapters/generic/task_pack.mjs`: task-file packing with a budget.
- `adapters/generic/answer_result.mjs`: parsing of the trailing JSON answer block.
- The separate Git result verification path (`tools/pro_bridge/cli.py verify`), which does not upgrade a chat reply into approval.
- `examples/cursor_page_channel/`: an experimental Cursor page-channel example, Source only.

## Version history

All are Pre-releases; none is a formal stable Release.

- `v0.1.0-rc.6`: rc.5 plus documentation updates; runtime code unchanged.
- `v0.1.0-rc.5`: adds `task_pack.mjs`, `answer_result.mjs`, and the experimental Cursor page-channel example (Source only).
- `v0.1.0-rc.4`: the first Pre-release with the ordinary ChatGPT web offload from PR #8: `offloadTask` / `recoverTask` in `online_host.mjs` and `readCollected` in `local_transport.mjs`.
- `v0.1.0-rc.3`: the offline research preview; it does not include the online offload.

## Boundaries, license, and rights

- Unofficial and noncommercial. The [PolyForm Noncommercial License 1.0.0](LICENSE) is reproduced unaltered. It is **not** an OSI-approved open-source license, covers only rights the licensor controls, grants no permission for third-party code, hosted services, or brands, and does not mean third-party rights have been cleared ([scope notice](docs/legal/LICENSE_SCOPE.md)).
- Not production-ready. The not-for-production statement is a support recommendation, not an added license condition.
- Visible model labels on the page are not backend model identity.
- Token or subscription savings are an unverified hypothesis, not a result.
- Single send is local at-most-once, not distributed exactly-once. An uncertain send (`SUBMISSION_UNKNOWN`) is never resent automatically.
- The reply is data, not Git delivery approval.
- It is not a universal browser driver or a model API client, and it does not hold credentials. Use only accounts and services you are authorized to use; see [research boundaries](docs/RESEARCH_USE.md).

## Contributing and security

Use synthetic fixtures, scoped changes, and independently reproducible tests. Read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md) before filing issues; do not post receipts, credentials, or private task data.

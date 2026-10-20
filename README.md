# chatgptpro-web-agent

[简体中文](README.zh-CN.md) · [Integrate a primary agent](docs/INTEGRATION.md) · [Online workflow](docs/ONLINE_WORKFLOW.md) · [Offline quick start](docs/QUICKSTART.md) · [Research boundaries](docs/RESEARCH_USE.md) · [License](LICENSE) · [Security](SECURITY.md)

**A Windows research helper that lets a host agent hand a bounded analysis to ordinary web ChatGPT, wait for it, and collect the reply locally.**

## Which download

The public download is Pre-release [`v0.1.0-rc.3`](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.3). It is not a formal stable Release, and it is not an official OpenAI product. That package is the offline research preview frozen at commit `3051279`. Later main, including the online offload from PR #8, is not in rc.3.

| Goal | Download |
| --- | --- |
| First offline demo | Slim Runtime |
| Full source and tests | Source |
| Modify or research | Source |
| Verify release integrity | Release SHA-256 digests |

You do not need both ZIPs. Slim Runtime (`chatgptpro-web-agent-v0.1.0-rc.3-runtime-slim.zip`, SHA-256 `4b5d65eefd337fa45d5dd2389d7fe409b2be6ee5494b86b2ac3084201d5208fd`) is the 42-file offline bundle: bridge code, example schemas, license, notices, and the quick start. It omits `tests\`, `scripts\`, and CI. Source (`chatgptpro-web-agent-v0.1.0-rc.3-source.zip`, SHA-256 `0a3f8d0f94bdd3b9ebcb055dfee9a60237e584b38ccc70f5d1329c03c1f60495`) is the full 58-file freeze, including tests and `scripts\publication_audit.py`. Hash the ZIP with `Get-FileHash -Algorithm SHA256` before extracting. A hash inside the ZIP is not the trust root. The offline demo needs Windows, Python 3.12, and Git 2.32 or later. The audit and unit tests are Source-only. Steps are in the [offline quick start](docs/QUICKSTART.md).

## Why we built it

An agent working in a local repository sometimes needs a second, substantial analysis: a code review, an architecture comparison, or a synthesis of material. We wanted a repeatable way to give that work to a web ChatGPT conversation, while the local agent keeps responsibility for context, local tools and the final decision. A useful handoff needs more than pasting a prompt: it must identify the task, avoid accidental duplicate sends, wait within a deadline, recover an interrupted session and bring the right answer back.

Our initial hypothesis was that moving suitable work out of the main agent **might** reduce its token or subscription usage. That remains an open question, not a project result or a promised benefit. Packaging material, operating the browser, waiting and reviewing the answer also have costs; total usage may stay the same or increase. The current goal is to make task sharing usable and inspectable so that its value can be evaluated on real workloads.

## What is included

- An optional CUA browser-host adapter for observed model/effort checks, one send, completion detection and response collection.
- A local Python ledger with immutable task manifests, original receipts, single-send claims and recovery checks.
- A direct `browser_response` path for self-contained analysis, plus the separate original Git-result verification path.
- A bounded, hash-checked result reader for the main agent, synthetic fixtures, offline regressions and Windows CI.

This is an independent noncommercial research preview, publicly available under PolyForm Noncommercial 1.0.0. It has fresh Git history and an explicit file allowlist. It is not an official OpenAI product or endorsed integration. A public Pre-release, `v0.1.0-rc.3`, is on GitHub Releases; it is not a formal stable Release.

## How to start

First run the credential-free offline demo. From a Slim Runtime extract, a Source extract, or this repository's root:

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

Requirements: Windows, Python 3.12, Git 2.32+. No credential, session cookie, account login or model API key is required to run these examples. The permission preview always reports `send_authorized=false`; it cannot create a send entitlement. These two commands are in the Slim Runtime ZIP. `scripts\publication_audit.py` and `tests\` are Source-only. A demo that stays offline (`network_attempted` false, `web_send_count` 0) is not proof that an online ChatGPT send has been verified for every host.

An actual handoff is documented for current main and is outside Pre-release `v0.1.0-rc.3`. The host additionally needs Node.js 22+, its documented CUA browser API and a logged-in ordinary Chat page. In that host's JavaScript browser runtime, call `offloadTask` once. If the page is still generating, call `recoverTask` on the same receipt for another wait. It does not click send. The [integration guide](docs/INTEGRATION.md) is the caller contract. The [online guide](docs/ONLINE_WORKFLOW.md) records selectors, failure behavior, and the browser notes. A terminal alone cannot drive the browser, and putting local file paths in a prompt does not upload their contents.

## Online integration scope

This section describes current main. Published Pre-release `v0.1.0-rc.3` (freeze `3051279`) does not include the PR #8 online handoff, `offloadTask`, or the acceptance records below.

On 2026-10-19 a synthetic read-only review completed in the Codex in-app browser with visible labels GPT-6 / Pro. Its 325-character rendered reply was collected; original-receipt recovery retained one send attempt and the same reply hash. That run is the historical record in `capability-manifest.json`. Its runtime digest does not match later trees, so the run does not certify this checkout, other hosts, other locales, or a consumer project.

On 2026-10-20 the owner accepted one later synthetic read-only review on a Windows Codex in-app browser, Chinese ordinary Chat. The accepted commit and runtime digest are `synthetic_online_acceptance` in `capability-manifest.json`. The pre-claim menu showed GPT-6 / Pro, and the closed control showed only Pro. One `offloadTask` clicked send once. The first wait returned `HOST_TIMEOUT` while the page was still generating. `recoverTask` on the same receipt collected a 296-character reply. After the host JavaScript runtime was restarted and the original tab was bound again, `recoverTask` returned that same reply, and the ledger still had one send attempt. A 120-character `readCollected` excerpt was a truncated prefix; the full body stayed in the ledger. This documentation records the owner's handoff. It did not run that browser flow. The acceptance is one host, one locale, and one synthetic task. It does not accept other hosts, other locales, or a consumer project. A consumer project needs its own synthetic online acceptance. Windows CI remains an offline check. Browser-response delivery does not need a GitHub writer. The original Git verification path remains separate and is not an upgrade of a chat reply. There is no universal browser driver, hosted-model API client, or credential custodian. Token and subscription savings are not an achieved result.

## Distribution and rights

The [PolyForm Noncommercial License 1.0.0](LICENSE) is reproduced without alteration and covers only interests the licensor actually controls. It permits certain noncommercial use, modification and redistribution subject to its conditions; it is **not** an OSI-approved open-source license. It does not grant permissions for third-party code or hosted services, and it does not mean third-party rights have been cleared. [Scope notice](docs/legal/LICENSE_SCOPE.md). The experimental *not-for-production* statement is a support recommendation, not an unannounced change to the license. This preview is not production-ready.

**Public Pre-release `v0.1.0-rc.3` is not a formal stable Release.** The published pre-release tag `v0.1.0-rc.3` is an earlier snapshot and does not include the online offload. Download the Source ZIP or the Slim Runtime ZIP from that Release and check each ZIP with `Get-FileHash -Algorithm SHA256` against the digests on the Release page (also listed in the [quick start](docs/QUICKSTART.md)). rc.3 stays the offline research preview at freeze `3051279`. Later main, including PR #8, is not in that Pre-release. Pin the commit you checked out and confirm the installed runtime in the [integration guide](docs/INTEGRATION.md). A candidate package name is not a tag, a formal Release, or online acceptance. Offline regressions and local online acceptance are separate evidence. A passing Windows CI job or a successful offline demo does not show that an online ChatGPT send has been verified for every host. Native Windows CI on this repository's own exact commit is the engineering check for that snapshot; historical repository CI results cannot certify it. Component-level source rights, attribution, and brand permission stay separate from public source availability.

## Contributing and security

Use synthetic fixtures, scoped changes and independently reproducible tests. Please read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md) before filing issues; do not post complete receipts, credentials, or private task data.

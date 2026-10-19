# chatgptpro-web-agent

[简体中文](README.zh-CN.md) · [Online workflow](docs/ONLINE_WORKFLOW.md) · [Offline quick start](docs/QUICKSTART.md) · [Research boundaries](docs/RESEARCH_USE.md) · [License](LICENSE) · [Security](SECURITY.md)

**A Windows research helper that lets a host agent hand a bounded analysis to ordinary web ChatGPT, wait for it, and collect the reply locally.**

## Why we built it

An agent working in a local repository sometimes needs a second, substantial analysis: a code review, an architecture comparison, or a synthesis of material. We wanted a repeatable way to give that work to a web ChatGPT conversation, while the local agent keeps responsibility for context, local tools and the final decision. A useful handoff needs more than pasting a prompt: it must identify the task, avoid accidental duplicate sends, wait within a deadline, recover an interrupted session and bring the right answer back.

Our initial hypothesis was that moving suitable work out of the main agent **might** reduce its token or subscription usage. That remains an open question, not a project result or a promised benefit. Packaging material, operating the browser, waiting and reviewing the answer also have costs; total usage may stay the same or increase. The current goal is to make task sharing usable and inspectable so that its value can be evaluated on real workloads.

## What is included

- An optional CUA browser-host adapter for observed model/effort checks, one send, completion detection and response collection.
- A local Python ledger with immutable task manifests, original receipts, single-send claims and recovery checks.
- A direct `browser_response` path for self-contained analysis, plus the separate original Git-result verification path.
- A bounded, hash-checked result reader for the main agent, synthetic fixtures, offline regressions and Windows CI.

This is an independent noncommercial research preview, publicly available under PolyForm Noncommercial 1.0.0. It has fresh Git history and an explicit file allowlist. It is not an official OpenAI product or endorsed integration, and no formal versioned Release has been published.

## How to start

First run the credential-free offline demo from this repository's root:

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

Requirements: Windows, Python 3.12, Git 2.32+. No credential, session cookie, account login or model API key is required to run these examples. The permission preview always reports `send_authorized=false`; it cannot create a send entitlement.

For an actual handoff, the host additionally needs Node.js 22+, its documented CUA browser API and a logged-in ordinary Chat page. In that host's JavaScript browser runtime, use `observeNewChat` → `prepareOnline` → `sendOnce` → `waitAndCollect` → `readCollected`. The [online guide](docs/ONLINE_WORKFLOW.md) provides the executable example, explicit installation/evidence paths and recovery steps. A terminal alone cannot drive the browser, and putting local file paths in a prompt does not upload their contents.

## Online integration scope

On 2026-10-19 a synthetic read-only review completed in the Codex in-app browser with visible labels GPT-6 / Pro. Its 325-character rendered reply was collected; original-receipt recovery retained one send attempt and the same reply hash. This is local acceptance of that host and Chinese ordinary-Chat UI profile. Other hosts, locales and project-bound starting pages require separate validation. Browser-response delivery does not need a GitHub writer. The original Git verification path remains separate. There is no universal browser driver, hosted-model API client or credential custodian. Token and subscription savings are unknown.

## Distribution and rights

The [PolyForm Noncommercial License 1.0.0](LICENSE) is reproduced without alteration and covers only interests the licensor actually controls. It permits certain noncommercial use, modification and redistribution subject to its conditions; it is **not** an OSI-approved open-source license. It does not grant permissions for third-party code or hosted services, and it does not mean third-party rights have been cleared. [Scope notice](docs/legal/LICENSE_SCOPE.md). The experimental *not-for-production* statement is a support recommendation, not an unannounced change to the license. This preview is not production-ready.

**No formal versioned Release has been published.** Offline regressions and local online acceptance are separate evidence. Native Windows CI on this repository's own exact commit is the engineering check for that snapshot; historical repository CI results cannot certify it. Component-level source rights, attribution, and brand permission stay separate from public source availability.

## Contributing and security

Use synthetic fixtures, scoped changes and independently reproducible tests. Please read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md) before filing issues; do not post complete receipts, credentials, or private task data.

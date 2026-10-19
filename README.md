# chatgptpro-web-agent

[简体中文](README.zh-CN.md) · [Offline quick start](docs/QUICKSTART.md) · [Research boundaries](docs/RESEARCH_USE.md) · [License](LICENSE) · [Security](SECURITY.md)

**Windows offline research bridge for agent task handoffs: at-most-once local send claims, original receipts, and Git result checks—with synthetic data only. Not a live ChatGPT client or official OpenAI product.**

This repository is a publicly available independent noncommercial research preview under PolyForm Noncommercial 1.0.0. `cherwinyu/chatgptpro-web-agent` is a fresh-history, allowlisted research subset, not a fork or clone of the prior internal engineering workspace. The repository name is not an OpenAI product name. It is not affiliated with or endorsed by OpenAI. No formal versioned Release has been published. Public source availability does not imply a verified live ChatGPT integration, production readiness, OpenAI endorsement, or permission to use third-party services.

## What can be tested

A Windows-oriented reference implementation for bounded agent task handoffs, local at-most-once send entitlements, original task receipts, and Git result checks. The supported first-run flow is **offline only** and uses disposable synthetic data.

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

Requirements: Windows, Python 3.12, Git 2.32+. No credential, session cookie, account login or model API key is required to run these examples. The permission preview always reports `send_authorized=false`; it cannot create a send entitlement.

## Not an online integration claim

This source preserves parts of the host-integration research interface, but **does not ship a universal browser driver, a hosted-model client, GitHub writer, or a certified live ChatGPT automation**. A functioning offline test does not prove account/service authorization, UI stability, model tier, cost, or end-to-end delivery. This preview does not include a verified online ChatGPT send. Avoid using it with real credentials or proprietary repositories without independent authorization.

## Distribution and rights

The [PolyForm Noncommercial License 1.0.0](LICENSE) is reproduced without alteration and covers only interests the licensor actually controls. It permits certain noncommercial use, modification and redistribution subject to its conditions; it is **not** an OSI-approved open-source license. It does not grant permissions for third-party code or hosted services, and it does not mean third-party rights have been cleared. [Scope notice](docs/legal/LICENSE_SCOPE.md). The experimental *not-for-production* statement is a support recommendation, not an unannounced change to the license. This preview is not production-ready.

**No formal versioned Release has been published.** Verified behavior on this independent clean-root snapshot is the Windows and Python 3.12 offline research demo. Native Windows CI on this repository's own exact commit is the engineering check for that snapshot; historical repository CI results cannot certify it. Component-level source rights, attribution, and brand permission stay separate from public source availability.

## Contributing and security

Use synthetic fixtures, scoped changes and independently reproducible tests. Please read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md) before filing issues; do not post complete receipts, credentials, or private task data.

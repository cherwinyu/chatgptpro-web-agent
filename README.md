# chatgptpro-web-bridge — noncommercial research preview

[简体中文](README.zh-CN.md) · [Offline quick start](docs/QUICKSTART.md) · [Research boundaries](docs/RESEARCH_USE.md) · [License](LICENSE) · [Security](SECURITY.md)

**Private release preview — not approved for public release.** This repository is a fresh-history, allowlisted research subset, not a fork or clone of the private engineering workspace. It is not affiliated with or endorsed by OpenAI.

## What can be tested

A Windows-oriented reference implementation for bounded agent task handoffs, local at-most-once send entitlements, original task receipts, and Git result checks. The supported first-run flow is **offline only** and uses disposable synthetic data.

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

Requirements: Windows, Python 3.12, Git 2.32+. No credential, session cookie, account login or model API key is required to run these examples. The permission preview always reports `send_authorized=false`; it cannot create a send entitlement.

## Not an online integration claim

This source preserves parts of the host-integration research interface, but **does not ship a universal browser driver, a hosted-model client, GitHub writer, or a certified live ChatGPT automation**. A functioning offline test does not prove account/service authorization, UI stability, model tier, cost or end-to-end delivery. Avoid using it with real credentials or proprietary repositories without independent authorization.

## Distribution and rights

The [PolyForm Noncommercial License 1.0.0](LICENSE) is reproduced without alteration and covers only interests the licensor actually controls. It permits certain noncommercial use, modification and redistribution subject to its conditions; it is **not** an OSI-approved open-source license. It does not grant permissions for third-party code or hosted services. [Scope notice](docs/legal/LICENSE_SCOPE.md). The experimental *not-for-production* statement is a support recommendation, not an unannounced change to the license.

**Public visibility remains blocked.** This independent clean-root snapshot must pass native Windows CI on its own exact commit before any distribution decision. Component-level source rights, attribution, and final publication approval remain separate mandatory gates. Historical repository CI results cannot certify this snapshot.

## Contributing and security

Use synthetic fixtures, scoped changes and independently reproducible tests. Please read [CONTRIBUTING](CONTRIBUTING.md) and [SECURITY](SECURITY.md) before filing public issues; do not post complete receipts or private task data.

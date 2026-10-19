# Contributing to the noncommercial research preview

[中文说明](README.zh-CN.md) · [Security](SECURITY.md) · [License](LICENSE)

This is a **publicly available noncommercial research preview** of `cherwinyu/chatgptpro-web-agent` under PolyForm Noncommercial 1.0.0. It is not an OSI-approved open-source project, no formal versioned Release has been published, and it is not a production service. The license text is source-available PolyForm Noncommercial 1.0.0. PolyForm Noncommercial is not OSI Open Source. The permitted license scope is limited to rights the licensor controls. Third-party rights are not cleared by accepting a change. Any proposed third-party snippet must include provenance and all required notices, or it must stay out of the tree.

Use new branches and synthetic fixtures. For Windows (Python 3.12, Git 2.32+), run the same offline checks as `.github/workflows/offline.yml`:

```powershell
py -3.12 scripts/publication_audit.py
py -3.12 -m unittest discover -s tests/publication -v
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 tests/pro_bridge/test_faults.py
py -3.12 tests/pro_bridge/test_security.py
py -3.12 -m unittest discover -s tests/audit -v
node --test tests/audit/browser_host.test.mjs tests/audit/online_host.test.mjs
```

The CI workflow must not contact a live model/page or use real receipts, credentials or repositories. Avoid posting private task logs, browser URLs or personal paths in issues. Do not add real credentials or live ChatGPT sessions as fixtures.

`readStandaloneChat` and `readChatGPTPage` are passed to `tab.playwright.evaluate`. The host serializes that function into an isolated page realm. Declare every helper inside the function. A module function, a Node API, or a saved model label is not there. Test that boundary by running `Function.toString()` in a fresh `vm` context with a synthetic DOM. Calling the function in the host module scope does not count. Those tests are offline. They are not CUA or ChatGPT acceptance.

Report reproducible behavior, environment versions and the exact commit SHA of this repository, not historical internal commits. Preserve at-most-once semantics: an unknown submission cannot authorize retries, a new task or deadline extension.

Proposals that change hosted-service interaction, brand use, or a formal Release must go through separate scope and authorization review. A passing CI run is not a versioned Release, rights clearance, or brand permission. Public source availability is not a substitute for that review.

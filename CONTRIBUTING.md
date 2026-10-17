# Contributing to the noncommercial research preview

[中文说明](README.zh-CN.md) · [Security](SECURITY.md) · [License](LICENSE)

This is a **source-available noncommercial research preview**, not an OSI-approved open-source project or a production service. The permitted license scope is limited to rights the licensor controls, and any proposed third-party snippet must include provenance and all required notices.

Use new branches and synthetic fixtures. For Windows (Python 3.12, Git 2.32+), run the same offline checks as `.github/workflows/offline.yml`:

```powershell
py -3.12 scripts/publication_audit.py
py -3.12 -m unittest discover -s tests/publication -v
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 tests/pro_bridge/test_faults.py
py -3.12 tests/pro_bridge/test_security.py
py -3.12 -m unittest discover -s tests/audit -v
```

The CI workflow must not contact a live model/page or use real receipts, credentials or repositories. Avoid posting private task logs, browser URLs or personal paths in issues.

Report reproducible behavior, environment versions and the exact new-repository commit SHA, not historical internal commits. Preserve at-most-once semantics: an unknown submission cannot authorize retries, a new task or deadline extension.

Proposals changing hosted-service interaction or public release must go through separate scope and authorization review. A passing CI run is never publication approval.

# Contributing to the noncommercial research preview

[中文说明](README.zh-CN.md) · [Security](SECURITY.md) · [License](LICENSE)

This is a **noncommercial research preview**, not an OSI-approved open-source project, not an approved public release, and not a production service. The license text is source-available PolyForm Noncommercial 1.0.0 and is not an OSI-approved open-source license. The permitted license scope is limited to rights the licensor controls. Third-party rights are not cleared by accepting a change. Any proposed third-party snippet must include provenance and all required notices, or it must stay out of the tree.

Use new branches and synthetic fixtures. For Windows (Python 3.12, Git 2.32+), run the same offline checks as `.github/workflows/offline.yml`:

```powershell
py -3.12 scripts/publication_audit.py
py -3.12 -m unittest discover -s tests/publication -v
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 tests/pro_bridge/test_faults.py
py -3.12 tests/pro_bridge/test_security.py
py -3.12 -m unittest discover -s tests/audit -v
```

The CI workflow must not contact a live model/page or use real receipts, credentials or repositories. Avoid posting private task logs, browser URLs or personal paths in issues. Do not add real credentials or live ChatGPT sessions as fixtures.

Report reproducible behavior, environment versions and the exact commit SHA of this repository, not historical internal commits. Preserve at-most-once semantics: an unknown submission cannot authorize retries, a new task or deadline extension.

Proposals that change hosted-service interaction, brand use, or publication status must go through separate scope and authorization review. A passing CI run is never publication approval, rights clearance, or brand permission. Repository visibility is not a substitute for that review.

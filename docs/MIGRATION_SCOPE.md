# Research preview scope

[简体中文](MIGRATION_SCOPE.zh-CN.md) · [Home](../README.md)

This independently initialized, allowlisted repository (`chatgptpro-web-agent`) contains local task-state and receipt code, generic launchers, synthetic examples, offline security tests, bilingual guides, legal text, Windows CI, and an optional CUA host adapter for online offloading. The credential-free demo remains Windows with Python 3.12 and is offline.

The [online workflow](ONLINE_WORKFLOW.md) records two separate browser notes. The 2026-10-19 note is historical. Its runtime digest does not match later trees, and `certifies_current_tree` stays false. The 2026-10-20 note is the owner's single-host synthetic acceptance of the commit and runtime digest named in `capability-manifest.json`. [Integration](INTEGRATION.md) is the caller contract for `offloadTask` and `recoverTask`. Neither note accepts other hosts, a consumer project, account or service authorization, other UI profiles, credentials, a remote GitHub writer, or token savings. A consumer project needs its own synthetic online acceptance. This preview is not production-ready and is not endorsed by OpenAI.

This repository's source is publicly available. This is not a full production release or a feature-for-feature claim, and no formal versioned Release has been published. Local online evidence is separate from offline regression results and exact-head Windows CI. Public source availability does not by itself establish source and notice rights, brand or platform permission, or a right to use third-party services. See [notices](../THIRD_PARTY_NOTICES.md).

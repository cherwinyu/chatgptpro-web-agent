# Security Policy / 安全报告说明

This is a noncommercial offline research preview, **not suitable for production**. No guarantees of browser-session security, remote delivery correctness or third-party service permission are made.

Do not post credentials, real ledger data, private prompts, full chat URLs or proprietary repositories in public issues. For security-sensitive findings, use GitHub's private vulnerability reporting option **if the repository owner has enabled it**. If it is unavailable, do not disclose sensitive details in a public issue; ask the maintainer for a private channel before sharing evidence. No response SLA or private channel is guaranteed.

The project does not authenticate third-party browser sessions, and a local at-most-once claim is not a distributed server exactly-once guarantee. Only use repositories and accounts you are authorized to access. Research/noncommercial descriptions do not override third-party terms.

## Privacy / 隐私

Offline commands use disposable synthetic fixtures. Do not point `PRO_BRIDGE_LEDGER` at a real ledger, and do not commit `.env`, local ledgers, browser profiles, or home-directory paths. `scripts/publication_audit.py` rejects blocked internal paths, personal mailbox patterns, home-directory paths, and common token shapes; a pass is a rule check, not a promise that every personal fact has been found.

中文：本项目是非商业离线研究预览版，不适用于生产。不要在公开 Issue 提交账号、Cookie、个人路径、真实回执、会话 URL 或私人源码。若 GitHub 私密漏洞报告已启用则使用该入口；否则先请维护者提供私密沟通方式。不要认为研究用途可豁免第三方条款。

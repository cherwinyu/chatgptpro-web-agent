# Security Policy / 安全报告说明

This is a noncommercial offline research preview, **not suitable for production**, and not an approved public release. No guarantees of browser-session security, remote delivery correctness or third-party service permission are made. There is no verified online ChatGPT send, and this policy does not grant OpenAI or other provider authorization.

Do not post credentials, real ledger data, private prompts, full chat URLs or proprietary repositories in issues. For security-sensitive findings, use GitHub's private vulnerability reporting option **if the repository owner has enabled it**. If it is unavailable, do not disclose sensitive details in an issue; ask the maintainer for a private channel before sharing evidence. No response SLA or private channel is guaranteed. Repository visibility does not change these handling rules.

The project does not authenticate third-party browser sessions, and a local at-most-once claim is not a distributed server exactly-once guarantee. Only use repositories and accounts you are authorized to access. Research/noncommercial descriptions do not override third-party terms.

## Privacy / 隐私

Offline commands use disposable synthetic fixtures. Do not point `PRO_BRIDGE_LEDGER` at a real ledger, and do not commit `.env`, local ledgers, browser profiles, or home-directory paths. `scripts/publication_audit.py` rejects blocked internal paths, personal mailbox patterns, home-directory paths, and common token shapes; a pass is a rule check, not a promise that every personal fact has been found. A passing audit does not set release approval, third-party rights clearance, or brand permission.

中文：本项目是非商业离线研究预览版，不适用于生产，也不是已批准的公开发布。不要在 Issue 提交账号、Cookie、个人路径、真实回执、会话 URL 或私人源码。若 GitHub 私密漏洞报告已启用则使用该入口；否则先请维护者提供私密沟通方式。不要认为研究用途或仓库可见性可豁免第三方条款，也不要把离线检查当成线上发送已验证或权利已核清。

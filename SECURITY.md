# Security Policy / 安全报告说明

This repository (`cherwinyu/chatgptpro-web-agent`) is publicly available as an independent noncommercial offline research preview under PolyForm Noncommercial 1.0.0 and is **not suitable for production**. No formal versioned Release has been published. No guarantees of browser-session security, remote delivery correctness or third-party service permission are made. There is no verified online ChatGPT send, and this policy does not grant OpenAI or other provider authorization.

Do not post credentials, real ledger data, private prompts, full chat URLs or proprietary repositories in issues. For security-sensitive findings, use GitHub private vulnerability reporting when enabled. If that option is not yet available, do not disclose sensitive details in an issue; ask the maintainer for a private channel before sharing evidence. No response SLA or private channel is guaranteed. Public source availability does not change these handling rules, does not verify an online ChatGPT send, and does not make this preview production-ready.

The project does not authenticate third-party browser sessions, and a local at-most-once claim is not a distributed server exactly-once guarantee. Only use repositories and accounts you are authorized to access. Research/noncommercial descriptions do not override third-party terms.

## Privacy / 隐私

Offline commands use disposable synthetic fixtures. Do not point `PRO_BRIDGE_LEDGER` at a real ledger, and do not commit `.env`, local ledgers, browser profiles, or home-directory paths. `scripts/publication_audit.py` rejects blocked internal paths, personal mailbox patterns, home-directory paths, and common token shapes; a pass is a rule check, not a promise that every personal fact has been found. A passing audit does not set release approval, third-party rights clearance, or brand permission.

中文：本仓库（`cherwinyu/chatgptpro-web-agent`）已公开提供采用 PolyForm Noncommercial 1.0.0 许可的独立非商业离线研究源码，不适用于生产。目前尚未发布正式版本 Release。不要在 Issue 提交账号、Cookie、个人路径、真实回执、会话 URL 或私人源码。启用后请使用 GitHub 私密漏洞报告；在该入口尚未可用时，不要在 Issue 中披露敏感细节，先请维护者提供私密沟通方式。源码公开不改变这些处理规则，也不代表在线 ChatGPT 发送已验证、适用于生产环境或第三方权利已核清。

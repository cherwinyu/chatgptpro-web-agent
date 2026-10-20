# 研究预览范围

[English](MIGRATION_SCOPE.md) · [首页](../README.zh-CN.md)

本仓库（`chatgptpro-web-agent`）采用独立新建的 Git 历史和严格文件白名单，包含本地任务状态及回执、通用进程入口、合成协议样例、安全回归测试、中英文说明、法律文本、Windows CI 和可选的 CUA 在线宿主适配器。无需凭据的入门演示仍是 Windows 与 Python 3.12 的离线流程。

[在线流程](ONLINE_WORKFLOW.md)记录了两条分开的浏览器说明。2026-10-19 是历史观察，其运行时摘要与后来的源码树不一致，`certifies_current_tree` 保持为 false。2026-10-20 是所有者对 `capability-manifest.json` 中指名的提交和运行时摘要所做的单宿主合成验收。主代理的调用约定见[接入说明](INTEGRATION.zh-CN.md)中的 `offloadTask` 和 `recoverTask`。两条记录都不验收其他浏览器、消费方项目、账号或服务授权、其他页面结构、凭据保管、GitHub 远端写入或 token 节省比例。消费方项目需要自己做一次合成在线验收。本预览不表示可用于生产，也不是 OpenAI 的背书。

本仓库源码已公开。这不是完整的生产发布，也不保证与其他工程完全同功能，目前尚未发布正式版本 Release。本地在线证据与离线回归及精确提交上的 Windows CI 分别记录。源码公开本身并不确立组件级代码来源、第三方许可及通知、品牌或平台许可，也不授予第三方服务使用授权。详见[第三方通知](../THIRD_PARTY_NOTICES.md)。

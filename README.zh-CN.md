# chatgptpro-web-agent

[English](README.md) · [接入主代理](docs/INTEGRATION.zh-CN.md) · [在线使用](docs/ONLINE_WORKFLOW.md) · [快速开始](docs/QUICKSTART.zh-CN.md) · [研究与使用边界](docs/RESEARCH_USE.zh-CN.md) · [许可证](LICENSE) · [安全说明](SECURITY.md)

**让本地编码代理把一个有边界、自包含的任务交给已登录的普通 ChatGPT 网页会话，只发送一次，再把回复取回本地。** 这是面向 Windows 的非官方、非商业研究预览。

## 它做什么

在本地工作的主代理（例如 Codex 或 Cursor 里的编码代理）先把一个有边界、自包含的任务打包，再交给已登录的普通 ChatGPT 网页会话。工具只发送一次，等待完成；中途被打断时按原回执恢复；回复保存在本地，也可以解析成紧凑的结构化结果。

1. **打包**：`adapters/generic/task_pack.mjs` 把 `pack.json` 打包成一个自包含的任务文件。打包有字节预算，超出就拒绝，不会截断。
2. **只发送一次**：`adapters/generic/online_host.mjs` 的 `offloadTask` 核对页面上可见的模型和强度，写入持久的单次发送资格，点击一次发送，等待后返回限长摘录。
3. **需要时恢复**：`recoverTask` 重新载入同一张回执，继续等待或读取已保存的回复。它从不点击发送。
4. **解析**：`adapters/generic/answer_result.mjs` 按约定读取回复结尾的 JSON 块（也能处理页面渲染后的文本形态），得到结论、摘要和发现项。`review_approved` 恒为 false。

网页读不到你的仓库。它需要的材料必须全部打包进任务文件；在提示词里写本地文件路径，只是一段文字，不会上传文件内容。

## 适合做什么

适合材料能完整放进任务文件的工作：

- 代码审查或某个 diff 的第二意见；
- 比较几种架构方案；
- 对提供的资料做综合分析。

不适合：

- 需要读取整个仓库的任务；
- 需要写 GitHub 的任务（回复只是数据，不是 Git 改动）；
- 无人值守或生产环境运行。

## 为什么做

目的是让“把这件事交给网页 ChatGPT”变得可重复、可检查、可恢复，并且不会意外重复发送。主代理继续掌握上下文、本地工具和最终决定权。最初的设想是，分担合适的工作**也许**能减少主代理的 token 或订阅额度消耗。这仍是有待评估的问题，不是项目成果。打包、操作浏览器、等待和复核都有成本，总消耗可能不变，也可能增加。

## 当前状态

当前预发布版本是 [`v0.1.0-rc.6`](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6)。源码已公开提供，但它不是正式稳定版 Release，也不是 OpenAI 官方产品或官方集成。

在线证据记录在 `capability-manifest.json` 和[已验收和未验收](docs/INTEGRATION.zh-CN.md#已验收和未验收)中：

- **2026-10-19**：Codex 内置浏览器里的一次历史运行。它的运行时摘要与后来的源码树不一致，因此不能证明当前这份代码。
- **2026-10-20**：所有者在 Windows Codex 内置浏览器、中文普通 Chat 上做的单宿主合成验收（`synthetic_online_acceptance`），只覆盖那里记录的提交和运行时摘要。
- **2026-10-25**：在 Cursor IDE 浏览器、中文普通 Chat、非 Pro 档上，对 Cursor 页面通道示例做的单宿主合成观察（`cursor_page_channel_observation`），不是正式验收。

当前运行时摘要没有正式在线验收。消费方项目需要自己做一次合成在线验收。Windows CI 只是离线检查。

## 下载与开始

只需从 [`v0.1.0-rc.6` Release 页面](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6)下载一个 ZIP。

| 目的 | 下载 | 解压后的目录 |
| --- | --- | --- |
| 离线演示，或给宿主安装运行时 | 精简运行时：`chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim.zip` | `chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim` |
| 跑测试、修改、研究，或试用 Cursor 页面通道示例 | 源码：`chatgptpro-web-agent-v0.1.0-rc.6-source.zip` | `chatgptpro-web-agent-v0.1.0-rc.6-source` |

精简运行时包含运行时代码（含在线分担）、示例 schema、随包的用户文档（不含迁移说明）和 `release-manifest.json`，不含 `tests/`、`scripts/`、`examples/` 和 CI。源码包是按 `PUBLIC_FILELIST.txt` 收录的完整公开树，含测试、脚本和示例；它没有发行 manifest，所以不适用 Manifest Pin。Cursor 页面通道示例（`examples/cursor_page_channel/`）只在源码包里。

1. 解压前用 `Get-FileHash -Algorithm SHA256` 计算 ZIP 的哈希，与 Release 页面上的 SHA-256 对照。以 Release 页面为准；ZIP 内部的哈希不是信任根。
2. 精简运行时还要从 Release 页面复制 Manifest Pin，在解压目录里运行 `py -3.12 adapters/generic/run_bridge.py identify --trusted-manifest-sha256 <pin>`，要求 `provenance_status` 为 `VERIFIED`。
3. 运行无需凭据的离线演示（Windows、Python 3.12、Git 2.32+）：

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang zh-CN --json
```

这两条命令都保持离线（`network_attempted` 为 false，`web_send_count` 为 0），权限预览始终报告 `send_authorized` 为 false。离线演示成功，不代表在线发送已在任何宿主上可用。完整命令见[快速开始](docs/QUICKSTART.zh-CN.md)。

要调用在线分担，宿主还需要 Node.js 22+、提供 CUA 浏览器 API 的宿主 JavaScript 运行环境，以及已登录的普通 Chat 页面。`offloadTask` 和 `recoverTask` 的调用约定见[接入说明](docs/INTEGRATION.zh-CN.md)，选择器和失败行为见[在线使用](docs/ONLINE_WORKFLOW.md)。单独一个终端无法驱动浏览器。

## 包含的组件

- `tools/pro_bridge/` 下的本地 Python 账本与回执：不可变任务清单、原始回执、单次发送资格和恢复检查，由 `adapters/generic/run_bridge.py` 启动。
- `adapters/generic/online_host.mjs`：`offloadTask` 与 `recoverTask`。
- `adapters/generic/local_transport.mjs`：账本传输层，含带哈希校验的限长读取 `readCollected`。
- `adapters/generic/browser_host.mjs`：可选的 CUA 浏览器宿主适配器，负责观察模型和强度、发送一次、识别完成并回收正文。
- `adapters/generic/task_pack.mjs`：带预算的任务文件打包。
- `adapters/generic/answer_result.mjs`：解析回复结尾的 JSON 答案块。
- 独立的 Git 结果验证路径（`tools/pro_bridge/cli.py verify`），不会把聊天回复升级为批准。
- `examples/cursor_page_channel/`：实验性的 Cursor 页面通道示例，只在源码包中。

## 版本历史

以下都是预发布版本，没有一个是正式稳定版 Release。

- `v0.1.0-rc.6`：rc.5 加上文档更新，运行时代码不变。
- `v0.1.0-rc.5`：新增 `task_pack.mjs`、`answer_result.mjs` 和实验性的 Cursor 页面通道示例（只在源码包中）。
- `v0.1.0-rc.4`：首次包含 PR #8 带来的普通 ChatGPT 网页任务分担：`online_host.mjs` 的 `offloadTask` / `recoverTask`，以及 `local_transport.mjs` 的 `readCollected`。
- `v0.1.0-rc.3`：离线研究预览，不包含这次在线分担。

## 边界、许可与权利

- 非官方、非商业。[PolyForm Noncommercial License 1.0.0](LICENSE) 原文未作修改。它**不是** OSI 认可的开源许可证，只适用于授权者实际有权许可的内容，不授予第三方代码、托管服务或品牌的任何许可，也不表示第三方权利已经核清（[授权范围说明](docs/legal/LICENSE_SCOPE.md)）。
- 不适用于生产。“不用于生产环境”是项目的支持定位，不是额外的许可条件。
- 页面上可见的模型标签不是后端模型身份。
- token 或订阅额度节省只是待验证的假设，不是结果。
- 单次发送是本地 at-most-once，不是分布式 exactly-once。发送结果不确定（`SUBMISSION_UNKNOWN`）时绝不自动重发。
- 回复只是数据，不是 Git 交付批准。
- 本项目不是通用浏览器驱动，也不是模型 API 客户端，不保管凭据。只使用你有权使用的账号和服务，详见[研究与使用边界](docs/RESEARCH_USE.zh-CN.md)。

## 贡献与安全

请使用合成数据、范围明确的改动和可独立复现的测试。提 Issue 前请阅读[贡献说明](CONTRIBUTING.md)与[安全说明](SECURITY.md)，不要上传回执、凭据或私人任务数据。

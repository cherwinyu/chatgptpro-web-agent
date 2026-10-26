# chatgptpro-web-agent

[English](README.md) · [接入主代理](docs/INTEGRATION.zh-CN.md) · [在线使用](docs/ONLINE_WORKFLOW.md) · [快速开始](docs/QUICKSTART.zh-CN.md) · [研究与使用边界](docs/RESEARCH_USE.zh-CN.md) · [许可证](LICENSE) · [安全说明](SECURITY.md)

**让本地编码代理把一个有边界、自包含的任务交给已登录的普通 ChatGPT 网页会话，最多发送一次，再把回复取回本地。** 这是面向 Windows 的非官方、非商业研究预览，不是 OpenAI 产品。

- **适合做什么**：第二意见、方案比较，或材料能完整放进一个任务文件的分析。不适合需要整个仓库、需要写 GitHub 或无人值守的工作。
- **核心思路**：打包材料 → 只发送一次 → 凭原回执恢复 → 解析结构化结果。主代理保留自己的上下文和最终决定权。
- **完成度**：预发布版本 `v0.1.0-rc.6`。本地协议和适配器已经实现，并有离线测试和 Windows CI 覆盖。在线证据只来自单宿主合成运行，当前运行时摘要没有正式在线验收。是否能减少 token 或订阅额度消耗，尚未测量。

## 它做什么

在本地工作的主代理（例如 Codex 或 Cursor 里的编码代理）先把一个有边界、自包含的任务打包。工具把这个任务放进已登录的普通 ChatGPT 网页会话，只发送一次，等待完成，把回复保存到本地账本，也可以解析成紧凑的结构化结果。目的是让“把这件事交给网页 ChatGPT”变得可重复、可检查、可恢复，并且不会意外重复发送。

适合材料能完整放进任务文件的工作：

- 代码审查或某个 diff 的第二意见；
- 比较几种架构方案；
- 对提供的资料做综合分析。

不适合：

- 需要读取整个仓库的任务；
- 需要写 GitHub 的任务（回复只是数据，不是 Git 改动）；
- 无人值守或生产环境运行。

## 核心思路

1. **打包**：`adapters/generic/task_pack.mjs` 把 `pack.json`（选定的文件或文件片段，可选一段 `git diff`）打包成一个自包含的任务文件。打包有字节预算，超出就拒绝，不会截断。
2. **只发送一次**：`adapters/generic/online_host.mjs` 的 `offloadTask` 核对页面上可见的模型和强度，在本地账本里写入持久的单次发送资格，点击一次发送，等待后返回限长摘录。
3. **凭原回执恢复**：`recoverTask` 重新载入同一张回执，继续等待或读取已保存的回复。它从不点击发送。
4. **解析**：`adapters/generic/answer_result.mjs` 按约定读取回复结尾的 JSON 块（也能处理页面渲染后的文本形态），得到结论、摘要和发现项。

由这条流程引出三个设计取舍：

- **最多一次（at-most-once）。** 结果不确定的一次点击可能已经到达页面，再发一次就可能把同一个任务提交两次。点击之前，单次发送资格必须已经落盘。点击结果不确定（`SUBMISSION_UNKNOWN`）、超时、遇到登录墙或标签页丢失之后，工具绝不自动重发；下一步是凭原回执恢复，而不是另备一个替代任务。这是本地 at-most-once，不是分布式 exactly-once。
- **网页读不到你的仓库。** 网页会话只能看到任务文件里的文字。它需要的材料必须全部打包进这个文件；在提示词里写本地文件路径，只是一段文字。
- **主代理保留最终决定权。** 回复按数据保存。工具不执行、不应用回复，`review_approved` 恒为 false，解析出的 `verdict` 也不是 Git 交付批准。独立的 Git 验证路径（`tools/pro_bridge/cli.py verify`）拒绝把聊天回复当作 Git 批准。

调用约定和恢复状态见[接入说明](docs/INTEGRATION.zh-CN.md)；选择器和失败行为见[在线使用](docs/ONLINE_WORKFLOW.md)。

## 完成度

当前预发布版本是 [`v0.1.0-rc.6`](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6)。源码已公开提供，但它不是正式稳定版 Release，也不是 OpenAI 官方集成。

### 已实现，并有离线测试

| 部分 | 代码 | 离线测试 |
| --- | --- | --- |
| 本地账本与回执：不可变任务清单、原始回执、单次发送资格、恢复检查、Windows 账本持久化 | `tools/pro_bridge/`，由 `adapters/generic/run_bridge.py` 启动 | `tests/pro_bridge/test_faults.py`、`tests/pro_bridge/test_security.py`、`tests/audit/` |
| 在线分担与恢复（`offloadTask`、`recoverTask`）：从模型菜单观察模型和强度、单次发送资格、发送一次、等待和回收；以及含带哈希校验的限长读取 `readCollected` 的账本传输层 | `adapters/generic/online_host.mjs`、`adapters/generic/local_transport.mjs` | `tests/audit/online_host.test.mjs` |
| 只读的 CUA 浏览器适配器：绑定浏览器和标签页、观察页面、识别完成，只在显式请求时返回回复正文。它不发送，也不读取模型菜单 | `adapters/generic/browser_host.mjs` | `tests/audit/browser_host.test.mjs` |
| 带字节预算和 `git diff` 守卫的任务打包 | `adapters/generic/task_pack.mjs` | `tests/audit/task_pack.test.mjs` |
| 答案解析，含页面渲染后的文本形态 | `adapters/generic/answer_result.mjs` | `tests/audit/answer_result.test.mjs` |
| 独立的 Git 结果验证 | `tools/pro_bridge/cli.py verify` | `tests/pro_bridge/test_security.py`、`tests/audit/test_browser_delivery.py` |
| 精简运行时包与 `identify` 来源校验 | `tools/pro_bridge/` | `tests/pro_bridge/test_runtime_package.py` |
| 实验性的 Cursor 页面通道示例（只在源码包中） | `examples/cursor_page_channel/` | `tests/audit/cursor_page_channel.test.mjs` |

Windows CI 在每次 push 和 pull request 时运行这些离线检查。测试使用合成数据，不登录、不打开 ChatGPT，也不发送任何内容。Windows CI 只是离线检查。

### 单宿主合成在线证据

记录在 `capability-manifest.json` 和[已验收和未验收](docs/INTEGRATION.zh-CN.md#已验收和未验收)中。每次运行都是在一个宿主、已登录的中文普通 Chat 页面上执行合成只读任务。页面上可见的模型和强度标签只是页面文字，不是后端模型身份。

| 日期 | 宿主 | 运行内容 | 是否正式验收 | 是否覆盖当前运行时摘要 |
| --- | --- | --- | --- | --- |
| 2026-10-19 | Codex 桌面版内置浏览器；可见 GPT-6 / Pro | 一条合成审查，发送一次，回收到回复 | 否：历史观察（`online_offload`） | 否。它的运行时摘要与后来的源码树不一致，因此不能证明当前这份代码。 |
| 2026-10-20 | Windows Codex 内置浏览器；可见 GPT-6 / Pro | 一条合成审查，发送一次。第一次等待以 `HOST_TIMEOUT` 结束；对同一张回执调用 `recoverTask` 回收到回复，宿主运行时重启后再次回收成功，发送次数仍为 1 | 是：所有者的单宿主合成验收（`synthetic_online_acceptance`） | 否。只覆盖那里记录的提交和运行时摘要。 |
| 2026-10-25 | Windows Cursor IDE 浏览器；非 Pro 档 | Cursor 页面通道示例：三次尝试，两次真实发送。task1 因请求的标签与页面不一致，在发送前被拒绝，没有写回执。task2 和 task3 的任务文件由 `task_pack.mjs` 生成。task2 回收到回复，但渲染形态未能解析；修复解析器后离线重新解析成功。task3 回收到回复、解析成功，并凭同一张回执恢复，没有重发 | 否：仅为观察（`cursor_page_channel_observation`） | 否。最后一次运行（task3）记录的运行时摘要与当前源码树相同（由 manifest 记录推导），但 `formal_acceptance` 与 `certifies_current_tree` 均为 false，不构成验收。 |

当前运行时摘要没有正式在线验收。消费方项目需要自己做一次合成在线验收。

### 未验证或仍是开放问题

- **token 或订阅额度消耗。** 最初的设想是，分担合适的工作**也许**能减少主代理的消耗。这一点没有测量（`NOT_OBSERVABLE`）。打包、操作浏览器、等待和复核都有成本，总消耗可能不变，也可能增加。
- **后端模型身份和计费归属。** 页面标签不能说明实际由哪个后端模型回答，也不能说明由谁计费。
- **其他宿主、界面语言和消费方项目。** 随附的页面配置读取中文普通 Chat 的界面文字；其他界面语言需要自己的观察函数、发送按钮选择器，并单独验收。
- **大任务。** 页面填充路径的任务大小上限没有测量过，见[打包说明](docs/INTEGRATION.zh-CN.md#打包任务与读取回答)。
- **只有附件的回复。** 可以识别为页面已完成，但不算正文回收的验收。
- **完全不在验收范围内：** 无人值守或生产使用、保管凭据、把聊天回复变成 GitHub 写入。

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

要调用在线分担，宿主还需要 Node.js 22+、提供 CUA 浏览器 API 的宿主 JavaScript 运行环境，以及已登录的普通 Chat 页面。单独一个终端无法驱动浏览器。请从[接入说明](docs/INTEGRATION.zh-CN.md)开始。

## 版本历史

以下都是预发布版本，没有一个是正式稳定版 Release。

- `v0.1.0-rc.6`：rc.5 加上文档更新，运行时代码不变。
- `v0.1.0-rc.5`：新增 `task_pack.mjs`、`answer_result.mjs` 和实验性的 Cursor 页面通道示例（只在源码包中）。
- `v0.1.0-rc.4`：首次包含 PR #8 带来的普通 ChatGPT 网页任务分担：`online_host.mjs` 的 `offloadTask` / `recoverTask`，以及 `local_transport.mjs` 的 `readCollected`。
- `v0.1.0-rc.3`：离线研究预览，不包含这次在线分担。

## 边界、许可与权利

- 非官方、非商业。[PolyForm Noncommercial License 1.0.0](LICENSE) 原文未作修改。它**不是** OSI 认可的开源许可证，只适用于授权者实际有权许可的内容，不授予第三方代码、托管服务或品牌的任何许可，也不表示第三方权利已经核清（[授权范围说明](docs/legal/LICENSE_SCOPE.md)）。
- 不适用于生产。“不用于生产环境”是项目的支持定位，不是额外的许可条件。
- 本项目不是通用浏览器驱动，也不是模型 API 客户端，不保管凭据。只使用你有权使用的账号和服务，详见[研究与使用边界](docs/RESEARCH_USE.zh-CN.md)。

## 贡献与安全

请使用合成数据、范围明确的改动和可独立复现的测试。提 Issue 前请阅读[贡献说明](CONTRIBUTING.md)与[安全说明](SECURITY.md)，不要上传回执、凭据或私人任务数据。

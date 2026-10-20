# chatgptpro-web-agent

[English](README.md) · [接入主代理](docs/INTEGRATION.zh-CN.md) · [在线使用](docs/ONLINE_WORKFLOW.md) · [离线快速开始](docs/QUICKSTART.zh-CN.md) · [研究与使用边界](docs/RESEARCH_USE.zh-CN.md) · [许可证](LICENSE) · [安全说明](SECURITY.md)

**面向 Windows 的研究型任务分担助手：让宿主代理把有明确边界的分析交给普通网页 ChatGPT，等待完成，再把回复取回本地。**

## 下载哪一个

可下载的公开版本是预发布 [`v0.1.0-rc.3`](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.3)。它不是正式稳定版 Release，也不是 OpenAI 官方产品。该包是冻结在提交 `3051279` 的离线研究预览。更晚的 main，包括 PR #8 的在线分担，不在 rc.3 中。

| 目的 | 下载 |
| --- | --- |
| 第一次离线演示 | 精简运行时 |
| 完整源码和测试 | 源码 |
| 修改或研究 | 源码 |
| 核对发行完整性 | Release 页面上的 SHA-256 摘要 |

不必两个 ZIP 都下。精简运行时（`chatgptpro-web-agent-v0.1.0-rc.3-runtime-slim.zip`，SHA-256 `4b5d65eefd337fa45d5dd2389d7fe409b2be6ee5494b86b2ac3084201d5208fd`）是 42 个文件的离线包：桥接代码、示例 schema、许可证、声明和快速开始。它不含 `tests\`、`scripts\` 和 CI。源码包（`chatgptpro-web-agent-v0.1.0-rc.3-source.zip`，SHA-256 `0a3f8d0f94bdd3b9ebcb055dfee9a60237e584b38ccc70f5d1329c03c1f60495`）是完整的 58 个文件冻结树，含测试和 `scripts\publication_audit.py`。解压前用 `Get-FileHash -Algorithm SHA256` 计算 ZIP 本身。ZIP 内部的哈希不是信任根。离线演示需要 Windows、Python 3.12 和 Git 2.32 或更高版本。审计和单元测试只属于源码包。步骤见[离线快速开始](docs/QUICKSTART.zh-CN.md)。

## 为什么做这个项目

本地代理在维护仓库时，有时需要一份较完整的第二意见，例如代码审查、架构比较或材料综合。我们的出发点是：能否把这块工作交给网页 ChatGPT，同时让本地代理继续负责上下文、本地工具和最终判断？一次可靠交接不只是粘贴提示词，还需要识别任务、防止意外重复发送、限制等待时间、在中断后恢复，并把对应结果带回来。

最初我们还设想，分担合适的任务**也许**能减少主代理的 token 或订阅额度消耗。但这件事目前没有明确结论，只是待验证的初始想法，不能当作项目成果或收益承诺。打包材料、操作浏览器、等待和复核都有成本，总消耗可能不变，也可能增加。当前先把任务分担做成可实际使用、可检查的流程，再用真实工作负载评估价值。

## 仓库包含什么

- 可选的 CUA 浏览器宿主适配器：核对页面模型和努力档位、发送一次、识别完成并回收正文。
- 本地 Python 账本：不可变任务清单、原始回执、单次发送资格和恢复检查。
- 面向自包含分析材料的 `browser_response` 交付路径，以及独立保留的原 Git 结果验证路径。
- 供主代理使用的正文哈希检查和限长读取入口、合成样例、离线回归测试与 Windows CI。

本仓库已公开提供采用 PolyForm Noncommercial 1.0.0 许可的独立非商业研究源码，采用全新 Git 历史和明确文件白名单。本项目不是 OpenAI 官方产品，也不代表获得官方背书。GitHub Releases 上已有公开预发布版本 `v0.1.0-rc.3`，它不是正式稳定版 Release。

## 怎么开始使用

先运行无需凭据的离线演示。在精简运行时解压目录、源码解压目录或本仓库根目录：

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang zh-CN --json
```

要求 Windows、Python 3.12、Git 2.32+。权限预览始终不授予发送资格。这两条命令在精简运行时 ZIP 里。`scripts\publication_audit.py` 和 `tests\` 只在源码包里。离线演示保持 `network_attempted` 为 false、`web_send_count` 为 0，也不表示在线 ChatGPT 发送已在每台宿主上验证通过。

实际分担任务写在当前 main 里，不在预发布 `v0.1.0-rc.3` 中。宿主还需要 Node.js 22+、宿主提供的 CUA 浏览器 API，以及已登录的普通 Chat 页面。在宿主的 JavaScript 浏览器运行环境中调用一次 `offloadTask`。页面仍在生成时，对同一张回执再调用 `recoverTask` 继续等待；它不会再次点击发送。调用约定见[接入说明](docs/INTEGRATION.zh-CN.md)，选择器、失败行为和浏览器记录见[在线使用](docs/ONLINE_WORKFLOW.md)。单独运行终端不能驱动浏览器；在提示词里写本地源码路径也不会上传文件内容。

## 功能与使用限制

本节描述当前 main。已发布的预发布版本 `v0.1.0-rc.3`（冻结提交 `3051279`）不包含 PR #8 的在线交接、`offloadTask`，也不包含下面的验收记录。

2026-10-19 已在 Codex 内置浏览器用页面显示的 GPT-6 / Pro 完成一条合成只读审查任务，回收了 325 字渲染正文；按原回执恢复后，发送次数仍为 1，正文哈希一致。该记录是 `capability-manifest.json` 里的历史观察。它的运行时摘要与后来的源码树不一致，因此不能证明当前这份代码、其他宿主、其他界面语言或某个消费方项目已经验收。

2026-10-20 所有者在 Windows 的 Codex 内置浏览器、已登录的中文普通 Chat 上，验收了后一次合成只读审查。验收提交和运行时摘要写在 `capability-manifest.json` 的 `synthetic_online_acceptance`。发送前菜单显示 GPT-6 / Pro，关闭后的控件只显示 Pro。一次 `offloadTask` 点击了一次发送。第一次等待返回 `HOST_TIMEOUT`，页面仍在生成。对同一张回执调用 `recoverTask` 后回收了 296 字正文。宿主 JavaScript 运行时重启并重新绑定原标签页后，`recoverTask` 仍取回同一正文，账本里的发送次数仍为 1。`readCollected` 取 120 字时是截断前缀，完整正文仍在账本。这次文档只记录所有者交来的结果，并没有再跑那次浏览器流程。验收范围是一个宿主、一种界面语言、一条合成任务。它不验收其他宿主、其他界面语言或某个消费方项目。消费方项目需要自己做一次合成在线验收。Windows CI 仍是离线工程检查。直接回收正文不依赖网页端写 GitHub；原 Git 验证路径独立保留，不能把聊天回复升级成 Git 批准。本项目不提供通用浏览器驱动、托管模型 API 客户端或凭据保管。token 和订阅额度节省不是已经实现的结果。

正式法律文本为未修改的 [PolyForm Noncommercial License 1.0.0](LICENSE)，只适用于授权者实际有权许可的内容。它允许符合条款的非商业使用、修改与分发，但不是 OSI 开源许可证，也不自动授权未核实的第三方材料，更不表示第三方权利已经核清。“不用于生产环境”是项目支持定位，不是偷偷收紧 PolyForm 原条款。详见[授权范围说明](docs/legal/LICENSE_SCOPE.md)。

**公开预发布版本 `v0.1.0-rc.3` 不是正式稳定版 Release。** 已发布的预发布标签 `v0.1.0-rc.3` 是更早的快照，不包含这次在线分担。请从该 Release 下载源码 ZIP 或精简运行时 ZIP，并用 `Get-FileHash -Algorithm SHA256` 对照 Release 页面上的摘要（[快速开始](docs/QUICKSTART.zh-CN.md)里也列了同样的值）。rc.3 仍是冻结在 `3051279` 的离线研究预览。更晚的 main，包括 PR #8，不在这次预发布里。请固定你检出的提交，并按[接入说明](docs/INTEGRATION.zh-CN.md)核对已安装运行时。候选包名称不是标签、正式 Release，也不是在线验收。离线回归和本地在线验收分别记录。Windows CI 通过或离线演示成功，并不表示在线 ChatGPT 发送已在每台宿主上验证通过。本项目独立新根提交以自身精确版本上的原生 Windows CI 为工程检查；历史项目的测试记录不可沿用。组件级代码来源、署名与品牌许可仍独立于源码公开。

提 Issue 前请阅读[贡献说明](CONTRIBUTING.md)与[安全说明](SECURITY.md)，不要上传真实回执、凭据或私人源码。

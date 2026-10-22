# ProjectShoe 对接说明（Cursor 页面通道）

本文只说明消费方 ProjectShoe 如何接当前草稿里的宿主契约，以及以后升到新的预发布时要重新钉哪些字节。它不是第二份上游规范。发送、认领、恢复仍以 `docs/INTEGRATION.zh-CN.md` 和 `docs/ONLINE_WORKFLOW.md` 为准。

本仓库没有 ProjectShoe 的页面通道实现，也不包含它的锁文件。下面“如何使用”来自 2026-10-22 的现场记录，不是本次审查重新跑出来的浏览器结果。本次审查没有打开网页，也没有读取凭据。

基线仍是 Pre-release `v0.1.0-rc.4` 的提交 `88d7314ebd65a061e83c655588691ba5e764c901`。本说明所在分支在该提交之上增加宿主契约，不改写该标签，也不改写 `capability-manifest.json` 里的历史运行时摘要。

## 1. ProjectShoe 当时怎么用

现场记录里的成功路径是 Node 页面通道，文件中继只作后备。

页面通道（留在集成方，不在本仓库）：

- Node 在 `127.0.0.1` 的随机端口上起一次性服务。
- 令牌为 256 bit。令牌文件只应所有者可读写，并在第一个页面 session 绑定后作废。绑定前，本机其他进程若读到任务目录里的令牌，可以抢先占住该 session。这不是跨站脚本通道。
- CORS 只允许 `https://chatgpt.com`。
- 第一个页面 session 绑定之后拒绝后来的 session。请求还要绑定本次任务的请求哈希。
- 操作走白名单；未知操作拒绝。
- bootstrap 是一次性的：先 `Page.setBypassCSP` 且 `enabled` 为 true，再 `location.reload()`，然后用 `Runtime.evaluate` 执行 `page-script --bootstrap` 的一行输出。
- 任务结束：`Page.setBypassCSP` 且 `enabled` 为 false，再重新加载，核验页面的 `connect-src` 再次拦截对本机端口的请求，并确认宿主进程已经退出。CSP 绕过只覆盖这一次已授权标签页、只在任务期间有效。

文件中继是后备。慢速逐跳应答时，认领后的单次读取若仍用默认 10000 毫秒会超时，状态变成 `SUBMISSION_UNKNOWN`，并且不能重发。本仓库的 `createFileRelayActions` 现在会在发送点击前做粘贴读回；调用方仍须自己把中继的 `timeoutMs` 设成同一次任务的 `domReadCapMs`。只改 `offloadTask` 的 `domReadCapMs` 时，经 `asPlaywrightTab` 发出的 `evaluate`、`url`、`queryRole` 和发送点击仍使用中继构造时的 `timeoutMs`（默认 10000）。`fill` / `readComposer` 会带上编排层传入的上限。

编排入口要等宿主声明就绪再开始计时。ProjectShoe 当时在看到 `PAGE_CHANNEL_READY`、并且第一个合法页面 session 连上之前，不调用上游编排。连接等待是集成方的 `--page-connect-timeout-ms`（现场记录里的默认是 120000）。上游的 `whenHostReady` 本身没有超时：钩子若不返回，认领不会发生，收集时钟也不会提前启动；钩子若一直不返回，也不会变成 `HOST_TIMEOUT`。钩子应在超时时拒绝，并带上取消信号。省略钩子表示调用方认为已经就绪。

认领之后若执行器已是 `failed` 或 `stopped`，可以在同一文档里用新 token、新 session 重新 bootstrap。不要为了重新注入去重载页面：重载会改变已绑定的 session。`starting` 和 `running` 时拒绝第二个执行器。

`SUBMISSION_UNKNOWN` 或认领之后的 `HOST_TIMEOUT` 只做 `recoverTask`，不重新发送，不新建替换任务，也不延长 `deadline_at`。

## 2. 哪些接口归上游，哪些留在集成方

已经收进本仓库、集成方应改用这些契约，而不是再复制一份：

- 浏览器动作：`url`、`evaluate`、`click`、`press`、`fill`、`readComposer`、`queryRole`，以及 `timeoutMs`。参考实现是 `createMockActions`、`createFileRelayActions` 和 `asPlaywrightTab`。`CDP_OP_REFERENCE` 只是对照表，没有套接字。
- `domReadCapMs`：默认 10000，允许 1–120000。成功认领后写入账本 `host_limits`，并追加笔记 `dom_read_cap_ms=<值>`。任务正文和页面内容不能改写这个数；它只来自调用参数。
- 填写：粘贴，拒绝 `textContent`。发送点击前用 `innerText` 读回，把 `\r\n` 和 `\r` 规范成 `\n` 后逐字比较。不一致是 `COMPOSER_TEXT_MISMATCH`，发生在点击之前。认领之后这次失败仍是 `SUBMISSION_UNKNOWN`，不会再点一次。
- `whenHostReady`：`offloadTask`、`sendPrepared`、`recoverTask` 在计时的浏览器步骤之前等待它。
- 页面适配表 `chatgpt-ordinary-chat-zh` 版本 1，以及只读的 `listVisibleSelection`。模型显示名不是代码常量。
- 执行器状态说明：`adapters/generic/executor_state.mjs`。
- `budgetMs` 必须是 1–45000 的整数。省略时收集预算现在是 45000。`55000` 和 `120000` 会以 `BOUNDED_WAIT_REQUIRED` 拒绝。
- 授权标签页和任务期间 CSP 绕过的文字边界，见 `SECURITY.md`。

仍留在 ProjectShoe，本仓库不实现：

- `127.0.0.1` 页面通道、256 bit 令牌、CORS、首个 session 绑定、请求哈希绑定、一次性 bootstrap，以及真正执行 `Page.setBypassCSP`。
- `--page-connect-timeout-ms`。上游钩子不代替它。
- 把 `domReadCapMs` 同步到文件中继 `timeoutMs` 的那一层调用。
- 集成方自己的部署锁，以及锁文件所在仓库的 `.gitattributes`。本仓库的 `-text` 只覆盖本仓库里的 `LICENSE`、`docs/legal/LICENSE`、`*.lock` 和 `*.lock.json`。

## 3. 以后升到新的预发布时要重新钉什么

rc.4 的锁继续钉提交 `88d7314ebd65a061e83c655588691ba5e764c901`。不要改写那个标签，也不要把它的锁改成新树的摘要。

升到未来的 rc.5（或任何包含本分支的新提交）时，集成方要新做一把锁，而不是覆盖 rc.4 那把。现场记录里的锁名是 `web_agent_offload_88d7314.lock.json`，它不在本仓库。新锁至少要重新计算：

- `source_commit` / `upstream_commit`：新的上游提交，不是 `88d7314`。
- `runtime_source_digest`：`tools/pro_bridge` 与 `adapters/generic` 的原始字节摘要。本分支因为新增了适配器文件，工作区摘要已经不等于 rc.4，也不等于 `capability-manifest.json` 里 2026-10-20 那次验收的 `covers_only_runtime_digest`。历史摘要不要改成新树的值；`certifies_current_tree` 仍是 false。
- `manifest_sha256`：若发布清单的文件列表变了，按 `tools/pro_bridge/deployment_lock.py` 的现有校验重新生成。锁的 `line_endings` 仍须是 `committed_raw_bytes`。
- `host_adapters`：页面通道和文件中继这些集成方文件的路径与 SHA-256。上游契约变了之后，这些文件若跟着改，哈希要重算。

`LICENSE` 与 `docs/legal/LICENSE` 相对 rc.4 的 git 对象没有变化。除非许可证字节本身变了，否则不要为升版重写许可证哈希。

建议重跑，且都不要打开真实账号下的网页：

- `node --test tests/audit/browser_host.test.mjs tests/audit/online_host.test.mjs tests/audit/browser_actions.test.mjs`
- `python3 -B scripts/publication_audit.py`
- `python3 -B -m unittest tests.audit.test_browser_delivery tests.publication.test_preview_publication tests.pro_bridge.test_runtime_package`
- `python3 -B tests/pro_bridge/test_security.py`
- 用 `runtime_identity.inventory` 加 `mapping_digest`，对钉住的那一提交的 blob 重算 `runtime_source_digest`，再和锁里的值比较。验收提交 `4399582ddc500216cf4a6034f273946f2b79208d` 的历史摘要应仍然等于清单里的 `covers_only_runtime_digest`。
- 旧的六字段回执仍用 `tools/pro_bridge/receipts.py` 的 `load`。嵌套的 `receipt` 必须恰好是那六个字段；多出来的 `host_wait` 键在外层，不改变这六个字段。

## 4. 模型与强度

`requestedModel` 和 `requestedEffort` 必须与这一次菜单上的可见文字逐字相同。不一致时 `offloadTask` 抛出 `REQUESTED_SELECTION_NOT_VISIBLE`，不准备、不发送、不自动降级。调用前用 `listVisibleSelection` 读当前标签，再把读到的字符串填进去。不要把模型显示名写死在调用方。

所有者的使用策略：默认强度用「6 极高」；只有难任务才用「6 Pro」。这两个字符串都不是上游代码里的常量。2026-10-22 的现场记录里，那一次页面上「选择模型」菜单项的 innerText 是 `6 Pro`，更早写死的 `GPT-6` 在认领前被拒绝。本次审查没有打开页面，不能把「6 极高」或「6 Pro」说成当前页面上一定存在的文字。策略只决定优先选哪一个；真正传给 `requestedEffort` 的值必须等于当次 `listVisibleSelection` 看到的标签。

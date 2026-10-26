# 实验性 Cursor 页面通道

[English](README.md) · [调用方契约](../../docs/INTEGRATION.zh-CN.md)

这是供调用方通过 CDP 操作已授权 Cursor 浏览器标签页的纯文字脚本示例。
**在单个宿主上观察过（见「联机观察记录」），不是正式验收。** 离线测试不等于 CUA 或 ChatGPT 验收，不声称节省 token。

Node host 向现有 `online_host.mjs` 提供兼容的 `tab`。
模型/档位观察、持久认领、一次填充/发送及回收均由该模块负责。
`local_transport.mjs` 提供账本接口；`task_pack.mjs` 可选打包提示词；
`answer_result.mjs` 解析已收集回答。本示例没有独立任务体系，也不自行调用 CDP。

## 前提与显式输入

- Windows、Node.js 22+、可通过 `py` 调用的 Python 3.12、Git 2.32+。
- 包含上述四个适配器模块的可信 web-agent 检出。
- 调用方具备对选定且经用户授权的浏览器与标签页的 CDP 操作能力。
- 已登录的顶层普通 Chat 页面，使用中文普通 Chat UI profile。
  offload 要求新 Chat 且输入框为空。提前在 UI 中选好模型和努力档位，host 只核对。
- 证据目录严格位于 web-agent 检出之外，解析符号链接/junction 后也必须如此。
  显式 ledger 和 receipt 路径均位于证据目录内部。
  每个来源仓库使用独立 ledger，每个任务使用全新证据目录。
- 自包含且获准外发的文字材料。提示词里的文件路径不会自动上传文件。
  不放凭据、私有回执或未经授权的材料。

必须显式提供 repository、完整 source SHA、source ref、orchestration 标签、
browser/tab 标识和页面可见的 model/effort 标签。host 不从 Git 推导这些值，也不选择模型。
`--kind` 为 `review` 或 `analysis`；必须传 `--ui-profile chinese-ordinary-chat`，
其他 profile 不支持。传入的根路径用于选择可信适配器代码，不是沙箱。

## 完整操作步骤，包括 CSP 恢复

以下顺序必须在同一个已授权标签页执行。保留独立 CDP 连接，
即使 Node 进程失败，也能完成恢复。

1. 确认 browser/tab 身份和普通 Chat 页面，不能是项目内页面。
   恢复任务时依据原回执找到原标签页与会话。
2. 调用方执行 CDP `Page.setBypassCSP`，参数 `{"enabled":true}`。
3. 执行 CDP `Runtime.evaluate`，参数 `{"expression":"location.reload()"}`。
   等待重载完成，重新确认已授权标签页、页面、模型与努力档位。
4. 在 web-agent 检出内按下方命令启动 host。
   将全部尖括号占位符替换为本任务的显式值。
5. 读取 stdout 中 `status` 为 `PAGE_CHANNEL_READY` 的唯一 JSON 行。
   将其解码后的 `bootstrap` 字符串作为该标签页 CDP `Runtime.evaluate`
   的 `expression`。加载器从秘密的一次性 loopback 路径获取完整执行器；再次获取返回 410。
6. host 运行期间保持标签页打开且不变。不重复注入正在运行的代理，
   不重载文档、不编辑输入框、不切换模型/档位。
7. **调用方必须在 finally 路径中，无论成功或失败**，执行
   `Page.setBypassCSP`，参数 `{"enabled":false}`，再执行 `Runtime.evaluate`，
   参数 `{"expression":"location.reload()"}`。确认重载完成、host 已退出。
   重载会清除页面代理及其 marker。

```powershell
node examples/cursor_page_channel/cursor_page_host.mjs `
  --mode offload --web-agent-root '<checkout>' `
  --evidence-directory '<external-evidence>' --ledger '<external-evidence>/ledger' `
  --receipt '<external-evidence>/prepare.json' --task-file '<task-text-file>' `
  --repository '<owner/name>' --source-sha '<full-source-sha>' --source-ref '<ref>' `
  --orchestration '<task-label>' --browser-id '<browser-id>' --tab-id '<tab-id>' `
  --model '<visible-model>' --effort '<visible-effort>' `
  --ui-profile chinese-ordinary-chat --kind review --budget-ms 45000
```

使用打包时，将 `--task-file` 替换为 `--pack-spec '<pack-spec-file>'
--source-root '<source-checkout>'`，两种输入互斥。
采用现有[打包格式](../../docs/INTEGRATION.zh-CN.md)，`kind` 必须与 `--kind` 一致。
建连后 host 调用 `pack`，以独占创建方式在证据目录写入 `packed-task.txt`；
输出已存在则拒绝。不创建另一套任务配置，也不推导来源身份。
恢复模式不重新打包、不重新读取输入材料。

## 收集模式

默认 `--collection lossless`。发送与等待不变。收集时，host 先核对任务绑定与最终回复证据，
再点击该轮**整条回复**的复制按钮，从页面内截获的剪贴板数据读回原始 Markdown 并写入账本。
页面代理在页面内包装 `navigator.clipboard` 的 `write` / `writeText`，只截获 ChatGPT
复制按钮写出的数据，不读写系统剪贴板（`clipboardRestoration` 为 `not_applicable_cursor_relay`）。

复制按钮的识别条件：位于所有 assistant 内容单元（`[data-content-search-unit-key]`）之外，
`aria-label` 精确为 `复制` 或 `Copy`，且恰好一个。写作块（`chatgpt-writing-block`）
标题栏自带的复制按钮会被排除。

失败时（如 `LOSSLESS_COPY_CONTROL_AMBIGUOUS`）outcome 带 `losslessFailure`，`--follow-ms`
不再自动续跑，**不退回渲染文本**，也不重发；只能按原回执 recover。

`--collection rendered` 是早先的行为：收集渲染后的 `innerText`（标题丢 `##`，表格变制表符）。
只适合只关心末尾 JSON 结论的评审类任务；翻译、长文转换等需要原文的任务不要使用。

## 等待、停止与恢复

连接超时默认 120000 ms（`--connect-timeout-ms`，1–300000）。
操作超时默认 8000 ms（`--response-timeout-ms`，1–45000）。
浏览器分段等待默认 45000 ms（`--budget-ms`，1–45000）。
可选 `--follow-ms`（1–900000）从首次 offload/recover 调用开始累计计时；
仅当结果为 `HOST_TIMEOUT`、`doNotResend: true` 且没有 error 时，
才对同一 tab、transport 和原 receipt 调用 `recoverTask`。
每段预算不超过剩余时间。适配器账本 I/O、预检另有自身限制，
因此这不是严格的墙钟截止时间；原任务 deadline 永不延长。

- `SUBMISSION_UNKNOWN`、`TASK_DEADLINE`、`HOST_TIMEOUT`、中断或页面身份变化：
  停止发送，只允许回收原任务。不得新建替代任务、重试点击或延长截止时间。
- 模型/档位不符：汇报并停止，不静默切换或重新发送。
- `PREPARED_NOT_SENT`：recover 不发送。本 CLI 没有 send-prepared 模式。
- `parse_ok: false`：只汇报解析失败并复核已保存文本。
  不要求网页重新回答来修复格式。截断回答不参与解析。

恢复时使用原输入重复完整 CSP 流程，仅将 `--mode offload` 改为
`--mode recover`（等待限制可以调整）。保留原 evidence、receipt、ledger、
browser/tab 和标签。本 CLI 即使回收已收集任务也等待页面连接；
适配器直接调用 `recoverTask({run})` 可不依赖页面读取已存结果。
不要删除中断证据来绕过已有回执拒绝。

收集成功后 stdout 输出有长度限制的 `summarizeAnswer` 结果，
`review_approved` 恒为 false。全文保留在现有 ledger 内；
host 通过适配器最多读取 65536 字符。
退出码：0 表示收集且解析成功，2 表示解析或 host 失败，3 表示未收集结果。

## 安全边界与崩溃恢复

服务仅在 `127.0.0.1` 的系统随机端口监听，并复核绑定地址。
要求精确匹配 `PAGE_ORIGIN` 常量（`https://chatgpt.com`）、256-bit bearer token
和首个通过鉴权的轮询 session。预检核查路由、方法及请求头，OPTIONS 不绑定 session。
全部响应设置 `no-store`。canonical 请求散列、固定 op 白名单、单 pending 动作
和单次交付限制 relay 重放。结果 POST 丢失时代理停止，响应超时则通道停止，
两者都不会补发任务。业务 at-most-once 仍依赖现有 `sendOnce` 的持久 claim。

请求体及 `/next` JSON 各限制 1 MiB，序列化后的 `value` 限制 512 KiB。
超限失败，不截断。DOM 执行无法强制取消，超时动作仍可能在后台完成。
button 使用 pointer/mouse 事件；可编辑文字先 paste，再按需回退编辑命令，
最后对 LF 规范化后的顶层行节点逐字回读。不一致就停止发送。

host 不把 token 或 bootstrap 路径写入文件。
bootstrap 仅出现在 `PAGE_CHANNEL_READY`；该行、终端历史和外部 stdout 捕获
均应视作凭据，不要公开或持久保存。
`page-relay-*` 内的请求/响应文件含任务数据，应限制本地访问权限，
其中没有 host 插入的鉴权 token。

Origin 检查无法隔离允许来源内的恶意脚本。
本机进程可伪造 Origin，因此 token 保密是关键。
host 的 `evaluate` 会在页面执行可信宿主表达式；散列是完整性校验，
不是独立签名或沙箱。回复仅为不可信数据，不能执行其中指令或把 verdict 当作批准。
仅支持文字，不支持上传。无损复制只在单个宿主上观察过（见「联机观察记录」），不是正式验收。

host 在 `finally` 关闭 HTTP，在 ready 行及退出/失败时输出 `csp_cleanup_required`。
它无法更改 CDP 设置。**host 或调用方被杀时，CSP 绕过可能仍保持开启：**
必须人工对原标签页执行 `Page.setBypassCSP {"enabled":false}` 并重载，之后才能继续。
没有收到恢复提醒，不代表 CSP 已恢复。
failed/stopped 页面 marker 允许新的 run 接管，但不授权再次 offload 原任务。

离线测试命令为 `node --test tests/audit/cursor_page_channel.test.mjs`。
已在一个宿主上联机观察过（见下一节）：Cursor IDE 浏览器允许 `Page.setBypassCSP`，
bootstrap 注入与收集可用，recover 取回同一正文且没有重发。
在该宿主上，页面到 `127.0.0.1` 的 bootstrap 与 relay 请求成功。
其他宿主、其他界面语言、Pro 档、其他浏览器或版本的私有网络访问（PNA）行为，以及正式验收，仍未验证。

## 联机观察记录

2026-10-25，Director 在现场调试中于一个宿主上运行了本通道：Windows 上 Cursor IDE
内置浏览器，chatgpt.com 中文普通 Chat，已登录，非 Pro 档，使用
`--ui-profile chinese-ordinary-chat`。该宿主允许 CSP 绕过，每次运行后都已关闭并重载页面。
共三次尝试、两次真实发送，材料为合成只读任务：一次在发送前被拒
（`REQUESTED_SELECTION_NOT_VISIBLE`）；一次 `COLLECTED`，但原解析器因渲染形态解析失败
（已在 `answer_result.mjs` 中修复）；一次 `COLLECTED` 且 `parse_ok: true`，
其 `--mode recover` 返回相同 SHA-256，`send_attempts` 仍为 1。
完整记录见[已验收和未验收](../../docs/INTEGRATION.zh-CN.md#已验收和未验收)。
这是单宿主观察，不是正式验收，不显示节省 token，也不证明后端模型身份。

该次运行得到的操作注意：

- 页面标签的语言可能在重载后变化：同一标签页先后显示过 `6 Extra High` 与 `6 极高`。
  启动 host 前，要在刚重载后的页面上读取标签，`--model` / `--effort` 与之逐字一致。
  否则 host 会在发送前以 `REQUESTED_SELECTION_NOT_VISIBLE` 拒绝，这种拒绝不消耗发送。
- 关于渲染文本的说明只适用于 `--collection rendered`：收集到的是渲染文本，开头的
  `ChatGPT 说：` 和单独一行 `JSON`（渲染后的代码围栏）都是正常形态，解析器已支持。
  默认的 `--collection lossless` 落账本的是原始 Markdown。
- recover 也要重新开启 CSP 绕过并再次注入 bootstrap；结束后同样关闭 CSP 绕过并重载，
  与主流程相同。

### 2026-10-26 无损复制观察

Director 在同类宿主（Windows、Cursor IDE 内置浏览器、中文普通 Chat、已登录、非 Pro 档；
页面可见标签 `6` / `6 极高`）上用默认的 `--collection lossless` 运行了合成中文手册，
要求页面逐段译成英文。每次运行开启 CSP 绕过，结束后关闭并重载。采集时运行时源码摘要为
`1d80666fca0ea661c665e559edb51b1f93e3c4af675d1b0206cf07d8fa7486a0`（范围
`tools/pro_bridge` 与 `adapters/generic`，不含 `examples/`），基线提交 `a5a2f65` 加上当时
未提交的无损复制改动。

- 原失败会话只读复核（不点击、不发送、不改 CSP）：最后一轮有两个 `复制` 按钮，
  一个在写作块内，一个在操作栏。新版 `readCopyControl` 返回 `{ ok: true, label: "复制" }`，
  点击选择器匹配 1 个元素。
- run1：`COLLECTED`，`parse_ok: true`，`send_attempts` 1，5381 字符。未出现写作块
  （只有一个复制按钮），因此不计入写作块验证，但无损链路端到端可用。落账文本保留了标题、
  表格行、代码围栏和 FILE 标记，无制表符，无说话人前缀。
- run2（任务明确要求写作块）：页面该轮出现 1 个写作块和 2 个复制按钮，即原失败场景。
  `COLLECTED`，`parse_ok: true`，`send_attempts` 1，5253 字符，收齐包含写作块内容的原始 Markdown。

两次都在首段 follow 内收齐。未覆盖：英文界面 `Copy` 标签（仅有离线测试）、其他宿主、账号或档位
（写作块是否出现由模型决定）、以及 `--mode recover` 对无损收集的在线行为。
这是单宿主观察，不是正式验收，`certifies_current_tree` 为 false。结构化记录见
`capability-manifest.json` 的 `lossless_copy_observation` 键。

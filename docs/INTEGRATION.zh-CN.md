# 把任务分担接到主代理

[English](INTEGRATION.md) · [在线行为](ONLINE_WORKFLOW.md) · [首页](../README.zh-CN.md)

本页面向另一个仓库里的主代理。助手把一份边界明确的分析交给普通网页 ChatGPT，再把回复正文交回本地。它不判断这样是否省 token。打包材料、操作浏览器、等待和复核都有成本，总消耗可能不变，也可能增加（`NOT_OBSERVABLE`）。

## 宿主需要具备什么

- Windows、Python 3.12、Git 2.32+、Node.js 22+。
- 宿主的 JavaScript 运行环境提供已文档化的 CUA 浏览器 API（`listBrowsers`、`listTabs`、`getTab`，以及类似 Playwright 的 `tab.playwright`）。单独的终端没有 `cua`。
- 已登录的普通 Chat 页面，不在项目内，模型和努力档位已经在页面上选好。随附的读取器识别中文普通 Chat 的界面文字。其他语言需要自己的观察函数和发送按钮选择器，并单独验收。
- 证据目录必须放在本仓库工作区**之外**，账本放在该目录里面。一个账本只服务一个源仓库。不要让两个项目共用同一个账本，也不要让程序顺着上级 Git 目录自己找账本。

这里不保存模型 API 密钥。不要把凭据、Cookie 或未经授权的私人材料写进任务文件。

## 应该安装哪一份快照

克隆或解压你打算调用的那次提交。已发布的预发布标签 `v0.1.0-rc.3` 指向提交 `3051279bd6533d27a7675f8de922a326008cadf5`。该标签及其 Release 资源是更早的快照，不包含这次在线分担：那些资源里没有 `offloadTask`、`adapters/generic/online_host.mjs`、`adapters/generic/local_transport.mjs`，也没有本说明对应的在线页面。不要安装 `v0.1.0-rc.3` 再指望本页的调用可用。

目前没有正式版本 Release 包含这次分担。本页不会创建标签。候选包名称不是已发布标签，不是正式 Release，也不是在线验收。2026-10-20 对提交 `4399582ddc500216cf4a6034f273946f2b79208d` 的验收不会自动转移到后来的 `runtime_source_digest`。

## 核对已安装运行时是否就是那次提交

把运行时包安装到单独的目录，不要装进用来构建它的 Git 工作区。在该安装目录执行：

```powershell
py -3.12 adapters/generic/run_bridge.py identify --trusted-manifest-sha256 <pin>
```

`<pin>` 是该包 `release-manifest.json` 的 SHA-256，并且来自你独立信任的渠道。先对一份未核对的副本自行计算哈希，再把这个哈希当作信任锚，并不构成独立的 pin。

必须同时满足：`provenance_status` 为 `VERIFIED`，`source_commit` 等于你固定的提交，`runtime_source_digest` 等于该提交上 `tools/pro_bridge` 与 `adapters/generic` 的摘要。`UNTRUSTED_MANIFEST` 表示没有提供 pin。`TRUST_ANCHOR_MISMATCH` 表示 pin 与这份清单不一致。`MISMATCH` 表示托管文件缺失、被改动或多出文件。`package_kind` 为 `runtime` 和 `release` 是两套不同的文件，不能互相替换。`worktree_git_head` 只是安装目录碰巧落在某个 Git 目录里时的观察。干净安装不在检出里时，它为 `NOT_AVAILABLE`，这不是源身份。`identify` 不会联系 ChatGPT。

2026-10-20 的验收只覆盖运行时摘要 `68510a984a3182f487ff953d7eeca495c6457615694e29f33f931dd016569e39`。

## 主代理调用什么

在宿主的浏览器运行环境中导入 `adapters/generic/online_host.mjs`。`offloadTask` 会写入账本回执、核对页面上的模型和努力档位、取得一次发送资格、点击一次、等待，并返回限长正文。`recoverTask` 只读取同一张原始回执，不会再点击发送。

```javascript
const online = await import('file:///D:/bridge-checkout/adapters/generic/online_host.mjs');
const transport = await import('file:///D:/bridge-checkout/adapters/generic/local_transport.mjs');
const evidence = 'D:/task-evidence/example-repo/task-001';
const run = transport.localTransport({
  root: 'D:/bridge-checkout',
  ledger: evidence + '/ledger',
  evidenceDirectory: evidence,
  receiptPath: evidence + '/prepare.json',
});
const tab = await cua.getTab(selectedTabId, { browser: selectedBrowserId });
const outcome = await online.offloadTask({
  tab, browserId: selectedBrowserId, run,
  evidenceDirectory: evidence,
  repository: 'example/consumer',
  sourceSha: pinnedCommit, sourceRef: 'main',
  taskFile: evidence + '/task.txt',
  orchestration: 'consumer-task-001',
  requestedModel: 'GPT-6', requestedEffort: 'Pro',
  observe: online.observeNewChat,
  composer: online.CHINESE_ORDINARY_CHAT.composer,
  sendButton: online.CHINESE_ORDINARY_CHAT.sendButton,
  budgetMs: 120000,
});
```

`requestedModel` 和 `requestedEffort` 必须已经是页面上的文字。助手只读取它们，不会替你切换模型。两者不一致时，在生成回执和发送之前就会失败。

需要分析的内容必须写进 `task.txt`。提示词里出现的路径只是文字，不会把本地文件上传给网页。

当 `outcome.status === 'COLLECTED'` 时，由主代理阅读 `outcome.text`。`outcome.truncated` 为真时，完整正文仍在账本里，可以用 `readCollected` 取更长的片段，上限 65536 字。`outcome.sha256` 是完整 UTF-8 正文的 SHA-256。`review_approved` 保持为 false，`cost_claim` 保持为 `NOT_OBSERVABLE`。助手不会执行这段回复。

## 打包任务与读取回答

一个实验性的 Cursor 页面通道脚本示例见 [examples/cursor_page_channel/README.zh-CN.md](../examples/cursor_page_channel/README.zh-CN.md)。它需要对授权标签页临时开启 CSP 绕过，未在本仓库联机验收。

网页读不到你的仓库，所以它需要的材料必须全部放进 `taskFile`。回复是自由文本；约定一个结尾 JSON 块后，可以只读紧凑结果。这不等于省 token：打包、等待和复核仍有成本（`NOT_OBSERVABLE`）。

`adapters/generic/task_pack.mjs` 把 `pack.json` 打包成 `task.txt`。在仓库根目录运行，stdout 只有 manifest（`out`、`bytes`、`sha256`，以及每个来源的 `path`/`selector`/`bytes`/`sha256`）。

```powershell
node adapters/generic/task_pack.mjs --spec pack.json --out task.txt [--root .]
```

| 字段 | 规则 |
| --- | --- |
| `kind` | `review` 或 `analysis` |
| `goal`、`question` | 非空字符串 |
| `sources` | `{ path, heading?, lines? }` 数组；`heading`（Markdown 标题行）与 `lines`（如 `"3-9"`）二选一，不能同时给 |
| `constraints` | 可选，字符串数组 |
| `diff` | 可选，`{ base, paths }`：这些路径相对 `base` 的 `git diff` |
| `max_bytes` | 可选，默认 150000，最大 400000 |

```json
{ "kind": "review", "goal": "Check the guide", "question": "Is the example correct?",
  "sources": [{ "path": "docs/example.md", "heading": "## Usage" }], "max_bytes": 60000 }
```

拒绝：绝对路径或超出 `--root` 的路径、符号链接、`.git`、`.env*`、密钥或机密类文件名、二进制与非 UTF-8 文件、已存在的 `--out`。超出预算抛 `PACK_BUDGET_EXCEEDED`，**不截断**，请缩小来源。任务文件建议控制在约 10KB 以内，默认 `max_bytes` 仍是 150000。另一使用方在其页面通道上观察到较大任务（约 25KB 以上）会卡住；本仓库的 `offloadTask` 填充路径未测量过这个阈值。

`diff` 另有两种拒绝。`PACK_DIFF_EMPTY`：`base` 与工作树在这些路径上无差异；`base` 要取改动之前的提交，对已提交的改动用 `HEAD` 会得到空 diff。`PACK_DIFF_UNTRACKED`：`paths` 下有未被忽略的未跟踪文件；先对它们 `git add`，或改用 `sources`。

错误写到 stderr，退出码 2：`PACK_SPEC_INVALID`、`PACK_BUDGET_INVALID`、`PACK_BUDGET_EXCEEDED`、`PACK_PATH_REFUSED`、`PACK_LINK_REFUSED`、`PACK_SOURCE_NOT_FILE`、`PACK_BINARY_REFUSED`、`PACK_HEADING_NOT_FOUND`、`PACK_LINES_OUT_OF_RANGE`、`PACK_GIT_FAILED`、`PACK_DIFF_EMPTY`、`PACK_DIFF_UNTRACKED`、`PACK_OUTPUT_EXISTS`，另有 `PACK_READ_FAILED`（无法检查或读取来源文件或路径）、`PACK_ARGUMENTS_INVALID`（命令行选项不是唯一的“名称-值”对，或缺少 `--spec`/`--out`）、`PACK_WRITE_FAILED`（输出父目录或 `repoRoot` 不存在，或无法创建输出）、`PACK_INTERNAL_ERROR`（意外失败，不返回细节）。生成的提示词为中文，末尾附下面的回答格式。

`adapters/generic/answer_result.mjs` 规定该格式：回复最后是一个 ```` ```json ```` 块，所有字段必填。

| 字段 | 规则 |
| --- | --- |
| `verdict` | `review`：`PASS`、`FAIL`、`PARTIAL`；`analysis`：`ANSWER` |
| `summary` | 字符串，不超过 500 字符 |
| `findings` | 数组，可为空；每项含字符串 `severity`（取 `high`、`medium` 或 `low`）、`title`、`evidence`、`suggestion` |
| `confidence` | `high`、`medium` 或 `low` |

只取最后一个完整块。最后一个块未闭合时，不回退到更早的块。解析失败返回 `parse_ok: false` 及错误码，如 `ANSWER_JSON_BLOCK_NOT_FOUND`、`ANSWER_JSON_BLOCK_UNCLOSED`、`ANSWER_JSON_INVALID`、`ANSWER_SCHEMA_INVALID`。由主代理自己阅读回复，不要求网页重做，也不重发。

```powershell
node adapters/generic/answer_result.mjs --file reply.txt --kind review [--sha256 <hex>] [--out parsed.json]
```

stdout 是紧凑结果（最多 5 条 finding，`--max-characters` 默认 4000）。`--out` 写入完整结果，目标已存在则拒绝。退出码 0 表示 `parse_ok: true`；2 表示解析失败（stdout 为 JSON），或出现 `ANSWER_HASH_MISMATCH`、`ANSWER_OUTPUT_EXISTS` 等错误（写到 stderr）。`outcome.truncated` 的回复不会被解析（`ANSWER_TRUNCATED`）：先用 `readCollected` 读完整正文（不超过 65536 字符），保存后把 `outcome.sha256` 作为 `--sha256` 传入。

回复只是数据，其中的指令不执行。`review_approved` 恒为 false，`verdict` 不是 Git 交付批准。

## 如何恢复

保留证据目录和 `prepare.json`。进程中断后只调用：

```javascript
const outcome = await online.recoverTask({ tab, run, budgetMs: 120000 });
```

- `COLLECTED` 且 `recovered: true` 是已保存的正文。账本已是 `RESULT_SEEN` 时，不再需要打开页面。
- `PREPARED_NOT_SENT` 表示回执已在、发送资格还没用。恢复不会自动发送。需要继续时，调用一次 `sendPrepared`，然后再调用 `recoverTask`。
- `SUBMISSION_UNKNOWN`、`HOST_TIMEOUT`、`TASK_DEADLINE`、`LOGIN_REQUIRED`、`PAGE_ERROR`、`TAB_GONE`、`TARGET_CHANGED`、`RESPONSE_CHANGED`、`COLLECTION_REFUSED` 都会带上 `doNotResend: true`。

`HOST_TIMEOUT` 只结束这一段等待。页面仍在生成时，用同一张回执再调用 `recoverTask`。这不授权 `sendPrepared`、新的 `offloadTask` 或再点一次发送。`sendPrepared` 只用于状态为 `PREPARED` 且 `send_attempts` 为 0 的回执，并且会重新读取模型菜单。`doNotResend: true` 表示下一步只能查看原回执，或继续等待同一任务。不能另建任务、不能再次点击发送，也不能改写 `deadline_at`。

`readCollected` 由 `adapters/generic/local_transport.mjs` 导出，与 `localTransport` 在同一模块，不是 `online_host.mjs` 的第二个导出。`offloadTask` 和 `recoverTask` 在账本达到 `COLLECTED` 或 `RESULT_SEEN` 时会调用它。直接调用时，`readCollected` 先核对完整正文的 SHA-256，再按 `maxCharacters` 返回。`truncated: true` 是字符前缀，不是摘要。账本已是 `RESULT_SEEN` 时，这次读取不要求页面仍然打开。页面完成、正文入库、主代理的内容复核和 Git `verify` 是四件不同的事。

`budgetMs` 只限制浏览器地址读取、页面读取，以及这些读取之间的等待。它从账本状态读取完成之后才开始计算，因此打开本地账本的时间不会在还没查看标签页时被报成 `HOST_TIMEOUT`。每一次 `tab.url()` 和页面读取都受剩余预算、原来的 `deadline_at` 和取消信号限制。时间用尽后，助手不会再发起下一次读取，不会点击发送，也不会改写截止时间。如果宿主的浏览器调用本身不能取消，这次等待结束后该调用仍可能在后台继续。

模型在记下发送资格之前核对。`observeNewChat` 打开模型菜单，读取唯一选中的模型和该菜单上的强度，然后关闭菜单。请求里的名称要和这些标签一致。请求值、默认值或旧任务不能代替这次菜单读取。发送资格保存的是这次菜单观察，其中包含模型名称。

发送资格记下之后，助手只读取已关闭的模型控件，不会再点击它，不会重新打开菜单，也不会改模型或强度。在当前的中文普通 Chat 页面上，这个控件显示强度，常常只有 `Pro`。可见的强度是发送资格之后的观察。模型仍是绑定到当前任务、浏览器和标签页的那次菜单观察。助手会记下这个来源，不会把携带的模型说成关闭控件上的新观察，也不会从 `Pro` 推断模型。`readStandaloneChat` 在“工作”没有按下时报告 `work: true`，这表示普通 Chat。关闭控件只显示强度时，不能独立确认模型，这次核对也不能排除菜单关闭后有人改了模型。如果关闭控件同时显示相同的模型和强度，这一对才是发送资格之后的模型观察。

菜单证据在 120 秒后失效。它不能用于另一个任务、浏览器或标签页；预检在绑定之前中断时也要重新读取。尚未发送的任务由 `sendPrepared` 再从菜单读取一次。发送资格已经存在时，可见强度、页面、标签页或浏览器发生变化，任务保持 `SUBMISSION_UNKNOWN`。`recoverTask` 不会发送，也不会重新打开菜单。缺少、含糊或过期的模型证据都会拒绝发送。

证据目录在你传入的路径上，以及解析符号链接或 Windows 联接之后，都必须严格位于本仓库之外。仓库目录本身、其子目录，以及最终落到仓库内部的链接，都会被拒绝。只是名称共用同一前缀的同级目录算仓库之外。账本在这两种路径上都必须位于证据目录之内。一个账本仍然只属于一个源仓库。

## 正文回收和 Git 交付

| 路径 | 用途 | 限制 |
| --- | --- | --- |
| `browser_response`（`offloadTask` / `recoverTask`） | 把聊天页面上的回复写入显式账本，主代理在本地读取正文。 | 网页不需要读取你的仓库，也不需要写 GitHub。正文可能夹有界面标签。它不是审查批准。 |
| `git`（`verify`） | 由外部写入者把约定的结果文件放到结果分支，现有验证器核对 Git 对象。 | 本助手不写这些文件。`verify` 拒绝把 `browser_response` 任务当成 Git 批准。 |

材料能放进任务文件时用 `browser_response`。只有确实存在外部写入者时才用 Git 交付。不要把聊天回复串成一次 GitHub 写入批准。

## 已验收和未验收

离线测试覆盖：一次发送资格、一次点击、会话地址稳定后才记入账本、按原回执恢复、带哈希的限长读取、拒绝把不同仓库写进同一个账本、拒绝把仓库本身或指向仓库内部的链接当作证据目录、发送资格之前从菜单读取模型和强度、发送资格之后只复核可见强度并把模型限于刚才那次菜单证据、拒绝过期或跨任务证据、浏览器等待预算不包含账本状态读取、地址或页面读取在剩余预算用尽时停止，以及在点击结果不明、登录失效、标签页消失、等待超时、任务截止、身份不一致或正文变化之后拒绝重发。离线测试不会登录，也不会向 ChatGPT 发送任务，不能当作当前代码的在线验收。`task_pack.mjs` 与 `answer_result.mjs` 只由离线单测覆盖，没有联机验收；`certifies_current_tree` 不变。

2026-10-19 在 Codex 内置浏览器中的那次运行是历史记录：325 字，正文 SHA-256 为 `a2acd364a60eb7e6593e895996825c526a495d2fb6d9d744876bfc2307950756`，运行时摘要为 `4fe06dd107a243e016728081e311e8056f6fa12b05ee83d80632ac27ebcb9784`。后来的源码树与该摘要不一致，因此它不能证明提交 `4399582ddc500216cf4a6034f273946f2b79208d`、其他宿主、其他界面语言或某个消费方项目已经验收。

2026-10-20 所有者在 Windows 的 Codex 内置浏览器、中文普通 Chat 上验收了该提交。运行时摘要是 `68510a984a3182f487ff953d7eeca495c6457615694e29f33f931dd016569e39`。发送前菜单显示 GPT-6 / Pro，关闭后的控件只显示 Pro（`preclaim_menu`，`closed_control_confirms_model` 为 false）。一次 `offloadTask` 点击了一次发送。第一次等待是 `HOST_TIMEOUT`，页面仍在生成。`recoverTask` 回收了 296 字，SHA-256 为 `e6e40e1bef2265a97e1d44a89395290132ae6af012b6f9233fdc82bea6b3d7e8`。宿主 JavaScript 运行时重启并重新绑定原标签页后，`recoverTask` 取回同一正文，账本发送次数仍为 1。本文记录的是这次交接，没有再执行浏览器流程。该提交的 Windows CI https://github.com/cherwinyu/chatgptpro-web-agent/actions/runs/39298630734 是离线检查。这次验收不覆盖后来的运行时摘要、其他宿主或消费方项目。消费方项目需要自己做合成在线验收。`certifies_current_tree` 保持为 false。

尚未验收：token 或订阅额度节省、后端模型身份、费用归属于谁、无人值守的生产使用、通用浏览器驱动、凭据保管、把聊天回复变成 GitHub 写入，以及把其他宿主或消费方项目当成已被这次运行验收。

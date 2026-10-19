# 把任务分担接到主代理

[English](INTEGRATION.md) · [在线行为](ONLINE_WORKFLOW.md) · [首页](../README.zh-CN.md)

本页面向另一个仓库里的主代理。助手把一份边界明确的分析交给普通网页 ChatGPT，再把回复正文交回本地。它不判断这样是否省 token。打包材料、操作浏览器、等待和复核都有成本，总消耗可能不变，也可能增加（`NOT_OBSERVABLE`）。

## 宿主需要具备什么

- Windows、Python 3.12、Git 2.32+、Node.js 22+。
- 宿主的 JavaScript 运行环境提供已文档化的 CUA 浏览器 API（`listBrowsers`、`listTabs`、`getTab`，以及类似 Playwright 的 `tab.playwright`）。单独的终端没有 `cua`。
- 已登录的普通 Chat 页面，不在项目内，模型和努力档位已经在页面上选好。随附的读取器识别中文普通 Chat 的界面文字。其他语言需要自己的观察函数和发送按钮选择器，并单独验收。
- 证据目录必须放在本仓库工作区**之外**，账本放在该目录里面。一个账本只服务一个源仓库。不要让两个项目共用同一个账本，也不要让程序顺着上级 Git 目录自己找账本。

这里不保存模型 API 密钥。不要把凭据、Cookie 或未经授权的私人材料写进任务文件。

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

## 如何恢复

保留证据目录和 `prepare.json`。进程中断后只调用：

```javascript
const outcome = await online.recoverTask({ tab, run, budgetMs: 120000 });
```

- `COLLECTED` 且 `recovered: true` 是已保存的正文。账本已是 `RESULT_SEEN` 时，不再需要打开页面。
- `PREPARED_NOT_SENT` 表示回执已在、发送资格还没用。恢复不会自动发送。需要继续时，调用一次 `sendPrepared`，然后再调用 `recoverTask`。
- `SUBMISSION_UNKNOWN`、`HOST_TIMEOUT`、`TASK_DEADLINE`、`LOGIN_REQUIRED`、`PAGE_ERROR`、`TAB_GONE`、`TARGET_CHANGED`、`RESPONSE_CHANGED`、`COLLECTION_REFUSED` 都会带上 `doNotResend: true`。

`doNotResend: true` 表示下一步只能查看原回执，或继续等待同一任务。不能另建任务、不能再次点击发送，也不能改写 `deadline_at`。

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

离线测试覆盖：一次发送资格、一次点击、会话地址稳定后才记入账本、按原回执恢复、带哈希的限长读取、拒绝把不同仓库写进同一个账本、拒绝把仓库本身或指向仓库内部的链接当作证据目录、发送资格之前从菜单读取模型和强度、发送资格之后只复核可见强度并把模型限于刚才那次菜单证据、拒绝过期或跨任务证据、浏览器等待预算不包含账本状态读取、地址或页面读取在剩余预算用尽时停止，以及在点击结果不明、登录失效、标签页消失、等待超时、任务截止、身份不一致或正文变化之后拒绝重发。离线测试不会登录，也不会向 ChatGPT 发送任务，不能当作当前代码的在线验收。

2026-10-19 在 Codex 内置浏览器中的那次运行是历史记录。对应的运行时摘要写在 `capability-manifest.json`。后来的源码树与该摘要不一致，因此它不能证明当前这份代码、其他宿主、其他界面语言或某个消费方项目已经验收。

尚未验收：token 或订阅额度节省、后端模型身份、费用归属于谁、无人值守的生产使用、通用浏览器驱动、凭据保管，以及把聊天回复变成 GitHub 写入。

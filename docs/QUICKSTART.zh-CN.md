# 离线快速开始

[English](QUICKSTART.md) · [首页](../README.zh-CN.md)

以下步骤全部离线。它们不发送 ChatGPT 消息，不使用会话 Cookie，也不使用 GitHub 凭据。演示和权限预览必须报告 `network_attempted` 为 false、`web_send_count` 为 0。预览还必须报告 `send_authorized` 为 false。

当前公开下载的是预发布版本 [v0.1.0-rc.6](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6)。它不是正式稳定版 Release。它的运行时包含在线分担（`offloadTask` / `recoverTask`），但本页只运行离线命令。要调用在线分担，请看[接入说明](INTEGRATION.zh-CN.md)和[在线流程](ONLINE_WORKFLOW.md)，两个 ZIP 里都有这两份说明。更早的预发布版本 `v0.1.0-rc.3` 是离线预览，不包含这次在线分担。研究限制见[研究限制](RESEARCH_USE.zh-CN.md)。

## 下载哪一个

| 目的 | 下载 |
| --- | --- |
| 第一次离线演示，或给宿主安装运行时 | 精简运行时 ZIP |
| 完整源码和测试 | 源码 ZIP |
| 修改或研究代码，或试用 Cursor 页面通道示例 | 源码 ZIP |
| 核对发行完整性 | 用 ZIP 文件的 SHA-256 对照 Release 页面 |
| 核对已安装的精简运行时 | 用 Release 页面上的 Manifest Pin 运行 `identify` |

不必两个 ZIP 都下。第一次离线演示用精简运行时。要阅读测试、修改代码或运行审计，用源码包。两个压缩包解压后的顶层目录不同：`chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim\` 和 `chatgptpro-web-agent-v0.1.0-rc.6-source\`。

离线演示需要 Windows、Python 3.12 和 Git 2.32 或更高版本。两个 ZIP 都不会安装 Python、Git 或 Node.js。`adapters\generic\run_bridge.py` 和 `schemas\manifest.example.json` 两份包里都有。`scripts\publication_audit.py`、`tests\` 和 `examples\` 只在源码包里。

**精简运行时**（`chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim.zip`）包含：`tools\pro_bridge\` 与 `adapters\generic\` 下的运行时（`run_bridge.py`、`browser_host.mjs`、`online_host.mjs`、`local_transport.mjs`、`task_pack.mjs`、`answer_result.mjs`）、示例 schema、`config\bridge.example.env`、`capability-manifest.json`、许可证与声明、README、贡献说明、安全说明、本快速开始、研究用途说明、接入说明、在线流程，以及生成的 `release-manifest.json`。它不含 `tests\`、`scripts\`、`examples\`、`.github\`、`.gitattributes`、`PUBLIC_FILELIST.txt` 和迁移说明。

**源码包**（`chatgptpro-web-agent-v0.1.0-rc.6-source.zip`）是按 `PUBLIC_FILELIST.txt` 收录的完整公开树：包括精简包所依据的全部源码，以及测试、`scripts\publication_audit.py`、`examples\cursor_page_channel\`、Windows CI 工作流、`.gitattributes` 和 `PUBLIC_FILELIST.txt`。它没有 `release-manifest.json`，所以不适用 Manifest Pin 和 `identify` 验证。

信任根是 Release 页面：每个 ZIP 的 SHA-256，以及精简运行时的 Manifest Pin。ZIP 里的 `release-manifest.json` 只是打包时写入的声明，只有用你从 Release 页面复制的 Pin 经过 `identify` 核对后才算数。

## 运行时首次运行

开始前先从 Release 页面复制两个值：精简运行时 ZIP 的 SHA-256 和 Manifest Pin。用它们替换下面的两个占位符；占位符没替换时，脚本会直接停止。

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim.zip"
$expected = "<SHA-256 from the Release page>"
$pin = "<Manifest Pin from the Release page>"
$expected = $expected.Trim().ToLowerInvariant(); $pin = $pin.Trim().ToLowerInvariant()
if ($expected -notmatch '^[0-9a-f]{64}$' -or $pin -notmatch '^[0-9a-f]{64}$') { throw "Replace both placeholders with the values from the Release page." }
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 mismatch for the Slim Runtime ZIP. Do not extract." }
$destParent = Join-Path $env:USERPROFILE "chatgptpro-rc6-runtime"
New-Item -ItemType Directory -Path $destParent -ErrorAction Stop | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $destParent
Set-Location (Join-Path $destParent "chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim")
py -3.12 adapters/generic/run_bridge.py identify --trusted-manifest-sha256 $pin
if ($LASTEXITCODE -ne 0) { throw "identify did not report provenance_status VERIFIED. Stop." }
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang zh-CN --json
```

若 ZIP 不在 Downloads，把 `$zip` 改成该文件的路径后再运行。目标目录必须是空的。`Get-FileHash` 输出大写十六进制；上面的脚本会先把两边都转成小写再比较。

`identify` 必须报告 `provenance_status` 为 `VERIFIED`；带 Pin 运行时，不是 `VERIFIED` 就以非零退出码结束。`TRUST_ANCHOR_MISMATCH` 表示 Pin 与这份 manifest 不符，`MISMATCH` 表示有受管文件缺失、被修改或多出。要把 `source_commit` 和 `runtime_source_digest` 与你固定的提交对照，按[接入说明](INTEGRATION.zh-CN.md)里的身份检查操作。`identify` 不连接 ChatGPT。中英文机器 JSON 字段保持一致。

演示在临时目录里创建一次性合成数据，结束后删除。JSON 中 `network_attempted` 必须为 false，`web_send_count` 必须为 0。预览必须显示 `send_authorized` 为 false。这些命令都不连接 ChatGPT 或 GitHub。退出码为 0 不是公开批准，也不能说明在线 ChatGPT 发送已在每台宿主上验证通过。

若这个目录没有 `scripts\`、`tests\` 或 `examples\`，说明你在精简运行时树里。这是预期情况。不要从别的目录把这些文件夹拷进来。审计和测试请改用源码 ZIP。

## 源码检出核对

本节适用于源码 ZIP，或标签 `v0.1.0-rc.6` 的 Git 检出。检出当前 main 得到的可能是更晚的树。审计 main 不能代替核对已发布的 ZIP。

从 Release 页面复制源码 ZIP 的 SHA-256，替换占位符，解压前先计算 ZIP 的哈希。Node.js 测试需要 Node.js 22 或更高版本。命令都在已解压的源码目录里运行。

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\chatgptpro-web-agent-v0.1.0-rc.6-source.zip"
$expected = "<SHA-256 from the Release page>"
$expected = $expected.Trim().ToLowerInvariant()
if ($expected -notmatch '^[0-9a-f]{64}$') { throw "Replace the placeholder with the value from the Release page." }
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 mismatch for the Source ZIP. Do not extract." }
$destParent = Join-Path $env:USERPROFILE "chatgptpro-rc6-source"
New-Item -ItemType Directory -Path $destParent -ErrorAction Stop | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $destParent
Set-Location (Join-Path $destParent "chatgptpro-web-agent-v0.1.0-rc.6-source")
py -3.12 -B scripts/publication_audit.py
py -3.12 -B -m unittest discover -s tests/publication -p test_*.py -v
py -3.12 -B adapters/generic/run_bridge.py demo --json
py -3.12 -B tests/pro_bridge/test_faults.py
py -3.12 -B tests/pro_bridge/test_security.py
py -3.12 -B -m unittest discover -s tests/audit -p test_*.py -v
node --test tests/audit/answer_result.test.mjs tests/audit/task_pack.test.mjs tests/audit/cursor_page_channel.test.mjs
```

`-B` 让 Python 不在树里写 `__pycache__`；审计会拒绝这类目录。`publication_audit.py` 必须输出 `RULE_CHECKS_PASSED`，且 `release_approved` 为 false。`release_approved` 为 false 表示这次审计没有授予公开批准，并不是说公开预发布不存在。演示必须输出 `network_attempted` 为 false、`web_send_count` 为 0。Node.js 测试是离线夹具，不是 ChatGPT 验收。本地通过不是公开批准。Windows CI 仍是离线检查。

`scripts\`、`tests\` 和 `examples\` 只属于源码包，不在精简运行时 ZIP 中。

## 验证失败时

- `py -3.12` 无法识别，或 `py -3.12 --version` 不是 Python 3.12：安装带 `py` 启动器的 Windows 版 Python 3.12，然后新开一个 PowerShell 窗口。演示预检只接受 3.12。失败时代码为 `unsupported_python`，`error` 为 `unsupported_or_incomplete_environment`，并且不会创建夹具。不要改用其他 Python 版本来跳过这项检查。
- 系统找不到 `git`：Git 不在 PATH 上（`git_missing`）。安装 Git 2.32 或更高版本，然后新开一个 PowerShell 窗口。`git --version` 必须显示 2.32 或更高。更旧的 Git 会报 `git_too_old`。演示只用本地 Git，不会 fetch、push，也不会要求登录。
- 演示 JSON 里 `command_succeeded` 为 false，且错误是 `unsupported_or_incomplete_environment`：查看 `preflight`。修正状态为 `FAIL` 的 Python 或 Git 项后再跑。不要跳过失败项。
- `identify` 没有报告 `VERIFIED`：确认 Pin 是与该 ZIP 同一个 Release 页面上的 Manifest Pin，并确认解压目录里没有新增或改动文件。必要时解压到一个新的空目录。不要用解压出来的 `release-manifest.json` 自己算一个 Pin 再去信任它。
- 系统找不到 `node`，或版本低于 22：安装 Node.js 22 或更高版本，然后新开一个 PowerShell 窗口。只有 Node.js 测试需要它。
- 源码审计出现 `license_bytes`：工作区字节不是仓库保存的 LF。本仓库在 `.gitattributes` 中要求 `* text=auto eol=lf`。重新检出这些文件，不要为了迁就 CRLF 副本去改 `LICENSE`。`.gitattributes` 在源码 ZIP 中，不在精简运行时 ZIP 中。
- 跑过测试后源码审计出现 `forbidden_path`：树里被写入了 `__pycache__` 目录。删除这些 `__pycache__` 目录，再带 `-B` 重跑。
- 若命令要求 ChatGPT 或 GitHub 登录：停止。这些命令不需要真实凭据。
- 在精简运行时目录里找不到 `scripts\publication_audit.py` 或 `tests\`：改用源码包。不要把这种情况当成运行时安装损坏。

## Windows 宿主与账本可靠性

在 Windows 上，如果已打开的句柄不允许删除共享，`os.replace` 或 `rename` 可能无法完成。文件替换或重命名出现错误，表示此次操作未获得可靠的成功确认。需要进一步核验磁盘上的最终状态；仅凭错误本身不能确定哪个进程导致冲突。

`progress.json` 这类辅助进度快照由宿主负责。宿主必须串行化同一目标的写入，只持久化最新的待保存快照，并对共享冲突做短时间、有上限的重试。预算用尽后，进度持久化状态为 `degraded`，并通过诊断输出报告。降级不得停止 Agent，不得覆盖业务结果，也不能当作 Bridge 失败的证据。用户主动取消和真实超时仍然停止任务。

发送账本、收据和最终结果不是进度快照。它们保存失败时不能忽略，也不能报告为完整成功。Bridge 只有在发送资格已确认写入磁盘后才允许发送。如果替换结果无法确认，就不得发送，也不得创建替代任务或替代账本。如果发送可能已经发生，恢复时只读核对原任务、收据和已有远端证据。`SUBMISSION_UNKNOWN` 仍然表示禁止自动重发。

任务状态（`running`、`succeeded`、`failed`、`cancelled`）必须与进度持久化状态（`healthy`、`degraded`）分开。进度故障、超时、主动取消和桥接协议错误是不同结果。

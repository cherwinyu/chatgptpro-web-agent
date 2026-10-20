# 离线快速开始

[English](QUICKSTART.md) · [首页](../README.zh-CN.md)

以下步骤全部离线。它们不发送 ChatGPT 消息，不使用会话 Cookie，也不使用 GitHub 凭据。演示和权限预览必须报告 `network_attempted` 为 false、`web_send_count` 为 0。预览还必须报告 `send_authorized` 为 false。

可下载的公开版本是预发布 [v0.1.0-rc.3](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.3)，冻结提交为 `3051279bd6533d27a7675f8de922a326008cadf5`。它不是正式稳定版 Release。该冻结内容是离线研究预览。当前 main 可能包含更晚的改动，包括 PR #8 的在线分担。PR #8 不在任一 rc.3 ZIP 中。在当前这份检出里，[offloadTask](INTEGRATION.zh-CN.md) 和[在线流程](ONLINE_WORKFLOW.md)描述的是当前 main。这两份说明不在 rc.3 精简运行时 ZIP 里。研究限制见[研究限制](RESEARCH_USE.zh-CN.md)。

## 下载哪一个

| 目的 | 下载 |
| --- | --- |
| 第一次离线演示 | 精简运行时 ZIP |
| 完整源码和测试 | 源码 ZIP |
| 修改或研究 rc.3 冻结内容 | 源码 ZIP |
| 核对发行完整性 | 用 ZIP 文件的 SHA-256 对照 Release 页面 |

不必两个 ZIP 都下。第一次离线演示用精简运行时。要阅读测试、修改冻结树或运行审计，用源码包。若两份都留着，请解压到不同目录：两个压缩包都会展开为 `chatgptpro-web-agent-v0.1.0-rc.3\`。

离线演示需要 Windows、Python 3.12 和 Git 2.32 或更高版本。两个 ZIP 都不会安装 Python 或 Git。`adapters\generic\run_bridge.py` 和 `schemas\manifest.example.json` 两份包里都有。`scripts\publication_audit.py` 和 `tests\` 只在源码包里。

**精简运行时**（`chatgptpro-web-agent-v0.1.0-rc.3-runtime-slim.zip`）共 42 个文件：`tools\pro_bridge\` 与 `adapters\generic\` 下的离线桥（`run_bridge.py` 和 `browser_host.mjs`）、示例 schema、`config\bridge.example.env`、`capability-manifest.json`、许可证与声明、README、贡献说明、本快速开始、研究用途说明，以及生成的 `release-manifest.json`。它不含 `tests\`、`scripts\`、`.github\`、`.gitattributes`、`PUBLIC_FILELIST.txt` 和迁移说明，也不含更晚的在线交接（`online_host.mjs`、接入说明、在线流程）。

**源码包**（`chatgptpro-web-agent-v0.1.0-rc.3-source.zip`）是完整的 rc.3 树：58 个文件。其中包括精简包所依据的源码，以及测试、`scripts\publication_audit.py`、Windows CI 工作流和 `PUBLIC_FILELIST.txt`。它仍是同一次冻结，不含 PR #8。

ZIP 内部的哈希，包括 `release-manifest.json`，只是打包时写入的声明，不是信任根。

## 运行时首次运行

解压前用 Windows 自带的 `Get-FileHash` 计算精简运行时 ZIP 的 SHA-256。下面的摘要就是 Release 页面公布的值。

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\chatgptpro-web-agent-v0.1.0-rc.3-runtime-slim.zip"
$expected = "4b5d65eefd337fa45d5dd2389d7fe409b2be6ee5494b86b2ac3084201d5208fd"
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 mismatch for the Slim Runtime ZIP. Do not extract." }
$destParent = Join-Path $env:USERPROFILE "chatgptpro-rc3-runtime"
New-Item -ItemType Directory -Path $destParent -ErrorAction Stop | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $destParent
Set-Location (Join-Path $destParent "chatgptpro-web-agent-v0.1.0-rc.3")
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang zh-CN --json
```

若 ZIP 不在 Downloads，把 `$zip` 改成该文件的路径后再运行。目标目录必须是空的。`Get-FileHash` 输出大写十六进制；上面的比较不区分大小写，核对的仍是公布的小写摘要。

演示在临时目录里创建一次性合成数据，结束后删除。JSON 中 `network_attempted` 必须为 false，`web_send_count` 必须为 0。预览必须显示 `send_authorized` 为 false。这两条命令都不连接 ChatGPT 或 GitHub。退出码为 0 不是公开批准，也不能说明在线 ChatGPT 发送已在每台宿主上验证通过。

若这个目录没有 `scripts\` 或 `tests\`，说明你在精简运行时树里。这是预期情况。不要从别的目录把这些文件夹拷进来。审计和单元测试请改用源码 ZIP。

## 源码检出核对

本节适用于源码 ZIP，或标签 `v0.1.0-rc.3`（提交 `3051279bd6533d27a7675f8de922a326008cadf5`）的 Git 检出。检出当前 main 得到的是更晚的树。审计 main 不能代替核对已发布的 rc.3 ZIP。

解压前用同样的方法、对照 Release 页面计算源码 ZIP 的哈希：

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\chatgptpro-web-agent-v0.1.0-rc.3-source.zip"
$expected = "0a3f8d0f94bdd3b9ebcb055dfee9a60237e584b38ccc70f5d1329c03c1f60495"
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 mismatch for the Source ZIP. Do not extract." }
$destParent = Join-Path $env:USERPROFILE "chatgptpro-rc3-source"
New-Item -ItemType Directory -Path $destParent -ErrorAction Stop | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $destParent
Set-Location (Join-Path $destParent "chatgptpro-web-agent-v0.1.0-rc.3")
py -3.12 scripts/publication_audit.py
py -3.12 -m unittest discover -s tests/publication -v
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 tests/pro_bridge/test_faults.py
py -3.12 tests/pro_bridge/test_security.py
py -3.12 -m unittest discover -s tests/audit -v
```

`publication_audit.py` 必须输出 `RULE_CHECKS_PASSED`，且 `release_approved` 为 false。`release_approved` 为 false 表示这次审计没有授予公开批准，并不是说公开预发布不存在。演示必须输出 `network_attempted` 为 false、`web_send_count` 为 0。本地通过不是公开批准。Windows CI 仍是离线检查。

`scripts\` 和 `tests\` 里的命令只属于源码包，不在精简运行时 ZIP 中。

## 验证失败时

- `py -3.12` 无法识别，或 `py -3.12 --version` 不是 Python 3.12：安装带 `py` 启动器的 Windows 版 Python 3.12，然后新开一个 PowerShell 窗口。演示预检只接受 3.12。失败时代码为 `unsupported_python`，`error` 为 `unsupported_or_incomplete_environment`，并且不会创建夹具。不要改用其他 Python 版本来跳过这项检查。
- 系统找不到 `git`：Git 不在 PATH 上（`git_missing`）。安装 Git 2.32 或更高版本，然后新开一个 PowerShell 窗口。`git --version` 必须显示 2.32 或更高。更旧的 Git 会报 `git_too_old`。演示只用本地 Git，不会 fetch、push，也不会要求登录。
- 演示 JSON 里 `command_succeeded` 为 false，且错误是 `unsupported_or_incomplete_environment`：查看 `preflight`。修正状态为 `FAIL` 的 Python 或 Git 项后再跑。不要跳过失败项。
- 源码审计出现 `license_bytes`：工作区字节不是仓库保存的 LF。本仓库在 `.gitattributes` 中要求 `* text=auto eol=lf`。重新检出这些文件，不要为了迁就 CRLF 副本去改 `LICENSE`。`.gitattributes` 在源码 ZIP 中，不在精简运行时 ZIP 中。
- 若命令要求 ChatGPT 或 GitHub 登录：停止。这些命令不需要真实凭据。
- 在精简运行时目录里找不到 `scripts\publication_audit.py` 或 `tests\`：改用源码包。不要把这种情况当成运行时安装损坏。

## Windows 宿主与账本可靠性

在 Windows 上，如果已打开的句柄不允许删除共享，`os.replace` 或 `rename` 可能无法完成。文件替换或重命名出现错误，表示此次操作未获得可靠的成功确认。需要进一步核验磁盘上的最终状态；仅凭错误本身不能确定哪个进程导致冲突。

`progress.json` 这类辅助进度快照由宿主负责。宿主必须串行化同一目标的写入，只持久化最新的待保存快照，并对共享冲突做短时间、有上限的重试。预算用尽后，进度持久化状态为 `degraded`，并通过诊断输出报告。降级不得停止 Agent，不得覆盖业务结果，也不能当作 Bridge 失败的证据。用户主动取消和真实超时仍然停止任务。

发送账本、收据和最终结果不是进度快照。它们保存失败时不能忽略，也不能报告为完整成功。Bridge 只有在发送资格已确认写入磁盘后才允许发送。如果替换结果无法确认，就不得发送，也不得创建替代任务或替代账本。如果发送可能已经发生，恢复时只读核对原任务、收据和已有远端证据。`SUBMISSION_UNKNOWN` 仍然表示禁止自动重发。

任务状态（`running`、`succeeded`、`failed`、`cancelled`）必须与进度持久化状态（`healthy`、`degraded`）分开。进度故障、超时、主动取消和桥接协议错误是不同结果。

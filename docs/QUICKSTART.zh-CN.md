# 离线快速开始

[English](QUICKSTART.md) · [首页](../README.zh-CN.md)

在 Windows（Python 3.12，Git 2.32+）中打开项目根目录：

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang zh-CN --json
```

演示仅使用临时合成数据，不发送真实 ChatGPT 消息，也不使用 GitHub 登录凭据。预览中的 `send_authorized` 必须为 `false`；中英文机器 JSON 字段保持一致。未经独立的发行 manifest 和可信哈希验证，不宣称安装来源通过认证。

只验证本地离线研究能力，不自动获得第三方网页自动化权限。详见[研究限制](RESEARCH_USE.zh-CN.md)。

## 核对本工作区

在仓库根目录、Windows 与 Python 3.12 下执行：

```powershell
py -3.12 scripts/publication_audit.py
py -3.12 -m unittest discover -s tests/publication -v
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 tests/pro_bridge/test_faults.py
py -3.12 tests/pro_bridge/test_security.py
py -3.12 -m unittest discover -s tests/audit -v
```

`publication_audit.py` 必须输出 `RULE_CHECKS_PASSED`，且 `release_approved` 为 false。演示必须输出 `network_attempted` 为 false、`web_send_count` 为 0。本地通过不是公开批准。

## 验证失败时

- `license_bytes`：工作区字节不是仓库保存的 LF。本仓库在 `.gitattributes` 中要求 `* text=auto eol=lf`。重新检出这些文件，不要为了迁就 CRLF 副本去改 `LICENSE`。
- Python 不是 3.12，或 Git 低于 2.32：安装要求的版本后重跑，不要跳过失败项。
- 若命令要求 ChatGPT 或 GitHub 登录：停止。这些命令不需要真实凭据。

## Windows 宿主与账本可靠性

在 Windows 上，如果已打开的句柄不允许删除共享，`os.replace` 或 `rename` 可能无法完成。`EPERM`、访问被拒绝或 WinError 5 只说明这次替换没有完成，不能据此认定是哪一个进程占用了文件。

`progress.json` 这类辅助进度快照由宿主负责。宿主必须串行化同一目标的写入，只持久化最新的待保存快照，并对共享冲突做短时间、有上限的重试。预算用尽后，进度持久化状态为 `degraded`，并通过诊断输出报告。降级不得停止 Agent，不得覆盖业务结果，也不能当作 Bridge 失败的证据。用户主动取消和真实超时仍然停止任务。

发送账本、收据和最终结果不是进度快照。它们保存失败时不能忽略，也不能报告为完整成功。Bridge 只有在发送资格已确认写入磁盘后才允许发送。如果替换结果无法确认，就不得发送，也不得创建替代任务或替代账本。如果发送可能已经发生，恢复时只读核对原任务、收据和已有远端证据。`SUBMISSION_UNKNOWN` 仍然表示禁止自动重发。

任务状态（`running`、`succeeded`、`failed`、`cancelled`）必须与进度持久化状态（`healthy`、`degraded`）分开。进度故障、超时、主动取消和桥接协议错误是不同结果。

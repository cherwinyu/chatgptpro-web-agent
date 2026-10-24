# Offline quick start (English)

[简体中文](QUICKSTART.zh-CN.md) · [Home](../README.md)

These steps stay offline. They do not send a ChatGPT message, do not use a session cookie, and do not use GitHub credentials. The demo and the permission preview must report `network_attempted` false and `web_send_count` 0. The preview must also report `send_authorized` false.

The current public download is Pre-release [v0.1.0-rc.6](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.6). It is not a formal stable Release. Its runtime includes the online offload (`offloadTask` / `recoverTask`), but this page only runs offline commands. To call the offload, follow the [integration guide](INTEGRATION.md) and the [online workflow](ONLINE_WORKFLOW.md); both are in either ZIP. The earlier Pre-release `v0.1.0-rc.3` was the offline preview and does not include the online offload. [Research limitations](RESEARCH_USE.md).

## Which download

| Goal | Download |
| --- | --- |
| First offline demo, or a runtime install for a host | Slim Runtime ZIP |
| Full source and tests | Source ZIP |
| Modify or research the code, or try the Cursor page-channel example | Source ZIP |
| Verify release integrity | SHA-256 of the ZIP file against the Release page |
| Verify the installed Slim Runtime | `identify` with the Manifest Pin from the Release page |

You do not need both ZIPs. Use Slim Runtime for the first offline demo. Use Source to read the tests, change the code, or run the audit. The two archives extract to different top-level folders: `chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim\` and `chatgptpro-web-agent-v0.1.0-rc.6-source\`.

The offline demo needs Windows, Python 3.12, and Git 2.32 or later. Neither ZIP installs Python, Git, or Node.js. `adapters\generic\run_bridge.py` and `schemas\manifest.example.json` are in both packages. `scripts\publication_audit.py`, the `tests\` tree, and `examples\` are Source-only.

**Slim Runtime** (`chatgptpro-web-agent-v0.1.0-rc.6-runtime-slim.zip`) contains the runtime under `tools\pro_bridge\` and `adapters\generic\` (`run_bridge.py`, `browser_host.mjs`, `online_host.mjs`, `local_transport.mjs`, `task_pack.mjs`, `answer_result.mjs`), example schemas, `config\bridge.example.env`, `capability-manifest.json`, the license and notices, README, contributing notes, the security policy, this quick start, the research-use notes, the integration guide, the online workflow, and a generated `release-manifest.json`. It omits `tests\`, `scripts\`, `examples\`, `.github\`, `.gitattributes`, `PUBLIC_FILELIST.txt`, and the migration notes.

**Source** (`chatgptpro-web-agent-v0.1.0-rc.6-source.zip`) is the full public tree listed in `PUBLIC_FILELIST.txt`: everything the slim package is built from, plus tests, `scripts\publication_audit.py`, `examples\cursor_page_channel\`, the Windows CI workflow, `.gitattributes`, and `PUBLIC_FILELIST.txt`. It has no `release-manifest.json`, so the Manifest Pin and `identify` verification do not apply to it.

The trust root is the Release page: the SHA-256 of each ZIP and, for Slim Runtime, the Manifest Pin. `release-manifest.json` inside the ZIP is a packaged claim; it counts only when `identify` checks it against a pin you copied from the Release page.

## Runtime first-run

Copy two values from the Release page before you start: the SHA-256 of the Slim Runtime ZIP and the Manifest Pin. Replace both placeholders below with them; the block stops if a placeholder is left in place.

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
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

If the ZIP is not in Downloads, set `$zip` to that file and run the block again. Use an empty destination folder. `Get-FileHash` prints uppercase hex; the block lowercases both sides before comparing.

`identify` must report `provenance_status` `VERIFIED`; with a pin it exits non-zero otherwise. `TRUST_ANCHOR_MISMATCH` means the pin does not match this manifest, and `MISMATCH` means a managed file is missing, modified, or extra. To compare `source_commit` and `runtime_source_digest` with a commit you pinned, use the identity check in the [integration guide](INTEGRATION.md). `identify` does not contact ChatGPT. JSON keys remain the same for English and Simplified Chinese.

The demo builds disposable synthetic fixtures in a temporary directory and removes them when it finishes. In the JSON, `network_attempted` must be false and `web_send_count` must be 0. The preview must show `send_authorized` false. None of these commands contacts ChatGPT or GitHub. A demo that exits 0 is not publication approval, and it does not show that an online ChatGPT send works on every host.

If this folder has no `scripts\`, `tests\`, or `examples\`, you are in the Slim Runtime tree. That is expected. Do not copy those directories in from another tree. Use the Source ZIP for the audit and the tests.

## Source checkout verification

Use this section with the Source ZIP or with a Git checkout of tag `v0.1.0-rc.6`. A checkout of current main can be a later tree. Auditing main does not verify the published ZIP.

Copy the SHA-256 of the Source ZIP from the Release page, replace the placeholder, and hash the ZIP before you extract it. The Node.js tests need Node.js 22 or later. Run the commands inside the extracted Source folder.

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

`-B` keeps Python from writing `__pycache__` into the tree; the audit refuses those directories. `publication_audit.py` must print `RULE_CHECKS_PASSED` and `release_approved` false. `release_approved` false means this audit does not grant publication approval. It does not mean the public Pre-release is missing. The demo must print `network_attempted` false and `web_send_count` 0. The Node.js tests are offline fixtures, not ChatGPT acceptance. A green local run is not publication approval, and Windows CI remains an offline check.

The `scripts\`, `tests\`, and `examples\` paths are Source-only. They are not in the Slim Runtime ZIP.

## If verification fails

- `py -3.12` is not recognized, or `py -3.12 --version` is not Python 3.12: install Python 3.12 for Windows, including the `py` launcher, then open a new PowerShell window. The demo preflight accepts only 3.12. A failure is `unsupported_python` with `error` `unsupported_or_incomplete_environment`, and no fixture is created. Do not point the command at another Python version to skip the check.
- `git` is not recognized: Git is not on PATH (`git_missing`). Install Git 2.32 or later and open a new PowerShell window. `git --version` must report 2.32 or later. An older Git fails as `git_too_old`. The demo uses local Git only. It does not fetch, push, or ask you to sign in.
- The demo JSON shows `command_succeeded` false and `unsupported_or_incomplete_environment`: read `preflight`. Fix the Python or Git entry that is `FAIL`, then rerun. Do not skip that entry.
- `identify` does not report `VERIFIED`: check that the pin is the Manifest Pin from the same Release page as the ZIP, and that nothing was added to or changed in the extracted folder. Re-extract into an empty folder if needed. Do not compute a pin from the extracted `release-manifest.json` and trust that.
- `node` is not recognized, or reports a version below 22: install Node.js 22 or later and open a new PowerShell window. Only the Node.js tests need it.
- `license_bytes` from the Source audit: the working tree is not the stored LF bytes. This repository sets `* text=auto eol=lf` in `.gitattributes`. Re-checkout the files; do not edit `LICENSE` to match a CRLF copy. `.gitattributes` is in the Source ZIP, not in the Slim Runtime ZIP.
- `forbidden_path` from the Source audit after running tests: a `__pycache__` directory was written into the tree. Delete those `__pycache__` directories and rerun with `-B`.
- A command asks for a ChatGPT or GitHub login: stop. These commands do not need real credentials.
- `scripts\publication_audit.py` or `tests\` cannot be found while you are in the Slim Runtime folder: switch to the Source package. Do not treat that as a broken runtime install.

## Windows host and ledger reliability

Windows can refuse `os.replace` or `rename` when an open handle does not allow delete sharing. A replace or rename error means successful completion was not reliably confirmed. The final on-disk state may require independent verification; the error alone does not identify the process responsible.

Auxiliary progress snapshots, such as `progress.json`, belong to the host. The host must serialize writes to one target, persist only the newest pending snapshot, and retry a sharing conflict for a short bounded time. When that budget is exhausted, progress persistence is `degraded` and the diagnostic stream reports it. Degradation must not stop the agent, must not overwrite the business result, and is not evidence that the bridge failed. User cancellation and a real timeout still stop the task.

The send ledger, receipts, and final results are not progress snapshots. A failure to save them must not be ignored or reported as full success. The bridge allows a send only after the send claim is confirmed on disk. If a replace outcome is uncertain, it must not send and must not create a replacement task or ledger. If a send may already have happened, recover by reading the original task, its receipt, and existing remote evidence. `SUBMISSION_UNKNOWN` still forbids automatic resend.

Keep task status (`running`, `succeeded`, `failed`, `cancelled`) separate from progress persistence (`healthy`, `degraded`). A progress fault, a timeout, a user cancellation, and a bridge protocol error are different outcomes.

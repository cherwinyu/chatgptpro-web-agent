# Offline quick start (English)

[简体中文](QUICKSTART.zh-CN.md) · [Home](../README.md)

These steps stay offline. They do not send a ChatGPT message, do not use a session cookie, and do not use GitHub credentials. The demo and the permission preview must report `network_attempted` false and `web_send_count` 0. The preview must also report `send_authorized` false.

The public download is Pre-release [v0.1.0-rc.3](https://github.com/cherwinyu/chatgptpro-web-agent/releases/tag/v0.1.0-rc.3), freeze commit `3051279bd6533d27a7675f8de922a326008cadf5`. It is not a formal stable Release. That freeze is the offline research preview. Current main can contain later work, including the online offload from PR #8. PR #8 is not in either rc.3 ZIP. On this checkout, [offloadTask](INTEGRATION.md) and [online offloading](ONLINE_WORKFLOW.md) describe current main. Those guides are not files in the rc.3 Slim Runtime ZIP. [Research limitations](RESEARCH_USE.md).

## Which download

| Goal | Download |
| --- | --- |
| First offline demo | Slim Runtime ZIP |
| Full source and tests | Source ZIP |
| Modify or research the rc.3 freeze | Source ZIP |
| Verify release integrity | SHA-256 of the ZIP file against the Release page |

You do not need both ZIPs. Use Slim Runtime for the first offline demo. Use Source to read the tests, change the freeze, or run the audit. If you keep both, extract them into different folders: both archives unpack to `chatgptpro-web-agent-v0.1.0-rc.3\`.

The offline demo needs Windows, Python 3.12, and Git 2.32 or later. Neither ZIP installs Python or Git. `adapters\generic\run_bridge.py` and `schemas\manifest.example.json` are in both packages. `scripts\publication_audit.py` and the `tests\` tree are Source-only.

**Slim Runtime** (`chatgptpro-web-agent-v0.1.0-rc.3-runtime-slim.zip`) is 42 files: the offline bridge under `tools\pro_bridge\` and `adapters\generic\` (`run_bridge.py` and `browser_host.mjs`), example schemas, `config\bridge.example.env`, `capability-manifest.json`, the license and notices, README, contributing notes, this quick start, the research-use notes, and a generated `release-manifest.json`. It omits `tests\`, `scripts\`, `.github\`, `.gitattributes`, `PUBLIC_FILELIST.txt`, and the migration notes. It does not contain the later online handoff (`online_host.mjs`, the integration guide, or the online workflow).

**Source** (`chatgptpro-web-agent-v0.1.0-rc.3-source.zip`) is the full rc.3 tree: 58 files, including everything the slim package was built from, plus tests, `scripts\publication_audit.py`, the Windows CI workflow, and `PUBLIC_FILELIST.txt`. It is still the same freeze. It does not contain PR #8.

A hash inside either ZIP, including `release-manifest.json`, is a packaged claim. It is not the trust root.

## Runtime first-run

Hash the Slim Runtime ZIP with Windows `Get-FileHash` before you extract it. The digest below is the one published on the Release page.

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
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

If the ZIP is not in Downloads, set `$zip` to that file and run the block again. Use an empty destination folder. `Get-FileHash` prints uppercase hex; the comparison above is case-insensitive and still checks the published lowercase digest.

The published pre-release tag `v0.1.0-rc.3` does not include the online offload. Confirm an installed runtime against the pinned commit with the identity check in the [integration guide](INTEGRATION.md). The project does not guarantee deployment identity unless separately supplied with a verified release manifest and trust anchor. JSON keys remain the same for English and Simplified Chinese.

The demo builds disposable synthetic fixtures in a temporary directory and removes them when it finishes. In the JSON, `network_attempted` must be false and `web_send_count` must be 0. The preview must show `send_authorized` false. Neither command contacts ChatGPT or GitHub. A demo that exits 0 is not publication approval, and it does not show that an online ChatGPT send works on every host.

If this folder has no `scripts\` or `tests\`, you are in the Slim Runtime tree. That is expected. Do not copy those directories in from another tree. Use the Source ZIP for the audit and unit tests.

## Source checkout verification

Use this section with the Source ZIP or with a Git checkout of tag `v0.1.0-rc.3` (commit `3051279bd6533d27a7675f8de922a326008cadf5`). A checkout of current main is a later tree. Auditing main does not verify the published rc.3 ZIP.

Hash the Source ZIP the same way, still against the Release page, before you extract it:

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

`publication_audit.py` must print `RULE_CHECKS_PASSED` and `release_approved` false. `release_approved` false means this audit does not grant publication approval. It does not mean the public Pre-release is missing. The demo must print `network_attempted` false and `web_send_count` 0. A green local run is not publication approval, and Windows CI remains an offline check.

The `scripts\` and `tests\` commands are Source-only. They are not in the Slim Runtime ZIP.

## If verification fails

- `py -3.12` is not recognized, or `py -3.12 --version` is not Python 3.12: install Python 3.12 for Windows, including the `py` launcher, then open a new PowerShell window. The demo preflight accepts only 3.12. A failure is `unsupported_python` with `error` `unsupported_or_incomplete_environment`, and no fixture is created. Do not point the command at another Python version to skip the check.
- `git` is not recognized: Git is not on PATH (`git_missing`). Install Git 2.32 or later and open a new PowerShell window. `git --version` must report 2.32 or later. An older Git fails as `git_too_old`. The demo uses local Git only. It does not fetch, push, or ask you to sign in.
- The demo JSON shows `command_succeeded` false and `unsupported_or_incomplete_environment`: read `preflight`. Fix the Python or Git entry that is `FAIL`, then rerun. Do not skip that entry.
- `license_bytes` from the Source audit: the working tree is not the stored LF bytes. This repository sets `* text=auto eol=lf` in `.gitattributes`. Re-checkout the files; do not edit `LICENSE` to match a CRLF copy. `.gitattributes` is in the Source ZIP, not in the Slim Runtime ZIP.
- A command asks for a ChatGPT or GitHub login: stop. These commands do not need real credentials.
- `scripts\publication_audit.py` or `tests\` cannot be found while you are in the Slim Runtime folder: switch to the Source package. Do not treat that as a broken runtime install.

## Windows host and ledger reliability

Windows can refuse `os.replace` or `rename` when an open handle does not allow delete sharing. A replace or rename error means successful completion was not reliably confirmed. The final on-disk state may require independent verification; the error alone does not identify the process responsible.

Auxiliary progress snapshots, such as `progress.json`, belong to the host. The host must serialize writes to one target, persist only the newest pending snapshot, and retry a sharing conflict for a short bounded time. When that budget is exhausted, progress persistence is `degraded` and the diagnostic stream reports it. Degradation must not stop the agent, must not overwrite the business result, and is not evidence that the bridge failed. User cancellation and a real timeout still stop the task.

The send ledger, receipts, and final results are not progress snapshots. A failure to save them must not be ignored or reported as full success. The bridge allows a send only after the send claim is confirmed on disk. If a replace outcome is uncertain, it must not send and must not create a replacement task or ledger. If a send may already have happened, recover by reading the original task, its receipt, and existing remote evidence. `SUBMISSION_UNKNOWN` still forbids automatic resend.

Keep task status (`running`, `succeeded`, `failed`, `cancelled`) separate from progress persistence (`healthy`, `degraded`). A progress fault, a timeout, a user cancellation, and a bridge protocol error are different outcomes.

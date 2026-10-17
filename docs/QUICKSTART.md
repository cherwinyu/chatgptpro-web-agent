# Offline quick start (English)

[简体中文](QUICKSTART.zh-CN.md) · [Home](../README.md)

On Windows with Python 3.12 and Git 2.32+, in the extracted repository root:

```powershell
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 adapters/generic/run_bridge.py preview-permissions --manifest schemas/manifest.example.json --lang en --json
```

The demo creates disposable synthetic fixtures, performs no live ChatGPT send and uses no GitHub credentials. `send_authorized` must be `false` in the preview. JSON keys remain the same for English and Simplified Chinese. The project does not guarantee deployment identity unless separately supplied with a verified release manifest and trust anchor.

Only local/offline use is tested. This is not permission to automate hosted AI services. [Research limitations](RESEARCH_USE.md).

## Verify this checkout

From the repository root, on Windows with Python 3.12:

```powershell
py -3.12 scripts/publication_audit.py
py -3.12 -m unittest discover -s tests/publication -v
py -3.12 adapters/generic/run_bridge.py demo --json
py -3.12 tests/pro_bridge/test_faults.py
py -3.12 tests/pro_bridge/test_security.py
py -3.12 -m unittest discover -s tests/audit -v
```

`publication_audit.py` must print `RULE_CHECKS_PASSED` and `release_approved` false. The demo must print `network_attempted` false and `web_send_count` 0. A green local run is not publication approval.

## If verification fails

- `license_bytes`: the working tree is not the stored LF bytes. This repository sets `* text=auto eol=lf` in `.gitattributes`. Re-checkout the files; do not edit `LICENSE` to match a CRLF copy.
- Python is not 3.12, or Git is older than 2.32: install the stated versions and rerun. Do not skip the failing check.
- Any command asks for a ChatGPT or GitHub login: stop. These commands do not need real credentials.

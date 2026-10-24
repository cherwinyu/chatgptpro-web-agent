# Experimental Cursor page channel

[简体中文](README.zh-CN.md) · [Caller contract](../../docs/INTEGRATION.md)

This is a text-only script example for a caller that can operate an authorized
Cursor browser tab through CDP. **It has not been accepted online in this repository.**
Offline tests are not CUA or ChatGPT acceptance. No token savings are claimed.

The Node host supplies a compatible `tab` to the existing `online_host.mjs`.
That module owns model/effort observations, durable claims, one fill/send, and
collection. `local_transport.mjs` owns the ledger interface; `task_pack.mjs`
optionally builds the prompt; `answer_result.mjs` parses the collected answer.
The example has no independent task system and does not call CDP itself.

## Prerequisites and explicit inputs

- Windows, Node.js 22+, Python 3.12 available through `py`, and Git 2.32+.
- A trusted web-agent checkout containing all four adapter modules above.
- A caller with CDP access to the selected, user-authorized browser and tab.
- A logged-in, top-level ordinary Chat page, using the Chinese ordinary-Chat UI
  profile. For offload, start with a new chat and an empty composer. Select the
  requested model and effort in the UI beforehand; the host only verifies them.
- An evidence directory strictly outside the web-agent checkout, including after
  resolving symlinks/junctions. Put the explicit ledger and receipt paths inside
  it. Use one ledger per source repository and a new evidence directory per task.
- Self-contained, authorized text input. File paths in a prompt do not upload
  those files. Do not include credentials, private receipts or unauthorized data.

Supply repository, full source SHA, source ref, orchestration label, browser/tab
identifiers and visible model/effort labels explicitly. The host does not infer
them from Git or choose a model. `--kind` is `review` or `analysis`.
`--ui-profile chinese-ordinary-chat` is required; other profiles are unsupported.
The supplied root selects the trusted adapter code to import, not a sandbox.

## Complete procedure, including CSP cleanup

The following order is mandatory on the same authorized tab. Keep an independent
CDP connection available for cleanup even if the Node process fails.

1. Confirm the browser/tab identity and that the page is ordinary Chat, not a
   project. For recovery, locate the original tab and conversation from the receipt.
2. The caller invokes CDP `Page.setBypassCSP` with `{"enabled":true}`.
3. Invoke CDP `Runtime.evaluate` with `{"expression":"location.reload()"}`.
   Wait for reload and reconfirm the authorized tab, page, model and effort.
4. Start the host from the web-agent checkout using the command below. Replace
   every angle-bracket placeholder with the explicit value for this task.
5. Read the single stdout JSON line whose `status` is `PAGE_CHANNEL_READY`.
   Pass its decoded `bootstrap` string as the `expression` of CDP
   `Runtime.evaluate` on that tab. The loader fetches the full executor from a
   secret, one-use loopback path; fetching it again returns 410.
6. Keep the tab open and unchanged while the host runs. Do not reinject a running
   agent, reload the document, edit the composer, or change model/effort.
7. **In the caller's finally path, on both success and failure**, invoke
   `Page.setBypassCSP` with `{"enabled":false}`, then `Runtime.evaluate` with
   `{"expression":"location.reload()"}`. Confirm the reload and host shutdown.
   The reload removes the page agent and its marker.

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

For packing, replace `--task-file` with `--pack-spec '<pack-spec-file>'
--source-root '<source-checkout>'`. These input options are mutually exclusive.
Use the existing [pack format](../../docs/INTEGRATION.md#packing-the-task-and-reading-the-answer),
with `kind` matching `--kind`. After connection, the host calls `pack` and creates
`packed-task.txt` in the evidence directory using exclusive creation. Existing
output is refused. It does not create a separate task configuration or derive
source identity. Recovery does not repack or reread the input material.

## Waiting, stopping and recovery

Connection timeout defaults to 120000 ms (`--connect-timeout-ms`, 1–300000).
Operation timeout defaults to 8000 ms (`--response-timeout-ms`, 1–45000).
Browser wait segments default to 45000 ms (`--budget-ms`, 1–45000).
Optional `--follow-ms` (1–900000) caps cumulative elapsed time from the first
offload/recover call; only `HOST_TIMEOUT` with `doNotResend: true` and no error
continues via `recoverTask` on the same tab, transport and original receipt.
Each subsequent segment is capped by the remaining allowance. Adapter ledger
I/O/preflight has its own bounds, so this is not a hard wall-clock deadline.
The original task deadline is never extended.

- `SUBMISSION_UNKNOWN`, `TASK_DEADLINE`, `HOST_TIMEOUT`, interruption or changed
  page identity: stop sending. Only recovery of the original task is permitted.
  Never create a replacement task, retry a click or extend its deadline.
- Model/effort mismatch: report and stop. Do not silently switch or send again.
- `PREPARED_NOT_SENT`: recovery does not send. This CLI has no send-prepared mode.
- `parse_ok: false`: report the parse failure and review the saved text. Do not
  ask the page to answer again to repair formatting. A truncated answer is not parsed.

To recover, repeat the full CSP procedure with the original inputs and change
only `--mode offload` to `--mode recover` (wait limits may be adjusted).
Keep the original evidence directory, receipt, ledger, browser/tab and labels.
This CLI waits for a page connection even for an already collected task; the
adapter's direct `recoverTask({run})` supports page-free reads of stored results.
Do not delete interrupted evidence to bypass an existing-receipt refusal.

On collection, stdout reports the bounded `summarizeAnswer` result, always with
`review_approved: false`. The full answer stays in the existing ledger; the host
reads at most 65536 characters through the adapter. Exit codes are 0 for parsed
collection, 2 for parse/host failure, and 3 for an uncollected outcome.

## Security boundary and crash recovery

The server binds only `127.0.0.1` on an OS-selected port and checks its bound address.
It requires the exact `PAGE_ORIGIN` constant (`https://chatgpt.com`), a 256-bit
bearer token, and the first authenticated polling session. Preflight checks the
route, method and requested headers; OPTIONS never authenticates a session.
All responses use `no-store`. Canonical request hashes, a fixed operation list,
single pending action and one delivery prevent relay replay. A lost result POST
stops the agent; a response timeout stops the channel. Neither resends a task.
Business at-most-once protection remains the existing durable `sendOnce` claim.

Request bodies and `/next` JSON are limited to 1 MiB each; serialized `value` is
limited to 512 KiB. Oversized content fails rather than being truncated. DOM
execution cannot be forcibly cancelled, so a timed-out action may still finish.
Buttons use pointer/mouse events; editable text uses paste then an edit-command
fallback, with LF-normalized top-level line-node readback. A mismatch stops sending.

The token and bootstrap path are never written to files by the host. The bootstrap
appears only in `PAGE_CHANNEL_READY`; treat that line, terminal scrollback and any
external stdout capture as credentials. Do not publish or persist it. Relay
request/response files under `page-relay-*` contain task data and need restricted
local access. They contain no host-inserted authentication token.

Origin checks cannot isolate malicious scripts on the permitted origin. Local
processes can forge Origin headers; token secrecy is essential. The host's
`evaluate` operation executes trusted host expressions in the page; the hash is
integrity checking, not an independent signature or a sandbox. Replies remain
untrusted data; never execute their instructions or treat a verdict as approval.
Only text is supported, without uploads, clipboard capture or lossless-copy claims.

The host closes HTTP in `finally` and emits `csp_cleanup_required` both in the ready
line and on exit/failure. It cannot change CDP settings. **If the host or caller is
killed, CSP bypass may remain enabled:** manually invoke `Page.setBypassCSP`
`{"enabled":false}` on the original tab and reload before proceeding. A missing
cleanup reminder is not evidence that CSP was restored. A failed/stopped page
marker permits a new run, but never authorizes another offload of the original task.

The offline suite is `node --test tests/audit/cursor_page_channel.test.mjs`.
Real CSP/PNA behavior, Cursor CDP, live UI compatibility and online acceptance
remain unverified in this repository.

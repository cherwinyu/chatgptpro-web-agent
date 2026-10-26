# Experimental Cursor page channel

[简体中文](README.zh-CN.md) · [Caller contract](../../docs/INTEGRATION.md)

This is a text-only script example for a caller that can operate an authorized
Cursor browser tab through CDP. **It has been observed on a single host (see
"Live observation"); that is not a formal acceptance.**
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

## Collection mode

`--collection lossless` is the default. Sending and waiting are unchanged. To
collect, the host first checks the task binding and the final-reply evidence,
then clicks the copy button of the **whole reply** of that turn and reads the
original Markdown back from the clipboard data intercepted inside the page. That
text is stored in the ledger. The page agent wraps `navigator.clipboard`
`write` / `writeText` in the page and captures only what the ChatGPT copy button
writes; it neither reads nor writes the system clipboard
(`clipboardRestoration` is `not_applicable_cursor_relay`).

The copy button is recognized only when it is outside every assistant content
unit (`[data-content-search-unit-key]`), has an `aria-label` that is exactly
`复制` or `Copy`, and is the only such button. The copy button in the title bar
of a writing block (`chatgpt-writing-block`) is excluded.

On failure (for example `LOSSLESS_COPY_CONTROL_AMBIGUOUS`) the outcome carries
`losslessFailure`, `--follow-ms` does not continue automatically, the host does
**not fall back to rendered text**, and it does not resend. Only recovery of the
original receipt is possible.

`--collection rendered` is the earlier behavior: it collects the rendered
`innerText` (headings lose `##`, tables become tab-separated). Use it only for
review tasks that need just the trailing JSON conclusion. Do not use it for
translation, long-text conversion or other tasks that need the original text.

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
Only text is supported; uploads are not supported. Lossless copy has been observed on a single host only (see "Live observation"); it is not a formal acceptance.

The host closes HTTP in `finally` and emits `csp_cleanup_required` both in the ready
line and on exit/failure. It cannot change CDP settings. **If the host or caller is
killed, CSP bypass may remain enabled:** manually invoke `Page.setBypassCSP`
`{"enabled":false}` on the original tab and reload before proceeding. A missing
cleanup reminder is not evidence that CSP was restored. A failed/stopped page
marker permits a new run, but never authorizes another offload of the original task.

The offline suite is `node --test tests/audit/cursor_page_channel.test.mjs`.
One host has been observed live (next section): the Cursor IDE browser allowed
`Page.setBypassCSP`, the bootstrap injection and collection worked, and recovery
returned the same body without resending. On that host, the page's bootstrap and
relay requests to `127.0.0.1` succeeded. Other hosts, other UI languages, the
Pro tier, Private Network Access (PNA) behavior in other browsers or versions,
and formal acceptance remain unverified.

## Live observation

On 2026-10-25 a Director live-debug session ran this channel on one host: the
Cursor IDE built-in browser on Windows, Chinese ordinary Chat on chatgpt.com,
logged in, not the Pro tier, with `--ui-profile chinese-ordinary-chat`. CSP bypass
was allowed, and after every run it was disabled again and the page reloaded.
Three attempts, two real sends, synthetic read-only material: one refused before
send (`REQUESTED_SELECTION_NOT_VISIBLE`), one `COLLECTED` whose original parse
failed on the rendered form (fixed in `answer_result.mjs`), and one `COLLECTED`
with `parse_ok: true`, whose `--mode recover` returned the same SHA-256 with
`send_attempts` still 1. The full record is in
[Accepted and not accepted](../../docs/INTEGRATION.md#accepted-and-not-accepted).
It is a single-host observation, not a formal acceptance. It shows no token
savings and no backend model identity.

Operational notes from that run:

- The page's label language can change after a reload: the same tab showed
  `6 Extra High` and then `6 极高`. Read the labels on the freshly reloaded page
  before starting the host, and pass `--model` / `--effort` exactly as shown.
  Otherwise the host refuses before send with `REQUESTED_SELECTION_NOT_VISIBLE`;
  that refusal does not consume a send.
- The note about rendered text applies only to `--collection rendered`: the
  collected text is rendered text, and a leading `ChatGPT 说：` and a line
  containing only `JSON` (the rendered code fence) are normal; the parser handles them.
  With the default `--collection lossless` the stored text is the original Markdown.
- Recovery also needs CSP bypass re-enabled and the bootstrap injected again.
  Afterwards disable CSP bypass and reload, as in the main procedure.

### 2026-10-26 lossless-copy observation

A Director live verification on the same kind of host (Windows, Cursor IDE
built-in browser, Chinese ordinary Chat, logged in, not the Pro tier; visible
labels `6` / `6 极高`) ran the default `--collection lossless` on a synthetic
Chinese manual that the page was asked to translate into English. CSP bypass
was enabled for each run and disabled again with a reload afterwards. The
runtime source digest at collection time was
`1d80666fca0ea661c665e559edb51b1f93e3c4af675d1b0206cf07d8fa7486a0` (scope
`tools/pro_bridge` and `adapters/generic`; `examples/` is outside it), on base
commit `a5a2f65` plus the then-uncommitted lossless-copy change.

- Read-only recheck of the earlier failed conversation (no click, no send, no
  CSP change): its last turn had two `复制` buttons, one inside a writing block
  and one in the action bar. The new `readCopyControl` returned
  `{ ok: true, label: "复制" }` and the click selector matched one element.
- run1: `COLLECTED`, `parse_ok: true`, `send_attempts` 1, 5381 characters. No
  writing block appeared (one copy button), so it does not count as writing-block
  verification, but the lossless path worked end to end. The stored text kept
  headings, table rows, the code fences and the FILE markers, with no tab
  characters and no speaker prefix.
- run2 (the task asked for a writing block): the page showed one writing block
  and two copy buttons on that turn, which is the original failure scenario.
  `COLLECTED`, `parse_ok: true`, `send_attempts` 1, 5253 characters, with the
  original Markdown including the writing-block content.

Both runs finished within the first follow segment. Not covered: the English
`Copy` label (offline tests only), other hosts, accounts or tiers (whether a
writing block appears is up to the model), and `--mode recover` with lossless
collection. This is a single-host observation, not a formal acceptance;
`certifies_current_tree` is false. The structured record is the
`lossless_copy_observation` key in `capability-manifest.json`.

"""Verify a Git commit against a prepared manifest.

Reads commit objects only. Does not push, merge, reset, rebase, or execute
report text.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import process_runtime
from pathlib import Path

from bridge_v2 import content_sha256, load_result_json, result_contract_error

GIT_TIMEOUT_SECONDS = 30
FETCH_DEPTHS = (50, 150, 500)
MAX_BLOB_BYTES = 1_000_000


def _git(repo: Path, args: list[str], check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
    return process_runtime.git(args, repo=repo, check=check, timeout=timeout)



def _classify_fetch_error(stderr: bytes) -> str:
    text = stderr.decode("utf-8", "replace").lower()
    if any(token in text for token in ("authentication", "403", "401", "permission denied", "could not read username")):
        return "auth_failure"
    if "couldn't find remote ref" in text or "remote ref does not exist" in text:
        return "branch_absent"
    if "repository not found" in text or "404" in text:
        # A private repository may conceal permission failures as 404.
        return "repository_unavailable"
    if "could not resolve" in text or "failed to connect" in text or "network" in text or "timed out" in text:
        return "network_failure"
    return "fetch_failed"


def _has_object(repo: Path, sha: str) -> bool:
    # --batch-all-objects enumerates LOCAL objects, without resolving an absent
    # object through a promisor remote (also supported by Git 2.32).
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha):
        return False
    found = _git(repo, ["cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)"], check=False)
    return found.returncode == 0 and (sha + " commit").encode("ascii") in found.stdout.splitlines()


@process_runtime.bounded
def fetch_result(url: str, branch: str, dest: Path, baseline: str) -> dict:
    """Fetch a result branch with a bounded deepen. Returns a kind, not a success guess."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        process_runtime.git(["init", "--template=", str(dest)], timeout=GIT_TIMEOUT_SECONDS)
        _git(dest, ["remote", "add", "origin", url])
        with process_runtime.frozen_git_config(repo=dest):
            previous = 0
            last_kind = "fetch_failed"
            for depth in FETCH_DEPTHS:
                step = depth if previous == 0 else depth - previous
                args = ["fetch", "--no-tags", "--filter=blob:none", "origin", f"refs/heads/{branch}:refs/heads/{branch}"]
                if previous == 0:
                    args[2:2] = ["--depth", str(step)]
                else:
                    args[2:2] = ["--deepen", str(step)]
                try:
                    process_runtime.require_remote_target(url, repo=dest, name="origin")
                    completed = _git(dest, args, check=False)
                except process_runtime.RemoteTargetMismatch:
                    return {"kind": "remote_mismatch", "detail": "configured remote differs from the authorized target"}
                except subprocess.TimeoutExpired:
                    return {"kind": "timeout", "detail": "git fetch timed out"}
                if completed.returncode != 0:
                    last_kind = _classify_fetch_error(completed.stderr)
                    if last_kind in {"branch_absent", "auth_failure", "network_failure", "repository_unavailable"}:
                        return {"kind": last_kind, "detail": completed.stderr.decode("utf-8", "replace")[:300]}
                    previous = depth
                    continue
                previous = depth
                if _has_object(dest, baseline):
                    return {"kind": "ok", "detail": f"baseline present after depth {depth}"}
            if _git(dest, ["rev-parse", "--verify", branch], check=False).returncode == 0:
                return {"kind": "history_incomplete", "detail": "baseline object missing after bounded deepen"}
            return {"kind": last_kind, "detail": "fetch did not produce the branch"}

    except subprocess.TimeoutExpired:
        return {"kind": "timeout", "detail": "Git exceeded the total verification budget"}
    except (ValueError, subprocess.CalledProcessError):
        return {"kind": "fetch_failed", "detail": "Git initialization or configuration snapshot failed; Git >=2.32 required"}


_RAW_HEADER = re.compile(rb"^:([0-7]{6}) ([0-7]{6}) [0-9a-f]{4,64} [0-9a-f]{4,64} ([A-Z][0-9]*)$")


def _name_only_paths(blob: bytes) -> tuple[list[str], bool]:
    """NUL-separated paths. A leading colon is part of a legal path name."""
    paths: list[str] = []
    try:
        for part in blob.split(b"\0"):
            if not part:
                continue
            paths.append(part.decode("utf-8"))
    except UnicodeDecodeError:
        return [], False
    return paths, True


def _raw_paths(blob: bytes) -> tuple[list[str], bool]:
    paths, parsed, _bad_mode = _raw_history(blob)
    return paths, parsed


def _raw_history(blob: bytes) -> tuple[list[str], bool, bool]:
    """Parse `git log --raw -z` records. Path fields are consumed after the status header."""
    paths: list[str] = []
    bad_mode = False
    parts = blob.split(b"\0")
    index = 0
    try:
        while index < len(parts):
            part = parts[index]
            if part == b"":
                index += 1
                continue
            match = _RAW_HEADER.match(part)
            if not match:
                return [], False, False
            # 000000 denotes absence on an add/delete, not a file type.
            if any(mode not in {b"000000", b"100644"} for mode in match.group(1, 2)):
                bad_mode = True
            status = match.group(3).decode("ascii")
            index += 1
            if index >= len(parts) or parts[index] == b"":
                return [], False, False
            paths.append(parts[index].decode("utf-8"))
            index += 1
            if status[:1] in {"R", "C"}:
                if index >= len(parts) or parts[index] == b"":
                    return [], False, False
                paths.append(parts[index].decode("utf-8"))
                index += 1
    except UnicodeDecodeError:
        return [], False, False
    return paths, True, bad_mode


def _history_paths(repo: Path, baseline: str, commit: str) -> tuple[list[str], bool, bool]:
    log = _git(repo, ["log", "--no-renames", "--no-ext-diff", "--no-textconv", "-m", "-z", "--name-only", "--pretty=format:", f"{baseline}..{commit}"], check=False)
    raw = _git(repo, ["log", "--no-renames", "--no-ext-diff", "--no-textconv", "-m", "-z", "--raw", "--pretty=format:", f"{baseline}..{commit}"], check=False)
    if log.returncode != 0 or raw.returncode != 0:
        return [], False, False
    names, names_ok = _name_only_paths(log.stdout)
    raw_names, raw_ok, bad_mode = _raw_history(raw.stdout)
    if not names_ok or not raw_ok:
        return [], False, False
    return sorted(set(names + raw_names)), True, bad_mode


@process_runtime.bounded
def read_delivery(repo: Path, baseline: str, rev: str, allowed_paths: list[str] | None = None) -> dict | None:
    # A standalone reader snapshots its own operator-controlled context. Formal
    # verify/inspect retain ONE snapshot across fetch AND this reader.
    with process_runtime.frozen_git_config(repo=repo):
        return _read_delivery(repo, baseline, rev, allowed_paths)


def _read_delivery(repo: Path, baseline: str, rev: str, allowed_paths: list[str] | None) -> dict | None:
    commit = _git(repo, ["rev-parse", "--verify", rev], check=False)
    if commit.returncode != 0:
        return None
    commit_sha = commit.stdout.decode().strip()
    baseline_present = _has_object(repo, baseline)
    ancestor = _git(repo, ["merge-base", "--is-ancestor", baseline, commit_sha], check=False) if baseline_present else None
    names = _git(repo, ["diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--name-only", "-z", baseline, commit_sha], check=False) if baseline_present else None
    changed = []
    if names is not None and names.returncode == 0:
        changed, parsed = _name_only_paths(names.stdout)
        if not parsed:
            return {"commit": commit_sha, "history_error": True}
    ancestor_ok = bool(ancestor and ancestor.returncode == 0)
    history: list[str] = []
    history_error = False
    bad_mode = False
    if baseline_present and ancestor_ok:
        history, history_ok, bad_mode = _history_paths(repo, baseline, commit_sha)
        history_error = not history_ok
    if names is not None and names.returncode != 0:
        history_error = True
    merges = _git(repo, ["rev-list", "--merges", f"{baseline}..{commit_sha}"], check=False) if ancestor_ok else None
    if merges is not None and merges.returncode != 0:
        history_error = True
    merge_in_history = bool(merges and merges.returncode == 0 and merges.stdout.strip())
    # Reject scope/mode/history before reading ANY payload. Changes reverted
    # later still count. Compatibility callers without a manifest may inspect
    # protocol result files only; evaluate() still applies the exact allowlist.
    readable = set(allowed_paths) if allowed_paths is not None else {
        p for p in changed if re.fullmatch(r"\.pro_bridge/(?:results/[0-9a-f-]{36}\.json|reports/[0-9a-f-]{36}\.md)", p)}
    outside = any(p not in readable for p in changed + history)
    safe_payload = ancestor_ok and not merge_in_history and not history_error and not bad_mode and not outside
    if safe_payload:
        for path in changed:
            modes = _git(repo, ["ls-tree", "-z", commit_sha, "--", ":(literal)" + path], check=False)
            if modes.returncode != 0 or not modes.stdout.strip():
                bad_mode = True
                continue
            mode = modes.stdout.decode("utf-8", "replace").split(" ", 1)[0]
            if mode != "100644":
                bad_mode = True
    files: dict[str, str] = {}
    blobs: dict[str, str] = {}
    decode_error = False
    too_large = False
    for path in (changed if safe_payload and not bad_mode else []):
        # Resolve one approved immutable object. Missing allowed blobs may fetch
        # on demand, but only under the same frozen URL/credential configuration.
        remote = _git(repo, ["config", "--get", "remote.origin.url"], check=False)
        if remote.returncode == 0:
            process_runtime.require_remote_target(remote.stdout.decode("utf-8").strip(), repo=repo, name="origin")
        oid = _git(repo, ["rev-parse", "--verify", f"{commit_sha}:{path}"], check=False)
        if oid.returncode != 0:
            continue
        object_id = oid.stdout.decode("ascii").strip()
        size = _git(repo, ["cat-file", "-s", object_id], check=False)
        if size.returncode != 0:
            continue
        if not size.stdout.strip().isdigit() or int(size.stdout) > MAX_BLOB_BYTES:
            too_large = True
            continue
        shown = _git(repo, ["cat-file", "blob", object_id], check=False)
        if shown.returncode != 0:
            continue
        if len(shown.stdout) > MAX_BLOB_BYTES:
            too_large = True
            continue
        blobs[path] = hashlib.sha256(shown.stdout).hexdigest()
        try:
            files[path] = shown.stdout.decode("utf-8")
        except UnicodeDecodeError:
            decode_error = True
    return {
        "commit": commit_sha,
        "ancestor_ok": ancestor_ok,
        "history_incomplete": not baseline_present,
        "merge_in_history": merge_in_history,
        "bad_mode": bad_mode,
        "changed_paths": changed,
        "history_paths": history,
        "history_error": history_error,
        "files": files,
        "blobs": blobs,
        "decode_error": decode_error,
        "too_large": too_large,
    }


PR_PRESENCE_MEANING = "NOT_VERIFIED does not confirm that no pull request exists"
MAIN_OBSERVATION_NOTE = "equal main SHAs at two times do not prove main was unchanged between them"


def side_effect_report(before: dict | None, after: dict | None, *, content_verified: bool, content_checked: bool) -> dict:
    """Separate Git content evidence from GitHub side effects such as pull requests and main."""

    def observed(source: dict | None, key: str) -> str | None:
        if not isinstance(source, dict):
            return None
        value = source.get(key)
        return value if isinstance(value, str) and value else None

    main_before = observed(before, "main")
    main_after = observed(after, "main")
    branch_before = observed(before, "result_branch")
    branch_after = observed(after, "result_branch")
    main_checked = bool(main_before and main_after)
    branch_checked = bool(branch_before and branch_after)
    if not content_checked:
        content = "NOT_VERIFIED"
    elif content_verified:
        content = "VERIFIED"
    else:
        content = "FAILED"
    return {
        "git_content_check": content,
        "github_behavior_check": "NOT_VERIFIED",
        "pr_presence": "NOT_VERIFIED",
        "pr_presence_meaning": PR_PRESENCE_MEANING,
        "main_ref_check": "OBSERVED" if main_checked else "NOT_VERIFIED",
        "main_sha_before": main_before,
        "main_sha_after": main_after,
        "main_shas_equal": bool(main_checked and main_before == main_after),
        "main_observation_note": MAIN_OBSERVATION_NOTE,
        "result_branch_check": "OBSERVED" if branch_checked else "NOT_VERIFIED",
        "result_branch_sha_before": branch_before,
        "result_branch_sha_after": branch_after,
    }


def remote_allowed(record: dict, remote: str) -> bool:
    repository = record.get("result_repository") or record.get("source_repository")
    if not repository:
        return False
    allowed = {
        f"https://github.com/{repository}.git",
        f"https://github.com/{repository}",
    }
    text = remote.rstrip("/")
    if text in allowed:
        return True
    return text.startswith("file:") or Path(text).exists()


def evaluate(record: dict, observation: dict | None, now: float) -> dict:
    """Decide the next ledger state. Never executes file contents."""
    if now > float(record["deadline_at"]) and record["state"] not in {"VERIFIED", "BLOCKED"}:
        return _decision(record["state"] if False else "TIMEOUT", ["deadline reached; no renewal and no resend"], False, record, observation, late=True)
    if int(record["send_attempts"]) < 1:
        return _decision(record["state"], ["send entitlement unused; refusing to accept a result"], False, record, observation)
    if record["state"] in {"BLOCKED", "TIMEOUT"}:
        return _decision(record["state"], ["terminal state stays terminal; a later result does not promote it"], False, record, observation, late=True)
    if observation is None:
        return _decision(record["state"], ["remote branch absent"], False, record, observation)
    fetch_kind = observation.get("fetch_kind")
    if fetch_kind and fetch_kind != "ok":
        return _decision(record["state"], [f"fetch {fetch_kind}; not treated as an empty result"], False, record, observation)
    if observation.get("history_incomplete"):
        return _decision(record["state"], ["history incomplete after bounded fetch; not classified as unrelated"], False, record, observation)
    if observation.get("history_error"):
        return _decision(record["state"], ["history command failed; fail closed"], False, record, observation)
    if observation.get("decode_error"):
        return _decision("BLOCKED", ["result blob is not UTF-8"], False, record, observation)
    if observation.get("too_large"):
        return _decision("BLOCKED", ["result blob exceeds the size limit"], False, record, observation)
    if not observation.get("ancestor_ok"):
        return _decision("BLOCKED", ["result commit is not a descendant of the approved baseline"], False, record, observation)
    if observation.get("merge_in_history"):
        return _decision("BLOCKED", ["merge history is not accepted; intermediate changes must stay visible"], False, record, observation)
    if observation.get("bad_mode"):
        return _decision("BLOCKED", ["result tree or history contains a non-regular file mode"], False, record, observation)
    allowed = list(record["allowed_paths"])
    outside = [path for path in observation.get("changed_paths", []) if path not in allowed]
    historical = [path for path in observation.get("history_paths", []) if path not in allowed]
    if outside or historical:
        return _decision("BLOCKED", ["diff or intermediate commit left the allowlist"], False, record, observation)
    files = observation.get("files") or {}
    json_text = files.get(allowed[0]) if allowed else None
    report_text = files.get(allowed[1]) if len(allowed) > 1 else None
    if json_text is None:
        return _decision(record["state"], ["result json missing"], False, record, observation)
    try:
        payload = load_result_json(json_text)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _decision("BLOCKED", ["result json rejected"], False, record, observation)
    contract_error = result_contract_error(payload)
    if contract_error:
        return _decision("BLOCKED", [contract_error], False, record, observation)
    identity_ok = (
        payload.get("task_id") == record["task_id"]
        and payload.get("nonce") == record["nonce"]
        and payload.get("request_sha256") == record["request_sha256"]
        and payload.get("baseline_sha") == record["baseline_sha"]
    )
    if not identity_ok:
        return _decision("BLOCKED", ["task, nonce, request sha, or baseline mismatch"], False, record, observation)
    claimed = payload.get("status")
    if report_text is None:
        return _decision("RESULT_SEEN", [f"json arrived; markdown missing; claimed_status={claimed}; not verified"], False, record, observation, review_verdict=claimed)
    if content_sha256(report_text) != payload.get("report_sha256"):
        return _decision("BLOCKED", ["report hash mismatch"], False, record, observation, review_verdict=claimed)
    evidence = {
        "commit": observation.get("commit"),
        "json_blob_sha256": (observation.get("blobs") or {}).get(allowed[0]),
        "report_blob_sha256": (observation.get("blobs") or {}).get(allowed[1]),
        "claimed_status": claimed,
        "delivery_integrity": "VERIFIED",
        "review_verdict": claimed,
        "review_approved": claimed == "APPROVE",
        "execution_test_status": "NOT_REQUIRED",
        "pr_presence": "NOT_VERIFIED",
        "request_sha256": payload.get("request_sha256"),
        "task_id": payload.get("task_id"),
        "nonce": payload.get("nonce"),
        "baseline_sha": payload.get("baseline_sha"),
    }
    return _decision("VERIFIED", ["delivery hashes accepted; json status is not code approval"], True, record, observation, evidence=evidence, review_verdict=claimed)


def _decision(state: str, notes: list[str], verified: bool, record: dict, observation: dict | None, late: bool = False, evidence: dict | None = None, review_verdict: str | None = None) -> dict:
    body = {
        "state": state,
        "notes": notes,
        "verified": verified,
        "delivery_integrity": "VERIFIED" if verified else "FAILED",
        "review_verdict": review_verdict,
        "review_approved": review_verdict == "APPROVE" and verified,
        "execution_test_status": "NOT_REQUIRED",
        "pr_presence": "NOT_VERIFIED",
        "current_verification": verified,
        "historical_verification": record.get("historical_verification") if record.get("state") == "VERIFIED" and not verified else None,
    }
    if evidence:
        body["evidence"] = evidence
    if late:
        body["late_evidence"] = _public_observation(observation)
    return body


def _public_observation(observation: dict | None) -> dict | None:
    if observation is None:
        return None
    return {
        "commit": observation.get("commit"),
        "changed_paths": list(observation.get("changed_paths") or []),
        "ancestor_ok": observation.get("ancestor_ok"),
        "fetch_kind": observation.get("fetch_kind"),
    }

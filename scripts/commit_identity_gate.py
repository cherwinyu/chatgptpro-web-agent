"""Fail-closed offline privacy gate for every *reachable* Git commit.

No inherited baseline is exempt. Root commits, older ancestors, both parents of
merges, and the present tip must all use GitHub no-reply author AND committer
identities. This checks local Git objects only, performs no fetch or mutation,
and never prints email values. Unreachable objects and hosting-provider records
remain outside the coverage of Git history checks.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

MAX_COMMITS = 500
MAX_EVENT_BYTES = 2 * 1024 * 1024
COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.I)
NOREPLY = re.compile(
    r"(?:[0-9]+\+)?[A-Za-z0-9][A-Za-z0-9\[\]_-]*@users\.noreply\.github\.com\Z", re.I)


def _safe_env() -> dict[str, str]:
    # Inherited git overrides, alternate object stores, replacement commits and
    # lazy fetching must not change which local object graph we inspect.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("GIT_", "SSH_"))}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1",
               GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def _git(repo: Path, *args: str) -> bytes:
    cmd = ["git", "-C", str(repo), "--no-pager", "--no-replace-objects",
           "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
           "-c", "credential.helper=", *args]
    completed = subprocess.run(cmd, capture_output=True, timeout=20,
                               env=_safe_env(), check=False)
    if completed.returncode != 0:
        raise ValueError("git_object_or_metadata_unavailable")
    if len(completed.stdout) > 256 * 1024:
        raise ValueError("git_output_too_large")
    return completed.stdout


def _sha(value: object) -> str:
    if not isinstance(value, str) or not COMMIT.fullmatch(value):
        raise ValueError("untrusted_commit_identifier")
    return value.lower()


def _is_noreply(value: str) -> bool:
    return bool(NOREPLY.fullmatch(value.strip()))


def inspect_history(repo: Path, heads: list[str] | None = None, *, all_refs: bool = False) -> dict:
    """Check all ancestors of the supplied heads, never just the new delta.

    all_refs additionally scans all local refs and the worktree HEAD; it is for
    independent full-checkout verification rather than GitHub PR merge refs.
    """
    if heads and all_refs:
        raise ValueError("ambiguous_history_selection")
    if not heads and not all_refs:
        raise ValueError("missing_history_selection")
    if all_refs:
        query = ["rev-list", "--parents", "--all", "HEAD"]
        inspected_heads = 0
    else:
        unique_heads = list(dict.fromkeys(_sha(v) for v in (heads or [])))
        if not unique_heads or len(unique_heads) > 8:
            raise ValueError("invalid_head_count")
        for oid in unique_heads:
            if _git(repo, "cat-file", "-t", oid).strip() != b"commit":
                raise ValueError("head_not_a_commit")
        query = ["rev-list", "--parents", *unique_heads]
        inspected_heads = len(unique_heads)
    lines = _git(repo, *query).decode("ascii", "strict").splitlines()
    if not lines or len(lines) > MAX_COMMITS:
        raise ValueError("empty_or_oversized_history")
    ids: list[str] = []
    roots: list[str] = []
    for line in lines:
        parts = line.split()
        if not parts or not all(COMMIT.fullmatch(s) for s in parts):
            raise ValueError("invalid_git_history_graph")
        ids.append(parts[0].lower())
        if len(parts) == 1:
            roots.append(parts[0].lower())
    # Reject unrelated roots even if they each use no-reply identities; no
    # separate predecessor project is allowed into this clean repository.
    if len(roots) != 1:
        raise ValueError("more_than_one_root_or_incomplete_history")
    findings = []
    for oid in ids:
        raw = _git(repo, "show", "-s", "--format=%ae%n%ce", oid)
        try:
            data = raw.decode("utf-8", "strict").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("commit_identity_unreadable") from exc
        if len(data) != 2:
            raise ValueError("commit_identity_unreadable")
        if not (_is_noreply(data[0]) and _is_noreply(data[1])):
            findings.append(oid[:12])
    return {"status": "PASS" if not findings else "BLOCKED_PERSONAL_EMAIL",
            "commits_examined": len(ids), "heads_examined": inspected_heads,
            "roots_found": len(roots), "flagged_commit_prefixes": findings,
            "historical_exemption": False, "full_email_values_exposed": False}


def _event_heads(repo: Path, event_path: Path) -> list[str]:
    if not event_path.is_file() or event_path.stat().st_size > MAX_EVENT_BYTES:
        raise ValueError("missing_or_oversized_workflow_event")
    event = json.loads(event_path.read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise ValueError("event_must_be_object")
    kind = os.environ.get("GITHUB_EVENT_NAME", "")
    if kind == "pull_request":
        pull = event.get("pull_request")
        if not isinstance(pull, dict):
            raise ValueError("pull_request_missing")
        base, head = pull.get("base"), pull.get("head")
        if not isinstance(base, dict) or not isinstance(head, dict):
            raise ValueError("pr_branches_missing")
        # GitHub's synthetic checkout merge commit need not have the chosen
        # author identity, so inspect the actual PR head and target base.
        return [_sha(base.get("sha")), _sha(head.get("sha"))]
    if kind == "push":
        head = _sha(event.get("after"))
        if head != _sha(os.environ.get("GITHUB_SHA", "")):
            raise ValueError("push_event_head_mismatch")
        local = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
        if local != head:
            raise ValueError("checkout_head_mismatch")
        before = _sha(event.get("before"))
        # Prevent non-fast-forward pushes from silently hiding previously
        # introduced commits; first branch creation has all-zero before.
        if before != "0" * 40:
            _git(repo, "merge-base", "--is-ancestor", before, head)
        return [head]
    if kind == "workflow_dispatch":
        head = _sha(os.environ.get("GITHUB_SHA", ""))
        if _git(repo, "rev-parse", "HEAD").decode("ascii").strip() != head:
            raise ValueError("dispatch_checkout_mismatch")
        return [head]
    raise ValueError("unsupported_event_type")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--repository", required=True, type=Path)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--preflight", action="store_true")
    choice.add_argument("--event", type=Path)
    choice.add_argument("--head")
    choice.add_argument("--all-refs", action="store_true")
    args = parser.parse_args(argv)
    try:
        repo = args.repository.resolve(strict=True)
        if args.preflight:
            address = _git(repo, "config", "--local", "--get", "user.email").decode("utf-8", "strict").strip()
            result = {"status": "PASS" if _is_noreply(address) else "BLOCKED_PERSONAL_EMAIL",
                      "check": "local_git_user_email", "full_email_values_exposed": False}
        elif args.event:
            result = inspect_history(repo, _event_heads(repo, args.event))
        elif args.all_refs:
            result = inspect_history(repo, all_refs=True)
        else:
            result = inspect_history(repo, [_sha(args.head)])
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "PASS" else 3
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError,
            subprocess.TimeoutExpired):
        print(json.dumps({"status": "REFUSED", "reason": "privacy_audit_unavailable_or_invalid",
                          "full_email_values_exposed": False}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

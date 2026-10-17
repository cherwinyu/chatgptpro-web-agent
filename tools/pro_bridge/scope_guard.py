"""Read-only infrastructure scope gate for a dedicated linked Git worktree.

Run before staging/publishing. This is a snapshot, not a lock or authorization
for push/merge/deployment. It includes committed, staged, unstaged and untracked
paths, including both sides of renames. No user worktree is cleaned or reset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import deploy
import process_runtime
import runtime_identity

INFRASTRUCTURE = ("tools/pro_bridge", "adapters/generic", "tests", "docs", "schemas", "reports",
                  "AGENTS.md", "capability-manifest.json", ".github/workflows/audit-offline.yml")


def check(repo: Path, baseline: str, *, expected_head: str | None = None) -> dict:
    result = {"scope_passed": False, "repository_modified": False, "remote_write": False}
    try:
        repo = deploy._safe_location(repo)
        if not isinstance(baseline, str) or not runtime_identity.COMMIT.fullmatch(baseline):
            raise ValueError("immutable baseline required")
        runtime_identity.reject_link(repo / ".git")
        if not (repo / ".git").is_file():
            raise ValueError("a dedicated linked worktree is required")
        def git(*args, check=True):
            return process_runtime.git(list(args), repo=repo, check=check)
        with process_runtime.operation_budget(30):
            top = Path(git("rev-parse", "--show-toplevel").stdout.decode("utf-8").strip()).resolve()
            if top != repo:
                raise ValueError("repository root mismatch")
            head = git("rev-parse", "HEAD").stdout.decode("ascii").strip()
            if expected_head is not None and head != expected_head:
                raise ValueError("candidate head changed")
            if git("merge-base", "--is-ancestor", baseline, head, check=False).returncode:
                raise ValueError("baseline is not an ancestor")
            paths = set()
            for args in (("diff", "--no-renames", "--name-only", "-z", baseline, head, "--"),
                         ("diff", "--no-renames", "--name-only", "-z", "--"),
                         ("diff", "--no-renames", "--cached", "--name-only", "-z", "--"),
                         ("ls-files", "--others", "--exclude-standard", "-z")):
                paths.update(p.decode("utf-8") for p in git(*args).stdout.split(b"\0") if p)
            outside = sorted(p for p in paths if not runtime_identity.in_scope(p, INFRASTRUCTURE))
            result.update(scope_passed=not outside, baseline=baseline, head=head,
                          changed_paths=sorted(paths), outside_scope=outside)
    except (OSError, ValueError, subprocess.SubprocessError):
        result["error"] = "infrastructure_scope_refused"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    report = check(args.repo, args.baseline, expected_head=args.expected_head)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["scope_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

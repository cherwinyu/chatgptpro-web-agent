"""Offline synthetic checks of entire reachable Git ancestry: no legacy exception."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import commit_identity_gate as gate

NOREPLY = "98765+synthetic@users.noreply.github.com"
PERSONAL = "private-person@provider.example"


class CommitIdentityGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="clean-root-privacy-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Synthetic Author")
        self.git("config", "user.email", NOREPLY)
        self.root = self.commit("root", NOREPLY, NOREPLY)

    def git(self, *args, env=None):
        e = dict(os.environ)
        if env:
            e.update(env)
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              check=True, capture_output=True, text=True, env=e).stdout.strip()

    def commit(self, name, author=NOREPLY, committer=NOREPLY):
        with (self.repo/"synthetic.txt").open("a", encoding="utf-8") as f:
            f.write(name + "\n")
        self.git("add", "synthetic.txt")
        env = {"GIT_AUTHOR_EMAIL": author, "GIT_COMMITTER_EMAIL": committer,
               "GIT_AUTHOR_NAME": "Synthetic Author", "GIT_COMMITTER_NAME": "Synthetic Committer"}
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", name, env=env)
        return self.git("rev-parse", "HEAD")

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = gate.main(["--repository", str(self.repo), *args])
        return status, json.loads(output.getvalue())

    def test_clean_root_is_inspected_not_exempted(self):
        report = gate.inspect_history(self.repo, [self.root])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["commits_examined"], 1)
        self.assertEqual(report["roots_found"], 1)
        self.assertFalse(report["historical_exemption"])

    def test_old_personal_root_rejected_even_when_tip_is_clean(self):
        self.git("checkout", "-q", "--orphan", "personalhistory")
        self.git("rm", "-q", "-rf", ".")
        oldroot = self.commit("personal root", PERSONAL, PERSONAL)
        tip = self.commit("later noreply")
        result = gate.inspect_history(self.repo, [tip])
        self.assertEqual(result["status"], "BLOCKED_PERSONAL_EMAIL")
        self.assertIn(oldroot[:12], result["flagged_commit_prefixes"])
        self.assertEqual(result["commits_examined"], 2)
        self.assertNotIn(PERSONAL, json.dumps(result))

    def test_personal_author_and_committer_each_rejected(self):
        a = self.commit("personal author", PERSONAL, NOREPLY)
        ra = gate.inspect_history(self.repo, [a])
        self.assertIn(a[:12], ra["flagged_commit_prefixes"])
        b = self.commit("personal committer", NOREPLY, PERSONAL)
        rb = gate.inspect_history(self.repo, [b])
        self.assertIn(b[:12], rb["flagged_commit_prefixes"])
        self.assertNotIn(PERSONAL, json.dumps(rb))

    def test_clean_multicommit_chain_passes_full_graph(self):
        head = self.commit("next clean")
        out = gate.inspect_history(self.repo, [head])
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(out["commits_examined"], 2)

    def test_new_personal_merge_commit_rejected(self):
        self.git("checkout", "-qb", "feature")
        (self.repo / "feature.txt").write_text("test\n", encoding="utf-8")
        self.git("add", "feature.txt")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "feature")
        self.git("checkout", "-q", "main")
        self.commit("main-side")
        env = {"GIT_AUTHOR_EMAIL": PERSONAL, "GIT_COMMITTER_EMAIL": PERSONAL,
               "GIT_AUTHOR_NAME": "Synthetic", "GIT_COMMITTER_NAME": "Synthetic"}
        self.git("-c", "commit.gpgsign=false", "merge", "--no-ff", "feature", "-m", "bad merge", env=env)
        head = self.git("rev-parse", "HEAD")
        out = gate.inspect_history(self.repo, [head])
        self.assertIn(head[:12], out["flagged_commit_prefixes"])
        self.assertEqual(out["commits_examined"], 4)

    def test_multiple_unrelated_roots_rejected(self):
        self.git("checkout", "-q", "--orphan", "orphan")
        self.git("rm", "-q", "-rf", ".")
        other = self.commit("unrelated")
        with self.assertRaises(ValueError):
            gate.inspect_history(self.repo, [self.root, other])
        rc, output = self.cli("--all-refs")
        self.assertEqual(rc, 3)
        self.assertEqual(output["status"], "REFUSED")

    def test_all_refs_detects_bad_side_branch(self):
        self.git("checkout", "-qb", "bad")
        self.commit("bad", PERSONAL, NOREPLY)
        self.git("checkout", "-q", "main")
        output = gate.inspect_history(self.repo, all_refs=True)
        self.assertEqual(output["status"], "BLOCKED_PERSONAL_EMAIL")

    def test_preflight_reads_local_git_email_without_printing(self):
        code, output = self.cli("--preflight")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.git("config", "user.email", PERSONAL)
        code, output = self.cli("--preflight")
        self.assertEqual(code, 3)
        self.assertNotIn(PERSONAL, json.dumps(output))

    def test_malformed_revision_and_empty_selection_fail_closed(self):
        with self.assertRaises(ValueError):
            gate.inspect_history(self.repo, ["not-a-commit"])
        with self.assertRaises(ValueError):
            gate.inspect_history(self.repo, [])
        code, output = self.cli("--head", "bad input")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")

    def test_push_event_checks_entire_root_and_tip(self):
        head = self.commit("push clean")
        event = self.repo/"event.json"
        event.write_text(json.dumps({"before": self.root, "after": head}), encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": head}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(code, 0)
        self.assertEqual(output["commits_examined"], 2)

    def test_pull_request_event_inspects_both_histories(self):
        self.git("checkout", "-qb", "feature")
        head = self.commit("PR", PERSONAL, NOREPLY)
        event = self.repo/"event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": self.root},
                                                       "head": {"sha": head}}}), encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request",
                                          "GITHUB_SHA": "a" * 40}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(code, 3)
        self.assertIn(head[:12], output["flagged_commit_prefixes"])

    def test_rejects_non_ancestor_push_change(self):
        head = self.commit("second")
        self.git("checkout", "-qb", "other", self.root)
        other = self.commit("divergent")
        self.git("checkout", "-q", "main")
        event = self.repo/"event.json"
        event.write_text(json.dumps({"before": other, "after": head}), encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": head}):
            code, result = self.cli("--event", str(event))
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "REFUSED")


if __name__ == "__main__":
    unittest.main()

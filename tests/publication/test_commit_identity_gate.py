"""Offline synthetic checks of entire reachable Git ancestry: no legacy exception.

The GitHub web committer is a strict name-and-mailbox allowance, not a SHA exemption.
"""
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

    def commit(self, name, author=NOREPLY, committer=NOREPLY, *,
               author_name="Synthetic Author", committer_name="Synthetic Committer"):
        with (self.repo/"synthetic.txt").open("a", encoding="utf-8") as f:
            f.write(name + "\n")
        self.git("add", "synthetic.txt")
        env = {"GIT_AUTHOR_EMAIL": author, "GIT_COMMITTER_EMAIL": committer,
               "GIT_AUTHOR_NAME": author_name, "GIT_COMMITTER_NAME": committer_name}
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", name, env=env)
        return self.git("rev-parse", "HEAD")

    def at_root(self):
        self.git("checkout", "-q", "-B", "case", self.root)

    def assert_blocked(self, sha, *secrets):
        result = gate.inspect_history(self.repo, [sha])
        self.assertEqual(result["status"], "BLOCKED_PERSONAL_EMAIL")
        self.assertIn(sha[:12], result["flagged_commit_prefixes"])
        self.assertFalse(result["full_email_values_exposed"])
        self.assertFalse(result["historical_exemption"])
        blob = json.dumps(result)
        for secret in secrets:
            self.assertNotIn(secret, blob)
        return result

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = gate.main(["--repository", str(self.repo), *args])
        return status, json.loads(output.getvalue())

    def annotated_tag(self, name, target=None):
        args = ["tag", "-a", name, "-m", name]
        if target:
            args.append(target)
        self.git(*args)
        oid = self.git("rev-parse", "refs/tags/" + name)
        self.assertEqual(self.git("cat-file", "-t", oid), "tag")
        return oid

    def push(self, before, after, *, ref="refs/heads/main", github_sha=None,
             github_ref=None, include_event_ref=True):
        # Pin GITHUB_REF so an ambient tag ref in CI cannot reclassify a
        # synthetic branch push and skip the fast-forward check.
        payload = {"before": before, "after": after}
        if include_event_ref:
            payload["ref"] = ref
        event = self.repo / "event.json"
        event.write_text(json.dumps(payload), encoding="utf-8")
        env = {"GITHUB_EVENT_NAME": "push",
               "GITHUB_SHA": after if github_sha is None else github_sha,
               "GITHUB_REF": ref if github_ref is None else github_ref}
        with patch.dict(os.environ, env):
            return self.cli("--event", str(event))

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
        code, output = self.push(self.root, head)
        self.assertEqual(code, 0)
        self.assertEqual(output["commits_examined"], 2)
        code, output = self.push("0" * 40, head)
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")

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
        code, result = self.push(other, head)
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "REFUSED")

    def test_user_noreply_author_and_committer_remain_accepted(self):
        head = self.commit("both user noreply")
        report = gate.inspect_history(self.repo, [head])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["commits_examined"], 2)
        self.assertEqual(report["roots_found"], 1)
        self.assertEqual(report["flagged_commit_prefixes"], [])
        self.assertFalse(report["historical_exemption"])
        code, output = self.cli("--head", head)
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")

    def test_github_web_committer_with_user_noreply_author_is_accepted(self):
        head = self.commit("web squash", NOREPLY, "noreply@github.com", committer_name="GitHub")
        report = gate.inspect_history(self.repo, [head])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["commits_examined"], 2)
        self.assertEqual(report["flagged_commit_prefixes"], [])
        self.assertNotIn("noreply@github.com", json.dumps(report))
        code, output = self.cli("--head", head)
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")

    def test_clean_root_and_multicommit_chain_including_web_committer(self):
        self.commit("second user noreply")
        head = self.commit("third web", NOREPLY, "noreply@github.com", committer_name="GitHub")
        report = gate.inspect_history(self.repo, [head])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["commits_examined"], 3)
        self.assertEqual(report["roots_found"], 1)
        self.assertFalse(report["historical_exemption"])

    def test_pull_request_and_push_events_accept_web_committer(self):
        head = self.commit("event web", NOREPLY, "noreply@github.com", committer_name="GitHub")
        event = self.repo / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": self.root},
                                                       "head": {"sha": head}}}), encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": "c" * 40}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.assertEqual(output["commits_examined"], 2)
        code, output = self.push(self.root, head)
        self.assertEqual(code, 0)
        self.assertEqual(output["commits_examined"], 2)

    def test_spoofed_noreply_domains_rejected(self):
        spoofs = (
            "person@users.noreply.github.com.example",
            "person@users.noreply.github.co",
            "person@users-noreply.github.com",
            "noreply@github.com.example",
            "noreply@github.co",
            "noreply@githubs.com",
            "noreply@GITHUB.com",
            "noreply@github.com.",
            "notgithub@github.com",
            "noreply@users.github.com",
            "users.noreply.github.com@example.com",
            "noreply@github.com@users.noreply.github.com",
        )
        for email in spoofs:
            self.at_root()
            author_bad = self.commit("spoof author", email, NOREPLY)
            self.assert_blocked(author_bad, email)
            self.at_root()
            committer_bad = self.commit("spoof committer", NOREPLY, email, committer_name="GitHub")
            self.assert_blocked(committer_bad, email)

    def test_wrong_committer_name_with_noreply_github_rejected(self):
        # Git trims surrounding whitespace before writing an ident, so padded
        # names are checked on the predicate. History checks use stored names.
        self.assertFalse(gate._is_github_web_committer("noreply@github.com", "GitHub "))
        self.assertFalse(gate._is_github_web_committer("noreply@github.com", " GitHub"))
        self.assertFalse(gate._is_github_web_committer("noreply@github.com", "github"))
        self.assertFalse(gate._is_github_web_committer("noreply@github.com", "GitHub\u200b"))
        self.assertFalse(gate._is_github_web_committer("noreply@GITHUB.com", "GitHub"))
        self.assertFalse(gate._is_github_web_committer("noreply@github.com.example", "GitHub"))
        self.assertTrue(gate._is_github_web_committer("noreply@github.com", "GitHub"))
        self.assertFalse(gate._author_and_committer_allowed(
            "noreply@github.com", "noreply@github.com", "GitHub"))
        names = ("github", "GITHUB", "GitHub Bot", "Git Hub",
                 "github-actions[bot]", "web-flow", "Not GitHub")
        for name in names:
            self.at_root()
            sha = self.commit("wrong name", NOREPLY, "noreply@github.com", committer_name=name)
            self.assert_blocked(sha, "noreply@github.com")

    def test_author_official_noreply_mailbox_rejected(self):
        sha = self.commit("author web mailbox", "noreply@github.com", NOREPLY, author_name="GitHub")
        self.assert_blocked(sha, "noreply@github.com")
        self.at_root()
        both = self.commit("both web mailbox", "noreply@github.com", "noreply@github.com",
                           author_name="GitHub", committer_name="GitHub")
        self.assert_blocked(both, "noreply@github.com")

    def test_personal_author_rejected_even_with_web_committer(self):
        sha = self.commit("personal author web committer", PERSONAL, "noreply@github.com",
                          committer_name="GitHub")
        result = self.assert_blocked(sha, PERSONAL, "noreply@github.com")
        self.assertEqual(result["commits_examined"], 2)

    def test_ancestor_leak_still_flagged_under_web_committer_tip(self):
        old = self.commit("leaked ancestor", PERSONAL, PERSONAL)
        tip = self.commit("later web", NOREPLY, "noreply@github.com", committer_name="GitHub")
        result = gate.inspect_history(self.repo, [tip])
        self.assertEqual(result["status"], "BLOCKED_PERSONAL_EMAIL")
        self.assertIn(old[:12], result["flagged_commit_prefixes"])
        self.assertNotIn(tip[:12], result["flagged_commit_prefixes"])
        self.assertEqual(result["commits_examined"], 3)
        blob = json.dumps(result)
        self.assertNotIn(PERSONAL, blob)
        self.assertNotIn("noreply@github.com", blob)
        self.assertFalse(result["historical_exemption"])

    def test_invalid_graph_and_event_still_refused(self):
        blob = self.git("hash-object", "-w", "synthetic.txt")
        with self.assertRaises(ValueError):
            gate.inspect_history(self.repo, [blob])
        code, output = self.cli("--head", blob)
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")
        self.assertNotIn("@", json.dumps(output))
        event = self.repo / "event.json"
        event.write_text("[]", encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": self.root,
                                          "GITHUB_REF": "refs/heads/main"}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")
        event.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(output["status"], "REFUSED")
        event.write_text("{", encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": self.root,
                                          "GITHUB_REF": "refs/heads/main"}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(output["status"], "REFUSED")
        event.write_text("{}", encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "issues", "GITHUB_SHA": self.root}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(output["status"], "REFUSED")
        event.write_text(json.dumps({"before": "0" * 40, "after": self.root}), encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "b" * 40,
                                          "GITHUB_REF": "refs/heads/main"}):
            code, output = self.cli("--event", str(event))
        self.assertEqual(output["status"], "REFUSED")
        self.assertNotIn(NOREPLY, json.dumps(output))
        self.assertNotIn(PERSONAL, json.dumps(output))

    def test_non_fast_forward_push_still_refused_for_web_committer(self):
        head = self.commit("web tip", NOREPLY, "noreply@github.com", committer_name="GitHub")
        self.git("checkout", "-qb", "other", self.root)
        other = self.commit("divergent")
        self.git("checkout", "-q", "main")
        code, result = self.push(other, head)
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "REFUSED")
        self.assertNotIn("noreply@github.com", json.dumps(result))

    def test_preflight_rejects_web_committer_mailbox(self):
        self.git("config", "user.email", "noreply@github.com")
        code, output = self.cli("--preflight")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "BLOCKED_PERSONAL_EMAIL")
        self.assertNotIn("noreply@github.com", json.dumps(output))

    def test_annotated_tag_push_peels_to_checked_out_commit(self):
        # actions/checkout leaves HEAD on the commit while after/GITHUB_SHA
        # are the annotated tag object.
        head = self.commit("release")
        tag = self.annotated_tag("v0.1.0-rc.3")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("rev-parse", tag + "^{commit}"), head)
        self.assertNotEqual(tag, head)
        code, output = self.push("0" * 40, tag, ref="refs/tags/v0.1.0-rc.3")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.assertEqual(output["commits_examined"], 2)
        self.assertEqual(output["heads_examined"], 1)
        self.assertFalse(output["historical_exemption"])
        self.assertNotIn(NOREPLY, json.dumps(output))
        self.assertNotIn(tag, json.dumps(output))
        # GITHUB_SHA may already be the peeled commit while after is the tag.
        code, output = self.push("0" * 40, tag, ref="refs/tags/v0.1.0-rc.3", github_sha=head)
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        # GITHUB_REF alone is enough when the event payload omits ref.
        code, output = self.push("0" * 40, tag, ref="refs/tags/v0.1.0-rc.3",
                                  include_event_ref=False)
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")

    def test_lightweight_tag_and_branch_push_still_pass(self):
        head = self.commit("lightweight")
        self.git("tag", "vlight", head)
        self.assertEqual(self.git("cat-file", "-t", head), "commit")
        self.assertEqual(self.git("rev-parse", "refs/tags/vlight"), head)
        code, output = self.push(self.root, head, ref="refs/tags/vlight")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.assertEqual(output["commits_examined"], 2)
        code, output = self.push(self.root, head, ref="refs/heads/main")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.assertEqual(output["commits_examined"], 2)
        self.assertNotIn(NOREPLY, json.dumps(output))

    def test_tag_retarget_before_annotated_object_passes(self):
        # Force-moving an annotated tag to a lightweight tag leaves before as
        # the old tag object. That object is not a commit, and checkout may
        # not have retained it. Tag refs skip the ancestor check.
        head = self.commit("retarget")
        old_tag = self.annotated_tag("vmove")
        self.assertNotEqual(old_tag, head)
        self.git("tag", "-d", "vmove")
        self.git("tag", "vmove", head)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.git("cat-file", "-t", old_tag), "tag")
        code, output = self.push(old_tag, head, ref="refs/tags/vmove")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.assertEqual(output["commits_examined"], 2)
        self.assertNotIn(NOREPLY, json.dumps(output))
        missing = "0123456789abcdef0123456789abcdef01234567"
        code, output = self.push(missing, head, ref="refs/tags/vmove")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        code, output = self.push(missing, head, ref="refs/heads/main")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")

    def test_tag_move_skips_fast_forward_check_branch_does_not(self):
        head = self.commit("main tip")
        self.git("checkout", "-qb", "other", self.root)
        other = self.commit("side")
        self.git("checkout", "-q", "main")
        code, output = self.push(other, head, ref="refs/tags/vmove")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.assertEqual(output["commits_examined"], 2)
        code, output = self.push(other, head, ref="refs/heads/main")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")
        self.assertNotIn(NOREPLY, json.dumps(output))

    def test_branch_push_peels_tag_object_before_ancestry_check(self):
        head = self.commit("tip")
        root_tag = self.annotated_tag("old", self.root)
        code, output = self.push(root_tag, head, ref="refs/heads/main")
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "PASS")
        self.git("checkout", "-qb", "side", self.root)
        side = self.commit("divergent side")
        self.git("checkout", "-q", "main")
        side_tag = self.annotated_tag("side-tag", side)
        code, output = self.push(side_tag, head, ref="refs/heads/main")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")

    def test_annotated_tag_push_mismatch_still_refused(self):
        head = self.commit("release")
        tag = self.annotated_tag("v0")
        self.git("checkout", "-qb", "side", self.root)
        other = self.commit("other")
        self.git("checkout", "-q", "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        code, output = self.push("0" * 40, tag, ref="refs/tags/v0", github_sha=other)
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")
        code, output = self.push(self.root, head, ref="refs/tags/v0", github_ref="refs/heads/main")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")
        self.git("checkout", "-q", "side")
        self.assertEqual(self.git("rev-parse", "HEAD"), other)
        code, output = self.push("0" * 40, tag, ref="refs/tags/v0", github_sha=tag)
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")
        self.assertNotIn(NOREPLY, json.dumps(output))

    def test_annotated_tag_push_still_blocks_personal_email(self):
        bad = self.commit("personal tagged", PERSONAL, PERSONAL)
        tag = self.annotated_tag("vbad")
        code, output = self.push("0" * 40, tag, ref="refs/tags/vbad")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "BLOCKED_PERSONAL_EMAIL")
        self.assertIn(bad[:12], output["flagged_commit_prefixes"])
        self.assertEqual(output["commits_examined"], 2)
        self.assertFalse(output["historical_exemption"])
        blob = json.dumps(output)
        self.assertNotIn(PERSONAL, blob)
        self.assertNotIn(NOREPLY, blob)

    def test_tag_object_not_pointing_at_commit_refused(self):
        blob = self.git("hash-object", "-w", "synthetic.txt")
        self.git("tag", "-a", "blobtag", blob, "-m", "blob")
        tag = self.git("rev-parse", "refs/tags/blobtag")
        self.assertEqual(self.git("cat-file", "-t", tag), "tag")
        code, output = self.push("0" * 40, tag, ref="refs/tags/blobtag")
        self.assertEqual(code, 3)
        self.assertEqual(output["status"], "REFUSED")
        self.assertNotIn(NOREPLY, json.dumps(output))


if __name__ == "__main__":
    unittest.main()

"""Raw-byte provenance and whole-directory deployment fault injection tests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/pro_bridge"))
import deploy
import runtime_identity as identity


def git(root, *args):
    return subprocess.run(["git", "--no-pager", "-C", str(root), *args],
                          capture_output=True, check=True, timeout=30).stdout.decode("utf-8").strip()


def init_git(root):
    git(root, "init")
    git(root, "config", "user.name", "Offline Audit")
    git(root, "config", "user.email", "audit@example.invalid")
    git(root, "config", "core.autocrlf", "false")


class ReleaseFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="source-fixture-")
        cls.base = Path(cls.fixture.name)
        cls.repo = cls.base / "repo"
        cls.repo.mkdir()
        shutil.copytree(ROOT / "tools/pro_bridge", cls.repo / "tools/pro_bridge", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "adapters/generic", cls.repo / "adapters/generic", ignore=shutil.ignore_patterns("__pycache__"))
        (cls.repo / "docs").mkdir()
        (cls.repo / "docs/raw.txt").write_bytes(b"raw\r\nbytes\r\n$Format:%H$\r\n")
        (cls.repo / ".gitattributes").write_text("docs/raw.txt -text export-subst\n", encoding="utf-8")
        init_git(cls.repo)
        git(cls.repo, "add", ".")
        git(cls.repo, "commit", "-m", "synthetic release")
        cls.commit = git(cls.repo, "rev-parse", "HEAD")
        cls.original = cls.base / "bundle"
        cls.export = deploy.export_release(cls.repo, cls.commit, cls.original)
        cls.pin = cls.export["manifest_sha256"]

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="deploy-audit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "发布 有空格"
        shutil.copytree(self.original, self.bundle)
        self.destination = self.root / "安装 有空格"

    def old_install(self):
        self.destination.mkdir()
        (self.destination / "old.py").write_bytes(b"old complete installation\r\n")
        (self.destination / "keep.txt").write_bytes(b"backup sentinel")
        return deploy._snapshot(self.destination)


class IdentityTests(ReleaseFixture):
    def test_manifest_without_external_trust_not_confirmed(self):
        value = identity.describe(self.bundle, "")
        self.assertEqual(value["provenance_status"], "UNTRUSTED_MANIFEST")
        self.assertIsNone(value["source_commit"])
        self.assertEqual(value["claimed_source_commit"], self.commit)

    def test_matching_pinned_release_confirmed(self):
        value = identity.verify_release(self.bundle, self.pin)
        self.assertEqual(value["source_commit"], self.commit)
        self.assertEqual(value["runtime_source_digest"], self.export["runtime_source_digest"])

    def test_changed_source_same_head_detected(self):
        init_git(self.bundle)
        git(self.bundle, "add", ".")
        git(self.bundle, "commit", "-m", "host baseline")
        head = git(self.bundle, "rev-parse", "HEAD")
        before = identity.describe(self.bundle, self.pin)
        source = self.bundle / "tools/pro_bridge/cli.py"
        source.write_bytes(source.read_bytes() + b"\n# changed without committing\n")
        after = identity.describe(self.bundle, self.pin)
        self.assertEqual(git(self.bundle, "rev-parse", "HEAD"), head)
        self.assertNotEqual(before["runtime_source_digest"], after["runtime_source_digest"])
        self.assertEqual(after["provenance_status"], "MISMATCH")
        self.assertIsNone(after["source_commit"])

    def test_identical_source_different_host_heads(self):
        second = self.root / "other-host"
        shutil.copytree(self.bundle, second)
        values = []
        for root, message in ((self.bundle, "host one"), (second, "host two")):
            init_git(root)
            git(root, "add", ".")
            git(root, "commit", "-m", message)
            result = subprocess.run([sys.executable, "-B", str(root / "adapters/generic/run_bridge.py"), "identify",
                                     "--trusted-manifest-sha256", self.pin], cwd=root,
                                    capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            values.append(json.loads(result.stdout))
        self.assertNotEqual(values[0]["worktree_git_head"], values[1]["worktree_git_head"])
        self.assertEqual(values[0]["runtime_source_digest"], values[1]["runtime_source_digest"])
        self.assertEqual(values[0]["source_commit"], values[1]["source_commit"])
        self.assertNotEqual(values[0]["source_commit"], values[0]["worktree_git_head"])

    def test_missing_extra_and_residual_source(self):
        path = self.bundle / "tools/pro_bridge/browser_target.py"
        original = path.read_bytes()
        path.unlink()
        self.assertEqual(identity.describe(self.bundle, self.pin)["release_file_check"]["missing"], ["tools/pro_bridge/browser_target.py"])
        path.write_bytes(original)
        for name in ("leftover.py", "leftover.pyc", "leftover.pyd"):
            extra = self.bundle / "tools/pro_bridge" / name
            extra.write_bytes(b"unexpected")
            value = identity.describe(self.bundle, self.pin)
            self.assertIn("tools/pro_bridge/" + name, value["release_file_check"]["extra"])
            self.assertIsNone(value["source_commit"])
            extra.unlink()

    def test_manifest_edit_cannot_self_assert_source_commit(self):
        manifest = self.bundle / identity.MANIFEST
        value = json.loads(manifest.read_bytes())
        value["source_commit"] = "b"*40
        manifest.write_text(json.dumps(value), encoding="utf-8")
        result = identity.describe(self.bundle, self.pin)
        self.assertEqual(result["provenance_status"], "TRUST_ANCHOR_MISMATCH")
        self.assertIsNone(result["source_commit"])

    def test_malformed_traversal_manifest_refused(self):
        path = self.bundle / identity.MANIFEST
        value = json.loads(path.read_bytes())
        value["files"]["tools/pro_bridge/../../escape.py"] = "a"*64
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            identity.verify_release(self.bundle, identity.digest(path.read_bytes()))

    def test_wrong_import_origin_refused(self):
        value = identity.describe(self.bundle, self.pin, module_files={"cli": str(self.root / "wrong/cli.py")})
        self.assertEqual(value["provenance_status"], "MISMATCH")
        self.assertIsNone(value["source_commit"])

    def test_raw_crlf_and_export_subst_bytes_unchanged(self):
        self.assertEqual((self.bundle / "docs/raw.txt").read_bytes(), b"raw\r\nbytes\r\n$Format:%H$\r\n")
        git(self.repo, "config", "core.autocrlf", "true")
        output = self.root / "CRLF export"
        new = deploy.export_release(self.repo, self.commit, output)
        self.assertEqual(new["manifest_sha256"], self.pin)
        self.assertEqual((output / "docs/raw.txt").read_bytes(), (self.bundle / "docs/raw.txt").read_bytes())

    def test_locations_canonicalized_after_link_guard(self):
        self.assertEqual(deploy._safe_location(self.repo / "new-child"),
                         (self.repo / "new-child").resolve())

    def test_export_inside_source_checkout_refused(self):
        with self.assertRaisesRegex(ValueError, "outside the source Git checkout"):
            deploy.export_release(self.repo, self.commit, self.repo / "nested-export")
        self.assertFalse((self.repo / "nested-export").exists())

    def test_uncommitted_checkout_edits_not_published(self):
        source = self.repo / "tools/pro_bridge/cli.py"
        old = source.read_bytes()
        try:
            source.write_bytes(old + b"\n# uncommitted local edit\n")
            exported = deploy.export_release(self.repo, self.commit, self.root / "committed-only")
            self.assertEqual(exported["manifest_sha256"], self.pin)
        finally:
            source.write_bytes(old)


class DeploymentTests(ReleaseFixture):
    def test_new_install_runs_exact_cli_without_ledger(self):
        value = deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True)
        self.assertTrue(value["installed"])
        self.assertEqual(value["runtime_check"]["source_commit"], self.commit)
        self.assertEqual(value["skill_check"]["status"], "NOT_CHECKED")
        self.assertFalse(list(self.destination.rglob("ledger.json")))

    def test_success_replaces_entire_tree_and_keeps_verified_backup(self):
        old = self.old_install()
        value = deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True)
        self.assertTrue(value["installed"])
        self.assertFalse((self.destination / "old.py").exists())
        self.assertEqual(deploy._snapshot(Path(value["backup"])), old)

    def test_faults_at_each_phase_leave_old_bytes(self):
        old = self.old_install()
        for point in ("stage_verified", "backup_verified", "old_moved", "new_moved", "runtime_checked"):
            def fail(phase, paths):
                if phase == point:
                    raise RuntimeError("synthetic failure at " + phase)
            with self.subTest(point=point), self.assertRaises(deploy.DeploymentFailure) as caught:
                deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True, fault_hook=fail)
            self.assertTrue(caught.exception.report["destination_unchanged"])
            self.assertEqual(deploy._snapshot(self.destination), old)
            self.assertFalse((self.root / ("." + self.destination.name + ".deploy.lock")).exists())

    def test_failed_first_install_leaves_no_partial_destination(self):
        def fail(destination, pin):
            raise RuntimeError("synthetic runtime failure")
        with self.assertRaises(deploy.DeploymentFailure):
            deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True, health_check=fail)
        self.assertFalse(self.destination.exists())

    def test_corrupt_stage_and_backup_refused_before_switch(self):
        old = self.old_install()
        for corrupt in ("stage", "backup"):
            def hook(phase, paths):
                if phase == "backup_verified":
                    path = paths[corrupt] / ("tools/pro_bridge/cli.py" if corrupt == "stage" else "old.py")
                    path.write_bytes(b"corrupt")
            with self.subTest(corrupt=corrupt), self.assertRaises(deploy.DeploymentFailure):
                deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True, fault_hook=hook)
            self.assertEqual(deploy._snapshot(self.destination), old)

    def test_missing_quiescence_wrong_pin_and_overlap_refused(self):
        with self.assertRaises(ValueError):
            deploy.install_release(self.bundle, self.destination, self.pin)
        with self.assertRaises(ValueError):
            deploy.install_release(self.bundle, self.destination, "0"*64, quiesced=True)
        with self.assertRaises(ValueError):
            deploy.install_release(self.bundle, self.bundle / "nested", self.pin, quiesced=True)
        self.assertFalse(self.destination.exists())

    def test_protects_git_checkout_and_live_ledger(self):
        self.destination.mkdir()
        for forbidden in (".git", ".pro_bridge", "ledger.json"):
            path = self.destination / forbidden
            if forbidden.endswith(".json"):
                path.write_bytes(b"protected")
            else:
                path.mkdir()
            with self.assertRaises(ValueError):
                deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True)
            self.assertTrue(path.exists())
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()

    def test_matching_installed_skill_checked(self):
        skill = self.root / "SKILL.md"
        skill.write_text('Run this literal entry:\n```text\npython "' + str(self.destination / "adapters/generic/run_bridge.py") + '" <command> --ledger <dir>\n```\n', encoding="utf-8")
        value = deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True, skill=skill)
        self.assertEqual(value["skill_check"]["status"], "MATCH")

    def test_stale_or_variable_skill_refused(self):
        skill = self.root / "SKILL.md"
        for command in ('python "' + str(self.root / "old/adapters/generic/run_bridge.py") + '" route',
                        'python "$BRIDGE/adapters/generic/run_bridge.py" route', 'python $BRIDGE route'):
            skill.write_text(command, encoding="utf-8")
            with self.assertRaises(ValueError):
                deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True, skill=skill)
            self.assertFalse(self.destination.exists())

    def test_other_installer_lock_not_removed(self):
        lock = self.root / ("." + self.destination.name + ".deploy.lock")
        lock.write_bytes(b"another installer")
        with self.assertRaises(FileExistsError):
            deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True)
        self.assertEqual(lock.read_bytes(), b"another installer")

    def test_killed_installer_recovered_from_saved_transaction(self):
        old = self.old_install()
        script = '''import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import deploy
def fault(phase, paths):
    if phase == "old_moved": os._exit(77)
deploy.install_release(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], quiesced=True, fault_hook=fault)
'''
        result = subprocess.run([sys.executable, "-B", "-c", script, str(ROOT / "tools/pro_bridge"),
                                 str(self.bundle), str(self.destination), self.pin], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertFalse(self.destination.exists())
        lock = self.root / ("." + self.destination.name + ".deploy.lock")
        token = json.loads(lock.read_text(encoding="utf-8"))["token"]
        with self.assertRaises((OSError, ValueError)):
            deploy.recover(self.destination, "0"*32, quiesced=True)
        self.assertTrue(lock.exists())
        recovered = deploy.recover(self.destination, token, quiesced=True)
        self.assertTrue(recovered["recovered"])
        self.assertEqual(deploy._snapshot(self.destination), old)
        self.assertFalse(lock.exists())

    def test_symlink_or_junction_destination_refused(self):
        other = self.root / "other-directory"
        other.mkdir()
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(self.destination), str(other)], capture_output=True, check=True)
        else:
            self.destination.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ValueError):
            deploy.install_release(self.bundle, self.destination, self.pin, quiesced=True)
        self.assertEqual(list(other.iterdir()), [])
        if os.name == "nt":
            self.destination.rmdir()
        else:
            self.destination.unlink()


if __name__ == "__main__":
    unittest.main()

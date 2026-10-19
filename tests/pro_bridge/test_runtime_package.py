# -*- coding: utf-8 -*-
"""P10: slim runtime package export + dual-manifest negative cases."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import deploy
import runtime_identity as identity


class RuntimePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[2]
        cls.commit = os.environ.get("PRO_BRIDGE_TEST_COMMIT")
        if not cls.commit:
            import subprocess
            cls.commit = subprocess.check_output(
                ["git", "-C", str(cls.repo), "rev-parse", "HEAD"], text=True
            ).strip()
        cls.root = Path(tempfile.mkdtemp(prefix="p10-runtime-pkg-"))
        cls.bundle = cls.root / "runtime-export"
        cls.export = deploy.export_runtime_release(cls.repo, cls.commit, cls.bundle)
        cls.pin = cls.export["manifest_sha256"]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_runtime_managed_roots_and_licenses(self) -> None:
        man, digest = identity.read_manifest(self.bundle)
        self.assertEqual(man["managed_roots"], list(identity.RUNTIME_PACKAGE_ROOTS))
        self.assertEqual(man.get("package_kind"), "runtime")
        self.assertEqual(digest, self.pin)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md"):
            self.assertIn(name, man["files"])
        # Slim: no tests / CI / publication scripts in managed map
        for path in man["files"]:
            self.assertFalse(path.startswith("tests/"), path)
            self.assertFalse(path.startswith("scripts/"), path)
            self.assertFalse(path.startswith(".github/"), path)
            self.assertNotEqual(path, "docs/MIGRATION_SCOPE.md")

    def test_runtime_verify_with_independent_pin(self) -> None:
        report = identity.verify_release(self.bundle, self.pin)
        self.assertEqual(report["provenance_status"], "VERIFIED")
        self.assertEqual(report["source_commit"], self.commit)

    def test_untrusted_without_pin(self) -> None:
        value = identity.describe(self.bundle, "")
        self.assertEqual(value["provenance_status"], "UNTRUSTED_MANIFEST")

    def test_module_replace_rejected(self) -> None:
        target = self.bundle / "tools" / "pro_bridge" / "cli.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# tamper\n")
            value = identity.describe(self.bundle, self.pin)
            self.assertEqual(value["provenance_status"], "MISMATCH")
            self.assertIn("tools/pro_bridge/cli.py", value["release_file_check"]["modified"])
        finally:
            target.write_bytes(original)

    def test_license_modify_rejected(self) -> None:
        target = self.bundle / "LICENSE"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# notice tamper\n")
            value = identity.describe(self.bundle, self.pin)
            self.assertEqual(value["provenance_status"], "MISMATCH")
            self.assertIn("LICENSE", value["release_file_check"]["modified"])
        finally:
            target.write_bytes(original)

    def test_manifest_missing_rejected(self) -> None:
        path = self.bundle / identity.MANIFEST
        original = path.read_bytes()
        try:
            path.unlink()
            value = identity.describe(self.bundle, self.pin)
            self.assertEqual(value["provenance_status"], "UNMANIFESTED")
        finally:
            path.write_bytes(original)

    def test_manifest_replaced_wrong_pin(self) -> None:
        value = identity.describe(self.bundle, "0" * 64)
        self.assertEqual(value["provenance_status"], "TRUST_ANCHOR_MISMATCH")

    def test_extra_unauthorized_file_rejected(self) -> None:
        extra = self.bundle / "tools" / "pro_bridge" / "evil_extra.py"
        try:
            extra.write_text("# unauthorized\n", encoding="utf-8")
            value = identity.describe(self.bundle, self.pin)
            self.assertEqual(value["provenance_status"], "MISMATCH")
            self.assertIn("tools/pro_bridge/evil_extra.py", value["release_file_check"]["extra"])
        finally:
            if extra.exists():
                extra.unlink()

    def test_release_export_still_defaults(self) -> None:
        out = self.root / "release-export"
        if out.exists():
            shutil.rmtree(out)
        result = deploy.export_release(self.repo, self.commit, out)
        man, _ = identity.read_manifest(out)
        self.assertEqual(man["managed_roots"], list(identity.RELEASE_ROOTS))
        self.assertEqual(man.get("package_kind"), "release")
        identity.verify_release(out, result["manifest_sha256"])
        # release export historically omits root LICENSE from managed_roots
        self.assertNotIn("LICENSE", man["files"])


if __name__ == "__main__":
    unittest.main()

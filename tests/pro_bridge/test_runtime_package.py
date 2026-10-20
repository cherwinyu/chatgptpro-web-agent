# -*- coding: utf-8 -*-
"""P10: slim runtime package export + dual-manifest negative cases."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "pro_bridge"))
import deploy  # noqa: E402
import runtime_identity as identity  # noqa: E402

# Markdown local links: [text](target) — skip http(s), mailto, anchors-only.
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _local_link_targets(text: str) -> list[str]:
    targets: list[str] = []
    for raw in _MD_LINK.findall(text):
        target = raw.strip().split()[0].strip("<>") if raw.strip() else ""
        if not target or target.startswith(("#", "mailto:", "http://", "https://")):
            continue
        # Drop fragment; keep query-less relative path.
        target = target.split("#", 1)[0].split("?", 1)[0]
        if target:
            targets.append(target)
    return targets


class RuntimePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = ROOT
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
        for name in (
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "docs/legal/LICENSE",
            "docs/legal/LICENSE_SCOPE.md",
            "docs/INTEGRATION.md",
            "docs/INTEGRATION.zh-CN.md",
            "docs/ONLINE_WORKFLOW.md",
        ):
            self.assertIn(name, man["files"])
        # Slim: no tests / CI / publication scripts in managed map
        for path in man["files"]:
            self.assertFalse(path.startswith("tests/"), path)
            self.assertFalse(path.startswith("scripts/"), path)
            self.assertFalse(path.startswith(".github/"), path)
            self.assertNotEqual(path, "docs/MIGRATION_SCOPE.md")
        # Still clearly smaller than public Source allowlist (~58)
        self.assertLess(len(man["files"]), 58)
        self.assertGreaterEqual(len(man["files"]), 38)

    def test_runtime_local_doc_links_resolve(self) -> None:
        """Every local-relative markdown link inside Runtime docs must resolve on disk."""
        man, _ = identity.read_manifest(self.bundle)
        md_files = [p for p in man["files"] if p.endswith(".md")]
        self.assertTrue(md_files)
        broken: list[str] = []
        for rel in md_files:
            text = (self.bundle / rel).read_text(encoding="utf-8")
            base = (self.bundle / rel).parent
            for target in _local_link_targets(text):
                resolved = (base / target).resolve()
                try:
                    resolved.relative_to(self.bundle.resolve())
                except ValueError:
                    broken.append(f"{rel} -> {target} (escapes package)")
                    continue
                if not resolved.is_file():
                    broken.append(f"{rel} -> {target}")
        self.assertEqual(broken, [], msg="broken local doc links: " + "; ".join(broken))

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

    def test_wrong_package_kind_rejected(self) -> None:
        """package_kind must match managed_roots; mismatched kind invalidates the manifest."""
        path = self.bundle / identity.MANIFEST
        original = path.read_bytes()
        try:
            man = json.loads(original.decode("utf-8"))
            man["package_kind"] = "release"  # roots remain RUNTIME_PACKAGE_ROOTS
            path.write_bytes(
                json.dumps(man, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            )
            value = identity.describe(self.bundle, self.pin)
            self.assertEqual(value["provenance_status"], "UNREADABLE_OR_INVALID_SOURCE")
        finally:
            path.write_bytes(original)

    def test_legacy_manifest_without_package_kind(self) -> None:
        """Legacy release manifests omit package_kind and must keep RELEASE_ROOTS."""
        out = self.root / "legacy-kind-check"
        if out.exists():
            shutil.rmtree(out)
        result = deploy.export_release(self.repo, self.commit, out)
        path = out / identity.MANIFEST
        man = json.loads(path.read_bytes().decode("utf-8"))
        self.assertEqual(man.get("package_kind"), "release")
        del man["package_kind"]
        raw = json.dumps(man, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        path.write_bytes(raw)
        pin = identity.digest(raw)
        # Legacy RELEASE_ROOTS without package_kind remains readable/verifiable.
        report = identity.verify_release(out, pin)
        self.assertEqual(report["provenance_status"], "VERIFIED")
        # But legacy + RUNTIME roots without kind must be rejected.
        man["managed_roots"] = list(identity.RUNTIME_PACKAGE_ROOTS)
        # Keep files keys that are still in scope to isolate the header check.
        man["files"] = {k: v for k, v in man["files"].items() if identity.in_scope(k, identity.RUNTIME_PACKAGE_ROOTS)}
        man["runtime_source_digest"] = identity.mapping_digest(
            {p: h for p, h in man["files"].items() if identity.in_scope(p, identity.RUNTIME_ROOTS)}
        )
        path.write_bytes(json.dumps(man, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        value = identity.describe(out, identity.digest(path.read_bytes()))
        self.assertEqual(value["provenance_status"], "UNREADABLE_OR_INVALID_SOURCE")

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
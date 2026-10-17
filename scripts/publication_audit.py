"""Fail-closed scope/privacy pattern gate for a curated source preview.

No network access, credentials, external services or source modification. A green
result proves compliance with these explicit rules only, never legal clearance.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import sys
import stat

BLOCKED_PREFIXES = (".cursor/", "context/", "reports/", "docs/public-readiness/", ".pro_bridge/", ".productization-transport/")
BLOCKED_BASENAMES = {"AGENTS.md", "release-manifest.json"}
PRIVATE_TERMS = re.compile(r"\bG0[2-6](?:\.\d+)?\b|\bProject(?:Shoe)\b", re.I)
PRIVATE_PERSONAL_MAILBOX = re.compile(r"\b[A-Za-z0-9_.+%-]+@(?:gmail|outlook|hotmail|icloud|163|qq|protonmail)\.[a-z]{2,}\b", re.I)
COMMON_SECRETS = (
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{40,255})\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,255}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9_-]{16,}"),
)
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
OFFICIAL_LICENSE_SHA256 = "c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5"
SKIP_DIRS = {".git"}


def check(root: Path) -> dict:
    root = root.resolve()
    result = {"schema": 1, "status": "REFUSED", "release_approved": False,
              "online_automation_verified": False, "source_items": 0, "checked_bytes": 0,
              "rule_violations": {}, "third_party_rights_cleared": False,
              "brand_permission_verified": False}
    violations: dict[str, int] = {}
    def fail(category: str) -> None:
        violations[category] = violations.get(category, 0) + 1
    try:
        allowlist = (root / "PUBLIC_FILELIST.txt").read_text(encoding="utf-8")
        names = allowlist.splitlines()
        if not names or names != sorted(set(names)) or "PUBLIC_FILELIST.txt" not in names:
            fail("filelist_invalid")
        found = []
        folded = set()
        total = 0
        for path in sorted(root.rglob("*")):
            name = path.relative_to(root).as_posix()
            if ".git" in path.relative_to(root).parts:
                continue
            if path.is_symlink():
                fail("symlink_present"); continue
            if path.is_dir():
                continue
            found.append(name)
            if (name in BLOCKED_BASENAMES or path.name in BLOCKED_BASENAMES
                    or any(name.startswith(p) for p in BLOCKED_PREFIXES)
                    or "__pycache__" in name.split("/") or path.suffix.lower() in {".pyc", ".pyo"}):
                fail("forbidden_path")
            if name.casefold() in folded:
                fail("path_collision")
            folded.add(name.casefold())
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                fail("non_regular_file"); continue
            if info.st_size > MAX_FILE or total + info.st_size > MAX_TOTAL:
                fail("file_size_limit");continue
            data = path.read_bytes();total += len(data)
            try: text = data.decode("utf-8")
            except UnicodeDecodeError:
                fail("non_utf8_file");continue
            if "\0" in text:
                fail("binary_file");continue
            if PRIVATE_TERMS.search(text): fail("private_internal_reference")
            if PRIVATE_PERSONAL_MAILBOX.search(text): fail("personal_mailbox")
            if re.search(r"(?i)(?:[A-Z]:[/\\]Users[/\\]|/(?:Users|home)/)[^\s\"'<>]+", text):
                fail("user_home_path")
            for pat in COMMON_SECRETS:
                if pat.search(text):fail("secret_shape")
            for match in re.finditer(r"https://(?:chatgpt\.com|chat\.openai\.com)/(?:c|g)/[^\s\"'<>]+", text):
                if not match.group(0).endswith("/synthetic"):
                    fail("conversation_url")
        if sorted(found) != names:
            fail("filelist_mismatch")
        for name in ("LICENSE", "docs/legal/LICENSE"):
            obj = root/name
            if not obj.is_file() or hashlib.sha256(obj.read_bytes()).hexdigest() != OFFICIAL_LICENSE_SHA256:
                fail("license_bytes")
        if (root/"LICENSE").is_file() and (root/"docs/legal/LICENSE").is_file():
            if (root/"LICENSE").read_bytes() != (root/"docs/legal/LICENSE").read_bytes():
                fail("license_mirrors")
        result.update(source_items=len(found), checked_bytes=total, rule_violations=violations,
                      status="RULE_CHECKS_PASSED" if not violations else "REFUSED")
    except (OSError, ValueError, RuntimeError):
        fail("scan_io_error")
        result["rule_violations"] = violations
    return result


def main() -> int:
    report = check(Path(__file__).resolve().parents[1])
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "RULE_CHECKS_PASSED" else 3


if __name__ == "__main__":
    raise SystemExit(main())

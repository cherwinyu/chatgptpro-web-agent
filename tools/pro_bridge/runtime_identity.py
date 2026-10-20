"""Byte-level release identity, independent of an enclosing workspace's HEAD.

A nearby manifest is a claim, not a trust anchor. VERIFIED requires its SHA-256
from an independently trusted caller/environment and an exact managed file set.
This diagnoses source files on disk; it is not code-signing or memory attestation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import process_runtime
import time
from pathlib import Path, PurePosixPath

MANIFEST = "release-manifest.json"
TRUST_ENV = "PRO_BRIDGE_TRUSTED_MANIFEST_SHA256"
RUNTIME_ROOTS = ("tools/pro_bridge", "adapters/generic")
RELEASE_ROOTS = (
    "tools/pro_bridge", "adapters", ".cursor/skills/chatgpt-pro-dispatch", "docs", "schemas",
    "context", "config", "tests", "reports", "README.md", "README.zh-CN.md", "AGENTS.md",
    "capability-manifest.json",
)
# Slim Runtime package scope: run modules + licenses/notices + minimal user docs +
# preview schemas/config. Excludes tests, CI, publication scripts, migration meta.
RUNTIME_PACKAGE_ROOTS = (
    "tools/pro_bridge", "adapters/generic", "schemas", "config",
    "capability-manifest.json",
    "LICENSE", "THIRD_PARTY_NOTICES.md", "SECURITY.md",
    "README.md", "README.zh-CN.md", "CONTRIBUTING.md",
    "docs/QUICKSTART.md", "docs/QUICKSTART.zh-CN.md",
    "docs/RESEARCH_USE.md", "docs/RESEARCH_USE.zh-CN.md",
    "docs/legal/LICENSE", "docs/legal/LICENSE_SCOPE.md",
)
ALLOWED_MANAGED_ROOTS = (RELEASE_ROOTS, RUNTIME_PACKAGE_ROOTS)
PACKAGE_KIND_ROOTS = {
    "release": RELEASE_ROOTS,
    "runtime": RUNTIME_PACKAGE_ROOTS,
}
REQUIRED = ("tools/pro_bridge/cli.py", "tools/pro_bridge/bridge_v2.py",
            "tools/pro_bridge/git_verify.py", "tools/pro_bridge/browser_target.py",
            "tools/pro_bridge/runtime_identity.py", "adapters/generic/run_bridge.py")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def mapping_digest(files: dict[str, str]) -> str:
    return digest(json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def managed_roots_for(package_kind: str = "release") -> tuple[str, ...]:
    try:
        return PACKAGE_KIND_ROOTS[package_kind]
    except KeyError as exc:
        raise ValueError("package_kind must be 'release' or 'runtime'") from exc


def allowed_managed_roots(value: object) -> bool:
    return value in (list(RELEASE_ROOTS), list(RUNTIME_PACKAGE_ROOTS))


def consistent_package_kind(manifest: dict) -> bool:
    """package_kind must match managed_roots; legacy (no kind) => RELEASE_ROOTS only."""
    roots = manifest.get("managed_roots")
    kind = manifest.get("package_kind")
    if kind is None:
        return roots == list(RELEASE_ROOTS)
    return isinstance(kind, str) and kind in PACKAGE_KIND_ROOTS and roots == list(PACKAGE_KIND_ROOTS[kind])



def in_scope(name: str, scopes: tuple[str, ...] = RELEASE_ROOTS) -> bool:
    return any(name == scope or name.startswith(scope + "/") for scope in scopes)


def safe_name(name: object) -> bool:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    path = PurePosixPath(name)
    parts = name.split("/")
    return (not path.is_absolute() and all(part.casefold() not in ("", ".", "..", ".git", ".pro_bridge", "__pycache__")
            and part.rstrip(" .") == part
            and not any(ord(c) < 32 or ord(c) == 127 for c in part)
            and part.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
            for part in parts))


def reject_link(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("symlink or reparse point: " + str(path))


def inventory(root: Path, scopes: tuple[str, ...] = RELEASE_ROOTS) -> dict[str, str]:
    """Hash every managed regular file; ignore only generated __pycache__ trees."""
    root = Path(root)
    files: dict[str, str] = {}
    folded: set[str] = set()

    def visit(path: Path) -> None:
        reject_link(path)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.name == "__pycache__":
                    reject_link(child)  # Ignored cache must not be a junction.
                    continue
                visit(child)
        elif path.is_file():
            name = path.relative_to(root).as_posix()
            if not safe_name(name) or name.casefold() in folded:
                raise ValueError("unsafe or case-colliding release path: " + name)
            folded.add(name.casefold())
            files[name] = digest(path.read_bytes())
        else:
            raise ValueError("non-regular managed entry: " + str(path))

    for scope in scopes:
        entry = root / scope
        # lexists semantics: a dangling symlink must also be refused.
        if entry.exists() or entry.is_symlink():
            current = entry
            while current != root:
                reject_link(current)
                current = current.parent
            visit(entry)
    return dict(sorted(files.items()))


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest field: " + key)
        result[key] = value
    return result


def read_manifest(root: Path) -> tuple[dict, str]:
    path = root / MANIFEST
    reject_link(path)
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("release manifest too large")
    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1 or not allowed_managed_roots(manifest.get("managed_roots")) or not consistent_package_kind(manifest)
            or manifest.get("runtime_roots") != list(RUNTIME_ROOTS)
            or not isinstance(manifest.get("source_commit"), str)
            or not COMMIT.fullmatch(manifest["source_commit"])):
        raise ValueError("invalid release manifest header or scope")
    managed = tuple(manifest["managed_roots"])
    files = manifest.get("files")
    if not isinstance(files, dict) or not all(key in files for key in REQUIRED):
        raise ValueError("release manifest omits required source files")
    folded = set()
    for name, value in files.items():
        if (not safe_name(name) or not in_scope(name, managed) or name.casefold() in folded
                or not isinstance(value, str) or not HEX64.fullmatch(value)):
            raise ValueError("invalid release file entry: " + str(name))
        folded.add(name.casefold())
    expected_runtime = mapping_digest({p: h for p, h in files.items() if in_scope(p, RUNTIME_ROOTS)})
    if manifest.get("runtime_source_digest") != expected_runtime:
        raise ValueError("manifest runtime digest is inconsistent")
    return manifest, digest(raw)


def describe(root: Path, trusted_manifest_sha256: str | None = None, *, module_files: dict | None = None) -> dict:
    root = Path(root).resolve()
    result = {
        "runtime_source_root": str(root), "runtime_source_digest": None,
        "runtime_digest_scope": list(RUNTIME_ROOTS), "source_commit": None,
        "claimed_source_commit": None, "provenance_status": "UNMANIFESTED",
        "source_manifest_sha256": None, "source_manifest_path": str(root / MANIFEST),
        "provenance_checked_at": time.time(), "provenance_errors": [],
        "source_identity_is_memory_attestation": False,
    }
    try:
        runtime = inventory(root, RUNTIME_ROOTS)
        result["runtime_source_digest"] = mapping_digest(runtime)
        module_errors = []
        if module_files is not None:
            result["runtime_module_files"] = module_files
            for name, value in module_files.items():
                expected = root / "tools" / "pro_bridge" / (name + ".py")
                if not value or Path(value).resolve() != expected:
                    module_errors.append({"field": "module_file", "module": name,
                                          "provided": value, "actual": str(expected)})
        if not (root / MANIFEST).exists():
            if module_errors:
                result.update(provenance_status="MISMATCH", provenance_errors=module_errors)
            return result
        manifest, manifest_hash = read_manifest(root)
        result["claimed_source_commit"] = manifest["source_commit"]
        result["source_manifest_sha256"] = manifest_hash
        result["managed_roots"] = list(manifest["managed_roots"])
        actual = inventory(root, tuple(manifest["managed_roots"]))
        expected = manifest["files"]
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        modified = sorted(p for p in expected.keys() & actual.keys() if expected[p] != actual[p])
        result["release_file_check"] = {"missing": missing, "extra": extra, "modified": modified}
        trust = trusted_manifest_sha256 if trusted_manifest_sha256 is not None else os.environ.get(TRUST_ENV)
        if missing or extra or modified or module_errors:
            result.update(provenance_status="MISMATCH", provenance_errors=module_errors)
        elif not trust:
            result["provenance_status"] = "UNTRUSTED_MANIFEST"
        elif not HEX64.fullmatch(trust) or trust != manifest_hash:
            result["provenance_status"] = "TRUST_ANCHOR_MISMATCH"
        else:
            result.update(provenance_status="VERIFIED", source_commit=manifest["source_commit"])
    except (OSError, ValueError, RecursionError) as exc:
        result["provenance_status"] = "UNREADABLE_OR_INVALID_SOURCE"
        result["provenance_errors"].append(str(exc))
    return result


def verify_release(root: Path, trusted_manifest_sha256: str) -> dict:
    report = describe(root, trusted_manifest_sha256)
    if report["provenance_status"] != "VERIFIED":
        raise ValueError("release verification failed: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


def worktree_head(root: Path) -> str:
    """Fresh enclosing-worktree observation; not a source provenance claim."""
    try:
        result = process_runtime.git(["rev-parse", "HEAD"], repo=root, timeout=3, check=False)
        value = result.stdout.decode("ascii").strip()
        return value if result.returncode == 0 and COMMIT.fullmatch(value) else "NOT_AVAILABLE"
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return "NOT_AVAILABLE"

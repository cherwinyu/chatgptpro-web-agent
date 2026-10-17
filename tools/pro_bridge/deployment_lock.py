"""Operator-pinned deployment plan and read-only launch preflight.

This is separate from deploy.py's per-transaction lock. It never creates a
ledger or sends a task. A lock's own hash must be approved outside the lock.
Run this file from a retained reviewed checkout if the installation is absent.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import deploy
import process_runtime
import runtime_identity as identity

LOCK_ENV = "PRO_BRIDGE_DEPLOYMENT_LOCK"
PIN_ENV = "PRO_BRIDGE_DEPLOYMENT_LOCK_SHA256"
MAX_LOCK_BYTES = 1024 * 1024


def _json_new(path: Path, data: dict) -> str:
    path = deploy._safe_location(path)
    raw = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    # Parent must already exist. Never overwrite approval or historical evidence.
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return identity.digest(raw)


def _file_hash(path: Path) -> str:
    path = deploy._safe_location(path)
    identity.reject_link(path)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("adapter must be a private regular single-link file")
    return identity.digest(path.read_bytes())


def load(path: Path, pin: str) -> dict:
    path = deploy._safe_location(path)
    if not isinstance(pin, str) or not identity.HEX64.fullmatch(pin):
        raise ValueError("independently approved deployment lock SHA-256 required")
    identity.reject_link(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_LOCK_BYTES + 1)
    if len(raw) > MAX_LOCK_BYTES or identity.digest(raw) != pin:
        raise ValueError("deployment lock byte hash mismatch")
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=identity._unique)
    fields = {"schema_version", "destination", "source_commit", "upstream_commit", "patch_sha256",
              "manifest_sha256", "runtime_source_digest", "entrypoint", "line_endings", "environment",
              "host_adapters", "rollback"}
    if not isinstance(data, dict) or set(data) != fields or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("invalid deployment lock schema")
    for key in ("source_commit", "upstream_commit"):
        if not isinstance(data[key], str) or not identity.COMMIT.fullmatch(data[key]):
            raise ValueError("invalid deployment commit")
    for key in ("manifest_sha256", "runtime_source_digest"):
        if not isinstance(data[key], str) or not identity.HEX64.fullmatch(data[key]):
            raise ValueError("invalid deployment digest")
    if data["patch_sha256"] is not None and (not isinstance(data["patch_sha256"], str) or not identity.HEX64.fullmatch(data["patch_sha256"])):
        raise ValueError("invalid informational patch digest")
    if data["entrypoint"] != "adapters/generic/run_bridge.py" or data["line_endings"] != "committed_raw_bytes":
        raise ValueError("unsupported entrypoint or line-ending policy")
    if data["environment"] != {"os": "Windows", "python": [3, 12], "git_min": list(process_runtime.MIN_GIT)}:
        raise ValueError("unsupported deployment environment policy")
    destination = _absolute(data["destination"])
    if path == destination or destination in path.parents:
        raise ValueError("deployment lock must be retained outside the installation")
    adapters = data["host_adapters"]
    if not isinstance(adapters, list) or len(adapters) > 32:
        raise ValueError("invalid host adapter inventory")
    seen = set()
    for adapter in adapters:
        if not isinstance(adapter, dict) or set(adapter) != {"path", "sha256"}:
            raise ValueError("invalid adapter entry")
        target = _absolute(adapter["path"])
        if target in seen or target == destination or destination in target.parents:
            raise ValueError("adapters must be unique external files")
        seen.add(target)
        if not isinstance(adapter["sha256"], str) or not identity.HEX64.fullmatch(adapter["sha256"]):
            raise ValueError("invalid adapter hash")
    rollback = data["rollback"]
    if rollback is not None:
        if not isinstance(rollback, dict) or set(rollback) != {"bundle", "manifest_sha256", "source_commit"}:
            raise ValueError("invalid rollback descriptor")
        old = _absolute(rollback["bundle"])
        if old == destination or destination in old.parents:
            raise ValueError("rollback bundle must be retained outside destination")
        if not isinstance(rollback["manifest_sha256"], str) or not identity.HEX64.fullmatch(rollback["manifest_sha256"]):
            raise ValueError("invalid rollback pin")
        if not isinstance(rollback["source_commit"], str) or not identity.COMMIT.fullmatch(rollback["source_commit"]):
            raise ValueError("invalid rollback commit")
    return data


def _absolute(value) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("deployment paths must be absolute")
    return deploy._safe_location(Path(value))


def create(bundle: Path, destination: Path, manifest_pin: str, upstream_commit: str, output: Path,
           *, adapters=(), patch: Path | None = None, rollback_bundle: Path | None = None,
           rollback_pin: str | None = None) -> dict:
    bundle, destination, output = map(deploy._safe_location, (bundle, destination, output))
    if not isinstance(upstream_commit, str) or not identity.COMMIT.fullmatch(upstream_commit):
        raise ValueError("full upstream commit required")
    if any(output == root or root in output.parents for root in (bundle, destination)):
        raise ValueError("lock must be outside bundle and destination")
    verified = identity.verify_release(bundle, manifest_pin)
    rollback = None
    if (rollback_bundle is None) != (rollback_pin is None):
        raise ValueError("rollback bundle and independent pin must be paired")
    if rollback_bundle is not None:
        rollback_bundle = deploy._safe_location(rollback_bundle)
        old = identity.verify_release(rollback_bundle, rollback_pin)
        rollback = {"bundle": str(rollback_bundle), "manifest_sha256": rollback_pin, "source_commit": old["source_commit"]}
    if len(adapters) > 32:
        raise ValueError("too many adapters")
    if rollback is not None and (Path(rollback["bundle"]) == destination or destination in Path(rollback["bundle"]).parents):
        raise ValueError("rollback must be retained outside the installation")
    adapter_entries = [{"path": str(deploy._safe_location(Path(path))), "sha256": _file_hash(Path(path))} for path in adapters]
    data = {"schema_version": 1, "destination": str(destination), "source_commit": verified["source_commit"],
            "upstream_commit": upstream_commit, "patch_sha256": _file_hash(patch) if patch else None,
            "manifest_sha256": manifest_pin, "runtime_source_digest": verified["runtime_source_digest"],
            "entrypoint": "adapters/generic/run_bridge.py", "line_endings": "committed_raw_bytes",
            "environment": {"os": "Windows", "python": [3, 12], "git_min": list(process_runtime.MIN_GIT)},
            "host_adapters": adapter_entries, "rollback": rollback}
    # Reject self-references and bad external inventories before creating output.
    for adapter in adapter_entries:
        target = Path(adapter["path"])
        if target == output or target == destination or destination in target.parents:
            raise ValueError("adapter must be outside destination and not the deployment lock")
    if len({a["path"] for a in adapter_entries}) != len(adapter_entries):
        raise ValueError("duplicate adapter")
    pin = _json_new(output, data)
    return {"created": True, "lock_sha256": pin, "source_commit": data["source_commit"],
            "trust_instruction": "Independently review and approve the lock bytes and hash before use.",
            "deployment_performed": False, "web_send_count": 0}


def _adapters(data: dict) -> bool:
    return all(_file_hash(Path(item["path"])) == item["sha256"] for item in data["host_adapters"])


def preflight(path: Path, pin: str, *, expected_root: Path | None = None) -> dict:
    result = {"preflight_passed": False, "deployment_performed": False, "ledger_touched": False,
              "send_authorized": False, "web_send_count": 0, "checks": {}}
    try:
        data = load(path, pin)
        root = _absolute(data["destination"])
        result["checks"]["selected_installation"] = expected_root is None or root == deploy._safe_location(expected_root)
        identity_report = identity.describe(root, data["manifest_sha256"])
        result["checks"]["release"] = identity_report["provenance_status"] == "VERIFIED"
        result["checks"]["source_commit"] = identity_report["source_commit"] == data["source_commit"]
        result["checks"]["runtime_digest"] = identity_report["runtime_source_digest"] == data["runtime_source_digest"]
        result["checks"]["adapters"] = _adapters(data)
        transaction = root.parent / ("." + root.name + ".deploy.lock")
        result["checks"]["deployment_transaction_clear"] = not (transaction.exists() or transaction.is_symlink())
        version = process_runtime.git(["--version"], timeout=3).stdout.decode("ascii", "replace")
        import re
        match = re.match(r"git version (\d+)\.(\d+)\.(\d+)", version)
        result["checks"]["git"] = bool(match and tuple(map(int, match.groups())) >= process_runtime.MIN_GIT)
        result["checks"]["python"] = list(sys.version_info[:2]) == data["environment"]["python"]
        result["checks"]["os"] = platform.system() == data["environment"]["os"]
        result["preflight_passed"] = all(result["checks"].values())
        result["provenance_status"] = identity_report["provenance_status"]
        result["manifest_sha256"] = data["manifest_sha256"]
        result["source_commit"] = identity_report["source_commit"]
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        result["error"] = "deployment_preflight_refused"
    return result


def apply(path: Path, pin: str, bundle: Path, *, quiesced=False, skill=None) -> dict:
    if not quiesced:
        raise ValueError("stop installation users and explicitly confirm quiescence")
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise ValueError("deployment apply supports Windows/Python 3.12 only")
    data = load(path, pin)
    verified = identity.verify_release(bundle, data["manifest_sha256"])
    if verified["source_commit"] != data["source_commit"] or verified["runtime_source_digest"] != data["runtime_source_digest"] or not _adapters(data):
        raise ValueError("locked source or adapter differs; no deployment")
    def locked_health(destination, manifest_pin):
        report = deploy.runtime_check(destination, manifest_pin)
        if not _adapters(data):
            raise ValueError("host adapter changed during installation")
        return report
    return deploy.install_release(bundle, Path(data["destination"]), data["manifest_sha256"],
                                  quiesced=quiesced, skill=skill, health_check=locked_health)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    subs = parser.add_subparsers(dest="command", required=True)
    make = subs.add_parser("create", allow_abbrev=False)
    for flag in ("bundle", "destination", "output"):
        make.add_argument("--" + flag, type=Path, required=True)
    make.add_argument("--manifest-sha256", required=True)
    make.add_argument("--upstream-commit", required=True)
    make.add_argument("--adapter", action="append", default=[], type=Path)
    make.add_argument("--patch", type=Path)
    make.add_argument("--rollback-bundle", type=Path)
    make.add_argument("--rollback-pin")
    for command in ("check", "apply", "recover"):
        sub = subs.add_parser(command, allow_abbrev=False)
        sub.add_argument("--lock", type=Path, required=True)
        sub.add_argument("--lock-sha256", required=True)
        if command != "check":
            sub.add_argument("--quiesced", action="store_true")
        if command == "apply":
            sub.add_argument("--bundle", type=Path, required=True)
            selection = sub.add_mutually_exclusive_group(required=True)
            selection.add_argument("--skill", type=Path)
            selection.add_argument("--no-skill", action="store_true")
        if command == "recover":
            sub.add_argument("--transaction-token", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            report = create(args.bundle, args.destination, args.manifest_sha256, args.upstream_commit, args.output,
                adapters=args.adapter, patch=args.patch, rollback_bundle=args.rollback_bundle, rollback_pin=args.rollback_pin)
        elif args.command == "check":
            report = preflight(args.lock, args.lock_sha256)
        elif args.command == "apply":
            report = apply(args.lock, args.lock_sha256, args.bundle, quiesced=args.quiesced, skill=args.skill)
        else:
            data = load(args.lock, args.lock_sha256)
            report = deploy.recover(Path(data["destination"]), args.transaction_token, quiesced=args.quiesced)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report.get("preflight_passed", True) else 2
    except deploy.DeploymentFailure as exc:
        print(json.dumps({**exc.report, "web_send_count": 0}, ensure_ascii=False, sort_keys=True))
        return 2
    except (OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError):
        print(json.dumps({"error": "deployment_operation_refused", "web_send_count": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

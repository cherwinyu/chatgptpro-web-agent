"""Explicit-ledger launcher for the copied Pro bridge CLI.

This wrapper does not contact a browser, a webhook, or GitHub by itself.
Legacy ledger commands require --ledger or PRO_BRIDGE_LEDGER. Inspect requires an explicit ledger.
Demo/doctor ignore the fallback ledger; demo owns a new temporary fixture.
The underlying CLI
walks parent directories for a .git folder and would otherwise select that
repository's .pro_bridge/tasks directory.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools" / "pro_bridge" / "cli.py"
PROTECTED = "PRO_BRIDGE_PROTECTED_LEDGER"
STATELESS = {"route", "check-target", "check-selection", "identify", "demo", "doctor", "preview-permissions"}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: python adapters/generic/run_bridge.py <cli-command> --ledger <disposable-dir> [flags]\n"
            "Legacy ledger commands require --ledger or PRO_BRIDGE_LEDGER; inspect requires explicit --ledger. Demo/doctor need no ledger. No retries or browser sends.",
            file=sys.stderr,
        )
        return 2
    options = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    options.add_argument("--ledger", action="append")
    try:
        parsed, remaining = options.parse_known_args(args[1:])
    except SystemExit:
        return 2
    if parsed.ledger and len(parsed.ledger) != 1:
        print("refusing to start: pass --ledger only once", file=sys.stderr)
        return 2
    if args[0] == "demo" and parsed.ledger:
        print("demo refuses --ledger; it creates and cleans its own offline fixture", file=sys.stderr)
        return 2
    if args[0] == "inspect" and not parsed.ledger:
        print("inspect requires an explicit --ledger; environment fallback is ignored", file=sys.stderr)
        return 2
    ledger = parsed.ledger[0] if parsed.ledger else (None if args[0] in STATELESS else os.environ.get("PRO_BRIDGE_LEDGER"))
    if args[0] not in STATELESS and (ledger is None or not ledger.strip()):
        print(
            "refusing to start: pass --ledger <disposable-directory> or set PRO_BRIDGE_LEDGER. "
            "A missing flag must not fall through to a parent git repository.",
            file=sys.stderr,
        )
        return 2
    if parsed.ledger and not parsed.ledger[0].strip():
        print("refusing to start: --ledger cannot be empty", file=sys.stderr)
        return 2
    # Parse once, check once, and forward precisely the checked absolute path.
    resolved = Path(ledger).resolve() if ledger and ledger.strip() else None
    protected = os.environ.get(PROTECTED, "")
    if protected and resolved == Path(protected).resolve():
        print("refusing to start: ledger matches PRO_BRIDGE_PROTECTED_LEDGER", file=sys.stderr)
        return 2
    if not CLI.is_file():
        print("bridge CLI is missing: " + str(CLI), file=sys.stderr)
        return 2
    forwarded = [args[0], *remaining]
    if args[0] not in STATELESS or (args[0] in {"doctor", "preview-permissions"} and resolved is not None):
        checked_path = Path(os.path.abspath(ledger)) if args[0] in {"doctor", "inspect", "preview-permissions"} else resolved
        forwarded.extend(["--ledger", str(checked_path)])
    environment = dict(os.environ)
    lock_path = environment.get("PRO_BRIDGE_DEPLOYMENT_LOCK")
    lock_pin = environment.get("PRO_BRIDGE_DEPLOYMENT_LOCK_SHA256")
    if lock_path is not None or lock_pin is not None:
        # Explicit opt-in: both fields are mandatory, never learn a trust pin
        # from a colocated file or silently fall back after preflight failure.
        if not lock_path or not lock_pin:
            print("deployment lock path and independent pin must be paired", file=sys.stderr)
            return 2
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(ROOT / "tools/pro_bridge"))
        import deployment_lock
        report = deployment_lock.preflight(Path(lock_path), lock_pin, expected_root=ROOT)
        if not report["preflight_passed"]:
            print("deployment preflight refused; no CLI or task started", file=sys.stderr)
            return 2
        environment["PRO_BRIDGE_TRUSTED_MANIFEST_SHA256"] = report["manifest_sha256"]
    entrypoint = CLI
    if args[0] == "preview-permissions":
        entrypoint = ROOT / "tools/pro_bridge/permission_preview.py"
        forwarded = forwarded[1:]
        if not entrypoint.is_file():
            print("permission preview entrypoint is missing", file=sys.stderr)
            return 2
    completed = subprocess.run([sys.executable, "-B", "-X", "utf8", str(entrypoint), *forwarded], check=False, env=environment)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

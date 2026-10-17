"""Read-only permission preview; never creates a ledger or grants a send.

Local output contains task identifiers/paths. --export builds a separate typed
summary. The original manifest and receipt remain the sole binding inputs.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import stat
import sys
sys.dont_write_bytecode = True
import bridge_v2 as protocol
import browser_target
import deploy
import diagnostics
import receipts
import research_profile


def read_object(path: Path) -> dict:
    path = deploy._safe_location(path)
    if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_nlink != 1:
        raise ValueError("private regular file required")
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("input too large")
    value = diagnostics._bounded_json(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("object required")
    return value


def validate(manifest: dict) -> None:
    # The existing validator is pure; call it without constructing Ledger.
    # Its legacy coercions are not suitable for a new public display interface.
    for key in ("task_id", "nonce", "mode", "branch", "baseline_sha", "source_repository"):
        if not isinstance(manifest.get(key), str):
            raise ValueError("invalid typed manifest")
    for key in ("allow_pr", "code_write_permission"):
        if key in manifest and type(manifest[key]) is not bool:
            raise ValueError("invalid permission boolean")
    for key in ("read_targets", "allowed_paths", "write_targets", "code_paths"):
        values = manifest.get(key, [])
        if not isinstance(values, list) or len(values) > 4096:
            raise ValueError("invalid path list")
        for path in values:
            if not isinstance(path, str) or any(ord(c) < 32 or ord(c) == 127 for c in path):
                raise ValueError("invalid path")
            protocol.safe_repo_path(path)
    permissions = manifest.get("permissions", {})
    if not isinstance(permissions, dict):
        raise ValueError("invalid permissions")
    for key in ("allow_pr", "allow_main_write", "explicit_code_write"):
        if key in permissions and type(permissions[key]) is not bool:
            raise ValueError("invalid permission boolean")
    protocol.Ledger._validate_manifest(None, manifest)
    if not protocol.protocol_is_v2(manifest.get("protocol_version")):
        raise ValueError("protocol v2 required")
    if "request_sha256" in manifest and manifest["request_sha256"] != protocol.request_sha256(manifest):
        raise ValueError("request hash mismatch")
    if "browser_target" in manifest:
        browser_target.validate_target(manifest["browser_target"])


def preview(args: argparse.Namespace) -> dict:
    report = {"schema_version": 1, "command": "preview-permissions", "command_succeeded": False,
              "send_authorized": False, "review_approved": False, "ledger_mutated": False,
              "network_attempted": False, "web_send_count": 0, "replacement_prepared": False,
              "do_not_resend": True, "exit_is_not_approval": True}
    try:
        if args.manifest:
            if args.receipt or args.task:
                raise ValueError("plan and receipt inputs cannot be mixed")
            manifest = read_object(Path(args.manifest))
            state, attempts, binding = "UNPREPARED_PLAN", None, "PLAN_ONLY_NOT_A_RECEIPT"
        else:
            if not args.ledger or not args.receipt:
                raise ValueError("explicit ledger and original receipt required")
            read_object(Path(args.receipt))  # Refuse linked, oversized and deep input first.
            _, _, record = diagnostics.select_snapshot(args)
            manifest = diagnostics._bounded_json(record["request_bytes"])
            state, attempts, binding = record["state"], record["send_attempts"], "ORIGINAL_RECEIPT_MATCHED"
        validate(manifest)
        target = manifest.get("browser_target")
        report.update(command_succeeded=True, input_binding=binding, state=state,
            send_attempts=attempts, task_id=manifest["task_id"], request_sha256=protocol.request_sha256(manifest),
            source_repository=manifest["source_repository"], source_commit=manifest["baseline_sha"],
            task_mode=manifest["mode"], read_targets=manifest.get("read_targets", []),
            result_repository=manifest.get("result_repository") or manifest["source_repository"],
            result_branch=manifest["branch"], result_paths=manifest["allowed_paths"],
            requested_code_paths=manifest.get("code_paths", []),
            requested_pr_creation=manifest["allow_pr"], main_write_permitted=False,
            browser_binding="BOUND_HOST_OBSERVATION" if target else "NOT_CONFIGURED",
            host="EXTERNAL_HOST_NOT_ATTESTED", result_writer="EXTERNAL_WRITER_NOT_VERIFIED",
            declared_future_receivers=["selected_host", "selected_model_service", "task_github_repository"],
            command_effects={"prepare": "writes_local_ledger", "claim": "consumes_local_send_entitlement",
                "verify": "reads_git_and_writes_verification_state", "inspect": "read_only_explicit_remote_opt_in",
                "preview-permissions": "reads_explicit_inputs_only"},
            enforcement={"read_targets": "request_scope_not_os_sandbox", "result_paths": "post_delivery_git_validation",
                "external_writer": "host_permission_responsibility", "page_identity": "not_runtime_attestation",
                "preview": "no_authority_or_new_entitlement"})
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        # Never echo malformed input, private paths or exception strings.
        report["error"] = "permission_preview_refused"
    return report


def share_summary(report: dict) -> dict:
    # Reconstruct, do not redact arbitrary user strings.
    return {"schema_version": 1, "command": "preview-permissions",
        "command_succeeded": report.get("command_succeeded") is True,
        "send_authorized": False, "review_approved": False, "ledger_mutated": False,
        "network_attempted": False, "web_send_count": 0, "do_not_resend": True,
        "input_binding": report.get("input_binding") if report.get("input_binding") in
            {"PLAN_ONLY_NOT_A_RECEIPT", "ORIGINAL_RECEIPT_MATCHED"} else "UNKNOWN",
        "state": report.get("state") if report.get("state") in protocol.ALLOWED_STATES | {"UNPREPARED_PLAN"} else "UNKNOWN",
        "read_target_count": len(report.get("read_targets", [])),
        "result_path_count": len(report.get("result_paths", [])),
        "external_writer": "NOT_VERIFIED", "redaction": "typed_allowlist_v1"}


def configure(parser: argparse.ArgumentParser) -> None:
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--manifest", type=Path)
    sources.add_argument("--ledger", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--lang", choices=("en", "zh-CN"), help="human preview language; JSON/export remain canonical")
    parser.add_argument("--export", type=Path)


def run(args: argparse.Namespace) -> int:
    report = preview(args)
    code = 0 if report["command_succeeded"] else 3
    if args.export:
        try:
            destination = deploy._safe_location(args.export)
            forbidden = [Path(__file__).resolve().parents[2]]
            for name in ("ledger",):
                if getattr(args, name, None):
                    forbidden.append(deploy._safe_location(getattr(args, name)))
            for name in ("PRO_BRIDGE_LEDGER", "PRO_BRIDGE_PROTECTED_LEDGER"):
                if os.environ.get(name):
                    forbidden.append(deploy._safe_location(Path(os.environ[name])))
            if any(destination == item or item in destination.parents for item in forbidden):
                raise ValueError("unsafe output")
            for source in (args.manifest, args.receipt):
                if source and destination == deploy._safe_location(source):
                    raise ValueError("input overwrite")
            report = share_summary(report)
            with destination.open("xb") as stream:
                stream.write((json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode())
        except (OSError, ValueError):
            report = {"error": "export_refused", "send_authorized": False, "ledger_mutated": False,
                      "network_attempted": False, "command_succeeded": False}
            code = 3
    language = getattr(args, "lang", None)
    if language and not args.json and not args.export:
        print(research_profile.render_preview(report, language))
    else:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.json else 2))
    return code


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    configure(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

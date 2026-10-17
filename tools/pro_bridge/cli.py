"""Command line for the Pro dispatch ledger.

The browser is not called from this process. A host agent claims a send
here, uses its available browser tool, then returns for verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge_v2
import browser_target
import git_verify
import receipts
import runtime_identity
import ui_selection
import process_runtime
import diagnostics
import offline_demo


TASK_TYPES = {"READ_ONLY_REVIEW", "CODE_WRITE_DRAFT", "RESEARCH"}


def configure_output() -> None:
    """The process wire format is UTF-8, independent of the host code page."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")


def ui_label(value: str) -> str:
    # Labels are observed UI text, never a fixed model/effort enumeration.
    if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise argparse.ArgumentTypeError("UI label must be nonempty single-line text")
    return value


def host_wait_guidance(record: dict) -> dict:
    return {
        "deadline_at": record["deadline_at"],
        "host_wait": {
            "owner": "calling_agent",
            "bounded_wait_required": True,
            "on_wait_budget_exhausted": "return_receipt_and_resume_same_task",
            "on_task_deadline": "stop_waiting_no_resend",
            "ui_completion_is_delivery": False,
            "automatic_retry": False,
        },
    }


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def default_ledger() -> Path:
    return repo_root() / ".pro_bridge" / "tasks"


def open_ledger(path: Path, *, create: bool = False) -> bridge_v2.Ledger:
    return bridge_v2.Ledger(path, bridge_v2.LiveClock(), create=create)


_GIT_SHA: str | None = None
_REAL_FETCH = git_verify.fetch_result


def bridge_git_sha() -> str:
    global _GIT_SHA
    if _GIT_SHA is not None:
        return _GIT_SHA
    try:
        proc = process_runtime.git(["rev-parse", "HEAD"], repo=repo_root(), timeout=3, check=False)
        text = (proc.stdout or b"").decode("utf-8", "replace").strip()
        _GIT_SHA = text if proc.returncode == 0 and text else "NOT_AVAILABLE"
    except (OSError, subprocess.TimeoutExpired):
        _GIT_SHA = "NOT_AVAILABLE"
    return _GIT_SHA


def ledger_id_for(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def source_identity(trusted_manifest_sha256: str | None = None) -> dict:
    modules = (bridge_v2, browser_target, git_verify, receipts, runtime_identity, ui_selection,
               process_runtime, diagnostics, offline_demo, diagnostics.deploy)
    paths = {module.__name__: str(Path(module.__file__).resolve()) for module in modules}
    paths["cli"] = str(Path(__file__).resolve())
    return runtime_identity.describe(Path(__file__).resolve().parents[2], trusted_manifest_sha256,
                                     module_files=paths)


def emit(payload: dict, code: int, ledger: bridge_v2.Ledger | None = None) -> int:
    body = dict(payload)
    body.setdefault("automatic_high_risk_continue", False)
    body.setdefault("bridge_version", bridge_v2.BRIDGE_VERSION)
    body.setdefault("bridge_git_sha", bridge_git_sha())
    body.setdefault("bridge_file", str(Path(__file__).resolve()))
    body.setdefault("worktree_git_head", runtime_identity.worktree_head(repo_root()))
    if "runtime_source_digest" not in body:
        body.update(source_identity())
    if ledger is not None:
        resolved = str(ledger.path.resolve())
        body.setdefault("resolved_ledger_path", resolved)
        body.setdefault("resolved_ledger_directory", str(ledger.path.resolve().parent))
        body.setdefault("ledger_id_path_kind", "absolute_resolved_ledger_json_file_path")
        body.setdefault("ledger_id", ledger_id_for(ledger.path))
        body.setdefault("replacement_ledger_created", False)
        receipt_keys = ("task_id", "request_sha256", "source_repository", "baseline_sha", "branch", "ledger_id")
        if not body.get("error") and all(body.get(key) for key in receipt_keys):
            body.setdefault("receipt", {key: body[key] for key in receipt_keys})
    print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    return code


def contract_fields(
    record: dict | None,
    task_id: str | None,
    *,
    command_succeeded: bool,
    verification_attempt_succeeded: bool,
    stored_delivery: bool | None = None,
) -> dict:
    if record is None or not task_id:
        current = False
        approved = False
    else:
        current = bridge_v2.delivery_current(record, task_id)
        approved = bridge_v2.current_approval(record, task_id)
    if stored_delivery is None:
        stored_delivery = current
    return {
        "command_succeeded": command_succeeded,
        "verification_attempt_succeeded": verification_attempt_succeeded,
        "current_delivery_valid": stored_delivery,
        "stored_delivery_valid": stored_delivery,
        "stored_review_approved": approved,
        "exit_is_not_approval": True,
    }


def task_index(ledger: bridge_v2.Ledger) -> dict:
    return json.loads(ledger.path.read_text(encoding="utf-8")).get("tasks", {})


def select_task(ledger: bridge_v2.Ledger, requested: str | None) -> tuple[str | None, str | None]:
    tasks = task_index(ledger)
    if requested:
        if requested not in tasks:
            return None, "task_not_found"
        return requested, None
    if not tasks:
        return None, "no tasks"
    if len(tasks) != 1:
        return None, "multiple_tasks"
    return next(iter(tasks)), None


RECEIPT_LABELS = (
    ("expect_request_sha256", "request_sha256"),
    ("expect_repository", "repository"),
    ("expect_baseline", "baseline"),
    ("expect_branch", "branch"),
    ("expect_ledger_id", "ledger_id"),
)


def receipt_error(record: dict, args: argparse.Namespace, ledger: bridge_v2.Ledger) -> list[dict]:
    return receipts.mismatches(record, args, ledger_id_for(ledger.path))


def missing_receipt_field(args: argparse.Namespace) -> str | None:
    for attr, label in RECEIPT_LABELS:
        if not (getattr(args, attr, "") or ""):
            return label
    return None


def locate_task(ledger: bridge_v2.Ledger, args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Name the task before reading how many rows the ledger happens to hold.

    --cross-workspace never selects a row by count. In-project queries still
    accept the sole row when that flag is absent.
    """
    if getattr(args, "cross_workspace", False):
        if not getattr(args, "task", None):
            return None, "task_required"
        missing = missing_receipt_field(args)
        if missing:
            return None, "receipt_field_required: " + missing
    return select_task(ledger, getattr(args, "task", None))


def refuse_selection(ledger: bridge_v2.Ledger, reason: str, requested: str | None = None,
                     args: argparse.Namespace | None = None) -> int:
    details = []
    if args is not None and getattr(args, "receipt", None):
        if args.expect_ledger_id != ledger_id_for(ledger.path):
            details.append({"field": "ledger_id", "provided": args.expect_ledger_id,
                            "actual": ledger_id_for(ledger.path), "actual_source": "selected_ledger",
                            "identity_input": "absolute_resolved_ledger_json_file_path_not_directory"})
        if reason == "task_not_found":
            details.append({"field": "task_id", "provided": requested, "actual": None,
                            "actual_source": "selected_ledger"})
    code = 1 if reason == "no tasks" else 3
    return emit(
        {
            "error": reason,
            "receipt_mismatches": details,
            "ledger_mutated": False,
            "task_id": requested,
            "do_not_resend": True,
            "review_approved": False,
            "current_verification": False,
            "replacement_prepared": False,
            **contract_fields(None, None, command_succeeded=False, verification_attempt_succeeded=False, stored_delivery=False),
        },
        code,
        ledger,
    )


def refuse_receipt(ledger: bridge_v2.Ledger, record: dict, differences: list[dict]) -> int:
    reason = "receipt mismatch: " + differences[0]["field"]
    task_id = record.get("task_id")
    return emit(
        {
            "error": reason,
            "receipt_mismatches": differences,
            "ledger_mutated": False,
            "task_id": task_id,
            "request_sha256": record.get("request_sha256"),
            "source_repository": record.get("source_repository"),
            "baseline_sha": record.get("baseline_sha"),
            "branch": record.get("branch"),
            "do_not_resend": True,
            "review_approved": False,
            "current_verification": False,
            "replacement_prepared": False,
            **contract_fields(record, task_id, command_succeeded=False, verification_attempt_succeeded=False),
        },
        3,
        ledger,
    )


def side_effects(before: dict | None, after: dict | None, *, content_verified: bool, content_checked: bool) -> dict:
    return git_verify.side_effect_report(before, after, content_verified=content_verified, content_checked=content_checked)


def probe_refs(remote: str, branch: str) -> dict | None:
    try:
        proc = process_runtime.git(["ls-remote", remote, "refs/heads/main", f"refs/heads/{branch}"],
                                   timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    found: dict[str, str] = {}
    for line in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref == "refs/heads/main":
            found["main"] = sha
        elif ref == f"refs/heads/{branch}":
            found["result_branch"] = sha
    return found


def render_prompt(manifest: dict, request_hash: str) -> str:
    reads = ", ".join(manifest["read_targets"])
    writes = "\n".join(f"- {path}" for path in manifest["allowed_paths"])
    acceptance = " | ".join(manifest["acceptance_criteria"])
    return (
        "protocol_version: 2\n"
        f"requested_model: {manifest.get('requested_model', 'UI_SELECTION_REQUIRED')}\n"
        + (f"requested_effort: {manifest['requested_effort']}\n" if "requested_effort" in manifest else "")
        + f"task_type: {manifest['task_type']}\n"
        f"task_id: {manifest['task_id']}\n"
        f"nonce: {manifest['nonce']}\n"
        f"request_sha256: {request_hash}\n"
        f"baseline_sha: {manifest['baseline_sha']}\n"
        f"source: {manifest['source_repository']}@{manifest['source_sha']}\n"
        f"source_ref: {manifest['source_ref']}\n"
        f"read_targets: {reads}\n"
        f"result_branch: {manifest['branch']}\n"
        "write_only:\n"
        f"{writes}\n"
        + (
            f"code_branch: {manifest['code_branch']}\n"
            + "code_paths:\n"
            + "\n".join(f"- {path}" for path in manifest.get("code_paths") or [])
            + "\n"
            if manifest.get("code_write_permission") is True and manifest.get("code_branch")
            else ""
        )
        + ("allow_pr: true\n" if manifest.get("allow_pr") else "do_not_open_a_pr: true\n")
        + "do_not_modify_main: true\n"
        "do_not_modify_any_other_file: true\n"
        f"expected_output: {manifest['expected_output']}\n"
        f"acceptance: {acceptance}\n"
        "The JSON must contain protocol_version, task_id, nonce, request_sha256, baseline_sha, status, and report_sha256.\n"
        "report_sha256 is the SHA-256 of the markdown file's exact UTF-8 bytes.\n"
        "Do not set self_upgrade. protocol_version must be 2.\n"
    )


def manifest_from_record(record: dict) -> dict:
    return json.loads(record["request_bytes"])


def public_record(record: dict) -> dict:
    hidden = {"request_bytes"}
    payload = {key: value for key, value in record.items() if key not in hidden}
    payload["browser_send_allowed"] = bridge_v2.send_gate(record)
    payload.update(host_wait_guidance(record))
    payload.update(browser_target_fields(record))
    return payload


def browser_target_fields(record: dict) -> dict:
    if bridge_v2.binding_error(record):
        return {"browser_target": None, "browser_target_status": "INVALID_BINDING"}
    target = manifest_from_record(record).get("browser_target")
    return {"browser_target": target, "browser_target_status": "BOUND" if target is not None else "NOT_CONFIGURED"}


def latest_task(ledger: bridge_v2.Ledger) -> str | None:
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    tasks = data.get("tasks", {})
    if not tasks:
        return None
    return max(tasks, key=lambda task_id: tasks[task_id].get("created_at", 0))


def build_manifest(args: argparse.Namespace) -> dict:
    task_id = args.task_id or bridge_v2.new_task_id()
    result_paths = [
        f".pro_bridge/results/{task_id}.json",
        f".pro_bridge/reports/{task_id}.md",
    ]
    if args.task_type == "CODE_WRITE_DRAFT":
        mode = "write"
        write_targets = list(args.write or [])
        allowed = list(write_targets)
        allow_pr = False
    else:
        mode = "read_only"
        write_targets = result_paths
        allowed = result_paths
        allow_pr = False
    branch = args.branch or f"test/pro-result-{task_id[:8]}"
    deadline = time.time() + args.deadline_seconds
    manifest = {
        "protocol_version": 2,
        "task_id": task_id,
        "nonce": bridge_v2.new_nonce(),
        "orchestration_id": args.orchestration,
        "task_type": args.task_type,
        "source_repository": args.repository,
        "source_ref": args.source_ref,
        "source_sha": args.source_sha,
        "read_targets": list(args.read or []),
        "write_targets": write_targets,
        "expected_output": args.expected_output,
        "acceptance_criteria": list(args.acceptance or ["delivery hashes match the manifest"]),
        "requested_model": args.ui_model_label or "UI_SELECTION_REQUIRED",
        "requested_effort": args.ui_effort_label or "UI_SELECTION_REQUIRED",
        "deadline": deadline,
        "deadline_at": deadline,
        "permissions": {
            "read_repositories": [args.repository],
            "result_branch": branch,
            "result_paths": allowed,
            "allow_pr": allow_pr,
            "allow_main_write": False,
            "explicit_code_write": False,
            "code_branch": None,
            "code_paths": [],
        },
        "mode": mode,
        "allowed_paths": allowed,
        "baseline_sha": args.source_sha,
        "branch": branch,
        "allow_pr": allow_pr,
        "result_repository": args.repository,
        "code_write_permission": False,
        "code_branch": None,
        "code_paths": [],
    }
    if getattr(args, "browser_target", None):
        target = browser_target.read_object(args.browser_target)
        browser_target.validate_target(target)
        if target["mode"] != "CHAT":
            raise ValueError("work_dispatch_not_supported")
        for argument, field in ((args.ui_model_label, "model_label"), (args.ui_effort_label, "effort_label")):
            if argument is not None and argument != target[field]:
                raise ValueError("UI label differs from browser target: " + field)
        manifest["requested_model"] = target["model_label"]
        manifest["requested_effort"] = target["effort_label"]
        manifest["browser_target"] = target
    if getattr(args, "selection_evidence", None):
        if "browser_target" not in manifest:
            raise ValueError("selection evidence requires a bound browser target")
        selection = ui_selection.check_selection(browser_target.read_object(args.selection_evidence),
                    model=manifest["requested_model"], effort=manifest["requested_effort"], now=time.time())
        if not selection["check_passed"]:
            raise ValueError("UI selection preflight failed: " + ", ".join(selection["reasons"]))
        manifest["selection_evidence_required"] = True
    if args.task_type == "CODE_WRITE_DRAFT" and args.authorize_code_write:
        canary = f"tests/pro_bridge/fixtures/pro_codewrite_canary_{task_id}.json"
        manifest["mode"] = "write"
        manifest["code_write_permission"] = True
        manifest["code_branch"] = args.code_branch
        manifest["code_paths"] = [canary]
        manifest["allowed_paths"] = result_paths
        manifest["write_targets"] = result_paths
        manifest["allow_pr"] = bool(args.allow_pr)
        manifest["permissions"]["result_paths"] = result_paths
        manifest["permissions"]["allow_pr"] = bool(args.allow_pr)
        manifest["permissions"]["explicit_code_write"] = True
        manifest["permissions"]["code_branch"] = args.code_branch
        manifest["permissions"]["code_paths"] = [canary]
    return manifest


def cmd_prepare(args: argparse.Namespace) -> int:
    if args.task_type not in TASK_TYPES:
        return emit({"error": "unsupported task_type"}, 1)
    if args.task_type == "CODE_WRITE_DRAFT" and args.authorize_code_write and not args.code_branch:
        return emit({"error": "authorized code write requires --code-branch"}, 1)
    try:
        manifest = build_manifest(args)
    except (OSError, ValueError) as exc:
        return emit({"error": str(exc), "do_not_resend": True, "replacement_prepared": False}, 3)
    ledger = open_ledger(Path(args.ledger), create=True)
    try:
        record = ledger.prepare(manifest)
    except (ValueError, PermissionError) as exc:
        return emit({"error": str(exc)}, 3)
    if record["state"] == "BLOCKED":
        return emit({"task_id": record["task_id"], "state": "BLOCKED", "do_not_resend": True}, 3)
    prompt = render_prompt(manifest_from_record(record), record["request_sha256"])
    record = ledger.update(
        record["task_id"],
        created_at=time.time(),
        browser_send_allowed=bridge_v2.send_gate(record),
    )
    return emit(
        {
            "task_id": record["task_id"],
            "state": record["state"],
            "request_sha256": record["request_sha256"],
            "branch": record["branch"],
            "source_repository": record["source_repository"],
            "baseline_sha": record["baseline_sha"],
            "browser_send_allowed": bridge_v2.send_gate(record),
            "do_not_resend": False,
            "prompt": prompt,
            "browser_target": manifest.get("browser_target"),
            "browser_target_check_required": "browser_target" in manifest,
            **host_wait_guidance(record),
        },
        0,
        ledger,
    )


def cmd_claim(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    record = ledger.get(args.task)
    if bridge_v2.identity_error(args.task, record):
        return emit({"task_id": args.task, "state": record["state"], "claimed": False, "do_not_resend": True, "reason": "task identity does not match", "review_approved": False, "current_verification": False}, 3)
    if bridge_v2.binding_error(record):
        return emit({"task_id": args.task, "state": record["state"], "claimed": False, "do_not_resend": True, "reason": "stored manifest does not match its request hash", "replacement_prepared": False, "review_approved": False, "current_verification": False, **contract_fields(record, args.task, command_succeeded=False, verification_attempt_succeeded=False)}, 3)
    if not bridge_v2.send_gate(record):
        return emit({"task_id": args.task, "state": record["state"], "claimed": False, "do_not_resend": True, "reason": "browser send is not allowed for this task"}, 3)
    target = manifest_from_record(record).get("browser_target")
    target_check = None
    selection_check = None
    if target is not None:
        try:
            if not args.browser_observation:
                raise ValueError("browser observation required for this task")
            observation = browser_target.read_object(args.browser_observation)
            target_check = browser_target.check_target(target, observation, now=time.time())
            if not target_check["check_passed"]:
                raise ValueError("browser target check failed")
            if manifest_from_record(record).get("selection_evidence_required"):
                selection_check = ui_selection.check_selection(observation.get("selection_evidence"),
                                  model=target["model_label"], effort=target["effort_label"], now=time.time())
                if not selection_check["check_passed"]:
                    raise ValueError("UI selection evidence check failed")
        except (OSError, ValueError) as exc:
            return emit({"task_id": args.task, "claimed": False, "do_not_resend": True,
                         "error": str(exc), "browser_target_check": target_check, "selection_check": selection_check,
                         "replacement_prepared": False}, 3, ledger)
    elif args.browser_observation:
        return emit({"task_id": args.task, "claimed": False, "do_not_resend": True,
                     "error": "task has no bound browser target; observation cannot upgrade a legacy task"}, 3, ledger)
    claimed = ledger.claim_send(args.task)
    current = ledger.get(args.task)
    return emit(
        {
            "task_id": args.task,
            "claimed": claimed,
            "state": current["state"],
            "send_attempts": current["send_attempts"],
            "do_not_resend": not claimed,
            "browser_target_check": target_check if target_check is not None else "NOT_CONFIGURED",
            "selection_check": selection_check if selection_check is not None else "NOT_CONFIGURED",
        },
        0 if claimed else 2,
    )


def cmd_mark_submitted(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    record = ledger.get(args.task)
    if record["state"] != "SUBMISSION_UNKNOWN" or record["send_attempts"] != 1:
        return emit({"task_id": args.task, "state": record["state"], "do_not_resend": True}, 2)
    try:
        updated = ledger.update(
            args.task,
            expected_revision=int(record.get("revision", 0)),
            expected_state="SUBMISSION_UNKNOWN",
            state="SUBMITTED",
            session_url=args.session_url or "",
            browser_evidence=args.evidence or "",
            notes=["browser submission observed"],
        )
    except (bridge_v2.StaleRecord, ValueError):
        fresh = ledger.get(args.task)
        return emit({"task_id": args.task, "state": fresh["state"], "do_not_resend": True, "error": "stale_record"}, 2)
    return emit({"task_id": args.task, "state": updated["state"], "do_not_resend": True}, 0)


def cmd_mark_unknown(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    record = ledger.get(args.task)
    if record["send_attempts"] < 1:
        return emit({"task_id": args.task, "state": record["state"], "do_not_resend": True, "reason": "nothing was claimed"}, 2)
    updated = ledger.update(args.task, notes=[args.reason or "browser result unknown; no resend"])
    return emit({"task_id": args.task, "state": updated["state"], "do_not_resend": True}, 2)


def cmd_block(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    record = ledger.get(args.task)
    if record["send_attempts"] > 0:
        updated = ledger.update(args.task, notes=[f"blocked after claim: {args.reason}; no resend"])
        return emit({"task_id": args.task, "state": updated["state"], "do_not_resend": True}, 2)
    updated = ledger.update(args.task, state="BLOCKED", notes=[args.reason or "blocked before send"])
    return emit({"task_id": args.task, "state": updated["state"], "do_not_resend": True}, 3)


def cmd_status(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    task_id, reason = locate_task(ledger, args)
    if reason:
        return refuse_selection(ledger, reason, args.task, args)
    record = ledger.get(task_id)
    mismatch = receipt_error(record, args, ledger)
    if mismatch:
        return refuse_receipt(ledger, record, mismatch)
    payload = public_record(record)
    payload["task_id"] = task_id
    payload["request_sha256"] = record.get("request_sha256")
    payload["source_repository"] = record.get("source_repository")
    payload["baseline_sha"] = record.get("baseline_sha")
    payload["source_sha"] = record.get("source_sha")
    payload["nonce"] = record.get("nonce")
    payload["review_approved"] = bridge_v2.current_approval(record, task_id)
    payload["current_verification"] = bridge_v2.delivery_current(record, task_id)
    if bridge_v2.identity_error(task_id, record):
        payload["browser_send_allowed"] = False
        payload["review_approved"] = False
        payload["current_verification"] = False
    payload["do_not_resend"] = record["send_attempts"] > 0 or record["state"] != "PREPARED" or bool(bridge_v2.binding_error(record))
    payload.update(contract_fields(record, task_id, command_succeeded=True, verification_attempt_succeeded=False))
    return emit(payload, 0, ledger)


def cmd_prompt(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    record = ledger.get(args.task)
    if bridge_v2.identity_error(args.task, record) or bridge_v2.binding_error(record):
        return emit({"task_id": args.task, "error": "stored manifest does not match its request hash", "do_not_resend": True, "review_approved": False, "current_verification": False, "replacement_prepared": False, **contract_fields(record, args.task, command_succeeded=False, verification_attempt_succeeded=False)}, 3)
    sys.stdout.write(render_prompt(manifest_from_record(record), record["request_sha256"]))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    task_id, reason = locate_task(ledger, args)
    if reason:
        return refuse_selection(ledger, reason, args.task, args)
    record = ledger.get(task_id)
    mismatch = receipt_error(record, args, ledger)
    if mismatch:
        return refuse_receipt(ledger, record, mismatch)
    payload = {
        "task_id": task_id,
        "state": record["state"],
        "send_attempts": record["send_attempts"],
        "branch": record["branch"],
        "request_sha256": record["request_sha256"],
        "source_repository": record.get("source_repository"),
        "baseline_sha": record.get("baseline_sha"),
        "source_sha": record.get("source_sha"),
        "nonce": record.get("nonce"),
        "session_url": record.get("session_url", ""),
        "do_not_resend": True,
        "resume": "read_only",
        **browser_target_fields(record),
        **host_wait_guidance(record),
        "review_approved": bridge_v2.current_approval(record, task_id),
        "current_verification": bridge_v2.delivery_current(record, task_id),
        "replacement_prepared": False,
    }
    if bridge_v2.identity_error(task_id, record):
        payload["review_approved"] = False
        payload["current_verification"] = False
        payload["error"] = "task identity does not match"
        payload.update(contract_fields(record, task_id, command_succeeded=False, verification_attempt_succeeded=False, stored_delivery=False))
        return emit(payload, 3, ledger)
    if args.verify:
        return verify_record(ledger, record, args, task_id)
    payload.update(contract_fields(record, task_id, command_succeeded=True, verification_attempt_succeeded=False))
    return emit(payload, 0, ledger)


def _failure_changes(record: dict, label: str) -> dict:
    changes = {
        "notes": [label],
        "last_observation": label,
        "review_approved": False,
        "pr_presence": "NOT_VERIFIED",
        "delivery_integrity": "NOT_CURRENT",
    }
    if record["state"] == "VERIFIED" and record.get("delivery_integrity") == "VERIFIED":
        changes["historical_verification"] = {
            "state": "VERIFIED",
            "delivery_integrity": record.get("delivery_integrity"),
            "review_verdict": record.get("review_verdict"),
            "evidence_origin": record.get("evidence_origin"),
            "result_evidence": record.get("result_evidence"),
        }
        changes["review_verdict"] = None
    return changes


def _note_failed_check(ledger: bridge_v2.Ledger, record: dict, label: str) -> None:
    current = record
    for _ in range(3):
        try:
            ledger.update(
                current["task_id"],
                expected_revision=int(current.get("revision", 0)),
                expected_state=current["state"],
                **_failure_changes(current, label),
            )
            return
        except ValueError:
            return
        except bridge_v2.StaleRecord:
            fresh = ledger.get(current["task_id"])
            if fresh["state"] != current["state"]:
                return
            if fresh.get("result_evidence") != current.get("result_evidence") and fresh.get("last_observation") == "current_ok":
                return
            current = fresh


def _historical(record: dict) -> dict | None:
    snapshot = record.get("production_snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    historical = record.get("historical_verification")
    return historical if isinstance(historical, dict) else None


def _binding_rejected(ledger: bridge_v2.Ledger, record: dict, reason: str, selected_task_id: str | None = None) -> int:
    selected = selected_task_id or record.get("task_id")
    if record.get("task_id") != selected:
        fresh = ledger.invalidate_selected(selected, "identity_error")
        return emit(
            {
                "task_id": selected,
                "error": reason,
                "state": fresh["state"],
                "do_not_resend": True,
                "verified": False,
                "current_verification": False,
                "review_approved": False,
                "delivery_integrity": fresh.get("delivery_integrity"),
                "historical_verification": _historical(fresh),
                "production_snapshot": fresh.get("production_snapshot"),
                "evidence_origin": fresh.get("evidence_origin"),
            },
            3,
        )
    if record.get("last_observation") != "binding_error":
        _started, generation = ledger.begin_verification(selected)
        record = ledger.finish_verification(
            selected,
            generation,
            last_observation="binding_error",
            review_approved=False,
            delivery_integrity="NOT_CURRENT",
            notes=[reason],
        )
    return emit(
        {
            "task_id": record["task_id"],
            "error": reason,
            "state": record["state"],
            "do_not_resend": True,
            "verified": False,
            "current_verification": False,
            "review_approved": False,
            "delivery_integrity": record.get("delivery_integrity"),
            "historical_verification": _historical(record),
            "production_snapshot": record.get("production_snapshot"),
            "evidence_origin": record.get("evidence_origin"),
        },
        3,
    )


def _observed(record: dict, error: str | None = None, verified: bool = False, code: int = 2, selected_task_id: str | None = None) -> int:
    selected = selected_task_id or record.get("task_id")
    current = bridge_v2.delivery_current(record, selected)
    payload = {
        "task_id": selected,
        "state": record["state"],
        "do_not_resend": True,
        "verified": verified,
        "current_verification": current,
        "review_approved": bridge_v2.current_approval(record, selected),
        "delivery_integrity": record.get("delivery_integrity"),
        "historical_verification": _historical(record),
        "production_snapshot": record.get("production_snapshot"),
        "evidence_origin": record.get("evidence_origin"),
        "review_verdict": record.get("review_verdict"),
        "result_evidence": record.get("result_evidence"),
        "pr_presence": "NOT_VERIFIED",
        "pr_presence_meaning": git_verify.PR_PRESENCE_MEANING,
        "github_behavior_check": "NOT_VERIFIED",
        "main_observation_note": git_verify.MAIN_OBSERVATION_NOTE,
        **side_effects(None, None, content_verified=False, content_checked=False),
        **contract_fields(
            record,
            selected,
            command_succeeded=False,
            verification_attempt_succeeded=False,
            stored_delivery=current,
        ),
    }
    if error:
        payload["error"] = error
        payload["verification_attempt_succeeded"] = False
    return emit(payload, 0 if current and not error else code)


def _start_failed(selected: str, fresh: dict | None, persisted: bool) -> int:
    current = False if fresh is None else bridge_v2.delivery_current(fresh, selected)
    approved = False if fresh is None else bridge_v2.current_approval(fresh, selected)
    # A start that never acquired its generation is not a successful verification.
    # When the failure itself cannot be stored, a later process may still see the previous row.
    return emit(
        {
            "task_id": selected,
            "error": "verification_start_failed",
            "state": None if fresh is None else fresh.get("state"),
            "do_not_resend": True,
            "verified": False,
            "current_verification": False if not persisted else current,
            "review_approved": False if not persisted else approved,
            "failure_persisted": persisted,
            "cross_process_recovery": "recorded" if persisted else "not_guaranteed",
            "delivery_integrity": None if fresh is None else fresh.get("delivery_integrity"),
            "historical_verification": None if fresh is None else _historical(fresh),
            "production_snapshot": None if fresh is None else fresh.get("production_snapshot"),
            "evidence_origin": None if fresh is None else fresh.get("evidence_origin"),
            "verification_attempt_succeeded": False,
            "command_succeeded": False,
            "stored_delivery_valid": current,
            "stored_review_approved": approved,
            "current_delivery_valid": False if not persisted else current,
            "pr_presence": "NOT_VERIFIED",
            "pr_presence_meaning": git_verify.PR_PRESENCE_MEANING,
            "github_behavior_check": "NOT_VERIFIED",
            "main_observation_note": git_verify.MAIN_OBSERVATION_NOTE,
            "exit_is_not_approval": True,
        },
        2,
    )


def _fixture_failure(selected: str, fresh: dict, error: str) -> int:
    return emit(
        {
            "task_id": selected,
            "state": fresh.get("state"),
            "do_not_resend": True,
            "verified": False,
            "delivery_integrity": "NOT_CURRENT",
            "review_verdict": "REJECTED",
            "review_approved": False,
            "current_verification": False,
            "evidence_origin": "fixture",
            "historical_verification": _historical(fresh),
            "historical_fixture_observation": fresh.get("historical_fixture_observation"),
            "production_snapshot": fresh.get("production_snapshot"),
            "fixture_observation": fresh.get("fixture_observation"),
            "pr_presence": "NOT_VERIFIED",
            "result_evidence": {"evidence_origin": "fixture", "review_verdict": "REJECTED", "review_approved": False, "commit": None, "failure": error},
            "automatic_high_risk_continue": False,
            "error": error,
        },
        2,
    )


def verify_record(ledger: bridge_v2.Ledger, record: dict, args: argparse.Namespace, selected_task_id: str | None = None) -> int:
    with process_runtime.operation_budget(getattr(args, "timeout_seconds", 30.0)):
        return _verify_record(ledger, record, args, selected_task_id)


def _verify_record(ledger: bridge_v2.Ledger, record: dict, args: argparse.Namespace, selected_task_id: str | None = None) -> int:
    selected = selected_task_id or record.get("task_id")
    observed = record.get("verification_generation")
    generation = None
    fixture = bool(getattr(args, "fixture", False))
    try:
        ident = bridge_v2.identity_error(selected, record)
        if ident:
            fresh = ledger.invalidate_selected(selected, "identity_error")
            return emit(
                {
                    "task_id": selected,
                    "error": ident,
                    "state": fresh["state"],
                    "do_not_resend": True,
                    "verified": False,
                    "current_verification": False,
                    "review_approved": False,
                    "production_snapshot": fresh.get("production_snapshot"),
                },
                3,
            )
        mismatch = bridge_v2.binding_error(record)
        if mismatch:
            return _binding_rejected(ledger, record, mismatch, selected)
        local_path = bool(args.local_repo) or (args.remote and (str(args.remote).startswith("file:") or Path(args.remote).exists()))
        if local_path and not fixture:
            _started, generation = ledger.begin_verification(selected)
            fresh = ledger.finish_verification(selected, generation, last_observation="fixture_required", review_approved=False, delivery_integrity="NOT_CURRENT", notes=["fixture_required"])
            generation = None
            return _observed(fresh, error="fixture_required", code=3, selected_task_id=selected)
        if args.remote and not git_verify.remote_allowed(record, args.remote):
            _started, generation = ledger.begin_verification(selected)
            fresh = ledger.finish_verification(selected, generation, last_observation="remote_mismatch", review_approved=False, delivery_integrity="NOT_CURRENT", notes=["remote_mismatch"])
            generation = None
            return _observed(fresh, error="remote_mismatch", code=3, selected_task_id=selected)
        if not args.local_repo and not args.remote:
            return emit({"error": "verify requires --remote or --local-repo"}, 1)
        _started, generation = ledger.begin_verification(selected)
        return _verify_body(ledger, ledger.get(selected), args, generation, selected)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        failure = "operation_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "cancelled"
        persisted = False
        if generation is not None:
            try:
                ledger.finish_verification(selected, generation, last_observation=failure,
                    review_approved=False, delivery_integrity="NOT_CURRENT", notes=[failure], fixture_result=fixture)
                persisted = True
            except (bridge_v2.LockTimeout, bridge_v2.StaleRecord, OSError):
                pass
        return emit({"task_id": selected, "state": record["state"], "error": failure,
            "command_succeeded": False, "verification_attempt_succeeded": False,
            "verified": False, "current_verification": False, "current_delivery_valid": False,
            "review_approved": False, "delivery_integrity": "NOT_CURRENT", "do_not_resend": True,
            "replacement_prepared": False, "failure_persisted": persisted, "exit_is_not_approval": True,
            "next_step": "Preserve the original receipt; inspect the same task without resending or renewing its deadline."},
            2 if failure == "operation_timeout" else 130)
    except bridge_v2.LockTimeout:
        try:
            fresh = ledger.note_start_failed(selected, observed)
        except bridge_v2.LockTimeout:
            return _start_failed(selected, None, False)
        return _start_failed(selected, fresh, True)
    except bridge_v2.StaleRecord:
        fresh = ledger.get(selected)
        return _observed(fresh, error="stale_generation", code=2, selected_task_id=selected)
    except Exception:
        if generation is not None:
            try:
                if fixture:
                    current_row = ledger.get(selected)
                    prior = current_row.get("fixture_observation") if isinstance(current_row.get("fixture_observation"), dict) else None
                    rejected = {
                        "fixture_result": True,
                        "last_observation": "reader_error",
                        "review_approved": False,
                        "notes": ["reader_error"],
                    }
                    if prior:
                        rejected["historical_fixture_observation"] = prior
                    ledger.finish_verification(selected, generation, **rejected)
                else:
                    ledger.finish_verification(
                        selected,
                        generation,
                        last_observation="reader_error",
                        review_approved=False,
                        delivery_integrity="NOT_CURRENT",
                        notes=["reader_error"],
                    )
            except bridge_v2.StaleRecord:
                pass
        fresh = ledger.get(selected)
        if fixture:
            return _fixture_failure(selected, fresh, "reader_error")
        current = bridge_v2.delivery_current(fresh, selected)
        approved = bridge_v2.current_approval(fresh, selected)
        return emit(
            {
                "task_id": selected,
                "error": "reader_error",
                "state": fresh["state"],
                "do_not_resend": True,
                "verified": False,
                "current_verification": current,
                "review_approved": approved,
                "delivery_integrity": fresh.get("delivery_integrity"),
                "historical_verification": _historical(fresh),
                "production_snapshot": fresh.get("production_snapshot"),
                "evidence_origin": fresh.get("evidence_origin"),
                "verification_attempt_succeeded": False,
                "command_succeeded": False,
                "stored_delivery_valid": current,
                "stored_review_approved": approved,
                "current_delivery_valid": current,
                "pr_presence": "NOT_VERIFIED",
                "pr_presence_meaning": git_verify.PR_PRESENCE_MEANING,
                "github_behavior_check": "NOT_VERIFIED",
                "git_content_check": "FAILED",
                "main_observation_note": git_verify.MAIN_OBSERVATION_NOTE,
                "exit_is_not_approval": True,
            },
            2,
        )


def _verify_body(ledger: bridge_v2.Ledger, record: dict, args: argparse.Namespace, generation: int, selected_task_id: str | None = None) -> int:
    observation = None
    fixture = bool(getattr(args, "fixture", False))
    expected_hash = record.get("request_sha256")
    before_refs = None
    after_refs = None
    probe = bool(args.remote) and not fixture and git_verify.fetch_result is _REAL_FETCH
    if args.local_repo:
        observation = git_verify.read_delivery(Path(args.local_repo), record["baseline_sha"], args.rev or record["branch"], record["allowed_paths"])
    elif args.remote:
        if probe:
            before_refs = probe_refs(args.remote, record["branch"])
        with tempfile.TemporaryDirectory(prefix="pro-bridge-fetch-") as directory:
            with process_runtime.frozen_git_config(repo=Path(directory)):
                fetched = git_verify.fetch_result(args.remote, record["branch"], Path(directory), record["baseline_sha"])
                if fetched["kind"] != "ok":
                    observation = {"fetch_kind": fetched["kind"], "detail": fetched["detail"]}
                else:
                    observation = git_verify.read_delivery(Path(directory), record["baseline_sha"], record["branch"], record["allowed_paths"])
                    if observation is None:
                        observation = {"fetch_kind": "branch_absent"}
                    elif observation.get("history_incomplete"):
                        observation["fetch_kind"] = "history_incomplete"
        if probe:
            after_refs = probe_refs(args.remote, record["branch"])
    decision = git_verify.evaluate(record, observation, time.time())
    changes: dict = {
        "notes": decision["notes"],
        "last_observation": ("fixture_checked" if decision["verified"] else "fixture_failed") if fixture else ("current_ok" if decision["verified"] else "current_verification_failed"),
        "review_approved": False if fixture else decision.get("review_approved") is True,
        "execution_test_status": decision.get("execution_test_status"),
        "pr_presence": "NOT_VERIFIED",
        "fixture_result": fixture,
        "expected_request_sha256": expected_hash,
        "github_side_observation": {"before": before_refs, "after": after_refs},
    }
    evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else None
    if evidence:
        changes["observed_binding"] = {
            "request_sha256": evidence.get("request_sha256"),
            "task_id": evidence.get("task_id"),
            "nonce": evidence.get("nonce"),
            "baseline_sha": evidence.get("baseline_sha"),
        }
    if not (fixture and record.get("production_snapshot")):
        changes["delivery_integrity"] = decision["delivery_integrity"]
        changes["review_verdict"] = decision.get("review_verdict")
        changes["evidence_origin"] = "fixture" if fixture else "github"
        if decision["state"] != record["state"] and not (record["state"] == "VERIFIED" and not decision["verified"]):
            changes["state"] = decision["state"]
    if "late_evidence" in decision:
        changes["late_evidence"] = decision["late_evidence"]
    prior_fixture = record.get("fixture_observation") if isinstance(record.get("fixture_observation"), dict) else None
    if fixture and decision.get("verified") and decision.get("evidence"):
        fixture_evidence = dict(decision["evidence"])
        fixture_evidence["review_approved"] = False
        fixture_evidence["evidence_origin"] = "fixture"
        changes["fixture_observation"] = fixture_evidence
        if prior_fixture:
            changes["historical_fixture_observation"] = prior_fixture
    elif fixture:
        if prior_fixture:
            changes["historical_fixture_observation"] = prior_fixture
    elif decision.get("evidence"):
        evidence = dict(decision["evidence"])
        if fixture:
            evidence["review_approved"] = False
            evidence["evidence_origin"] = "fixture"
        changes["result_evidence"] = evidence
        changes["qa_result"] = "NOT_A_QUALITY_APPROVAL"
    selected = selected_task_id or record.get("task_id")
    updated = ledger.finish_verification(selected, generation, **changes)
    if updated.get("last_observation") == "request_changed":
        return emit(
            {
                "task_id": selected,
                "state": updated["state"],
                "do_not_resend": True,
                "verified": False,
                "error": "request_changed",
                "delivery_integrity": updated.get("delivery_integrity"),
                "review_verdict": None,
                "review_approved": False,
                "current_verification": False,
                "evidence_origin": updated.get("evidence_origin"),
                "historical_verification": _historical(updated),
                "production_snapshot": updated.get("production_snapshot"),
                "automatic_high_risk_continue": False,
                "pr_presence": "NOT_VERIFIED",
                "pr_presence_meaning": git_verify.PR_PRESENCE_MEANING,
                "verification_attempt_succeeded": False,
                "command_succeeded": False,
                "exit_is_not_approval": True,
                **side_effects(before_refs, after_refs, content_verified=False, content_checked=True),
            },
            2,
        )
    code = 0 if decision["verified"] else {"BLOCKED": 3, "TIMEOUT": 4}.get(updated["state"], 2)
    current = (not fixture) and decision["verified"] and bridge_v2.delivery_current(updated, selected)
    if fixture and not (decision.get("verified") and decision.get("evidence")):
        shown_evidence = {"evidence_origin": "fixture", "review_verdict": "REJECTED", "review_approved": False, "commit": None, "failure": "fixture_failed"}
        shown_verdict = "REJECTED"
        shown_origin = "fixture"
    elif fixture:
        shown_evidence = updated.get("fixture_observation") if isinstance(updated.get("fixture_observation"), dict) else {"evidence_origin": "fixture", "review_verdict": "REJECTED", "review_approved": False, "commit": None}
        shown_verdict = shown_evidence.get("review_verdict")
        shown_origin = "fixture"
    else:
        shown_evidence = updated.get("result_evidence")
        shown_verdict = updated.get("review_verdict")
        shown_origin = updated.get("evidence_origin")
    return emit(
        {
            "task_id": selected,
            "state": updated["state"],
            "do_not_resend": True,
            "verified": decision["verified"],
            "delivery_integrity": "NOT_CURRENT" if fixture else updated.get("delivery_integrity"),
            "review_verdict": shown_verdict,
            "review_approved": False if fixture else bridge_v2.current_approval(updated, selected),
            "current_verification": current,
            "evidence_origin": shown_origin,
            "historical_verification": _historical(updated),
            "historical_fixture_observation": updated.get("historical_fixture_observation"),
            "production_snapshot": updated.get("production_snapshot"),
            "fixture_observation": updated.get("fixture_observation"),
            "pr_presence": "NOT_VERIFIED",
            "pr_presence_meaning": git_verify.PR_PRESENCE_MEANING,
            "result_evidence": shown_evidence,
            "automatic_high_risk_continue": False,
            "verification_attempt_succeeded": bool(decision["verified"]),
            "command_succeeded": bool(decision["verified"]),
            "exit_is_not_approval": True,
            **side_effects(before_refs, after_refs, content_verified=bool(decision["verified"]), content_checked=True),
            **contract_fields(
                updated,
                selected,
                command_succeeded=bool(decision["verified"]),
                verification_attempt_succeeded=bool(decision["verified"]),
            ),
        },
        code,
    )


def cmd_verify(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    return verify_record(ledger, ledger.get(args.task), args, args.task)


def cmd_report(args: argparse.Namespace) -> int:
    ledger = open_ledger(Path(args.ledger))
    task_id, reason = locate_task(ledger, args)
    if reason:
        return refuse_selection(ledger, reason, args.task, args)
    record = ledger.get(task_id)
    receipt = receipt_error(record, args, ledger)
    if receipt:
        return refuse_receipt(ledger, record, receipt)
    ident = bridge_v2.identity_error(task_id, record)
    if ident:
        record = ledger.invalidate_selected(task_id, "identity_error")
        return emit(
            {
                "task_id": task_id,
                "state": record["state"],
                "qa_result": record.get("qa_result"),
                "branch": record["branch"],
                "request_sha256": record.get("request_sha256"),
                "source_repository": record.get("source_repository"),
                "baseline_sha": record.get("baseline_sha"),
                "source_sha": record.get("source_sha"),
                "nonce": record.get("nonce"),
                "result_evidence": record.get("result_evidence"),
                "current_verification": False,
                "historical_verification": _historical(record),
                "production_snapshot": record.get("production_snapshot"),
                "delivery_integrity": record.get("delivery_integrity"),
                "review_verdict": record.get("review_verdict"),
                "review_approved": False,
                "evidence_origin": record.get("evidence_origin"),
                "verification_generation": record.get("verification_generation"),
                "markdown_is_evidence_not_authorization": True,
                "automatic_high_risk_continue": False,
                "do_not_resend": True,
                "replacement_prepared": False,
                "error": ident,
                "pr_presence": "NOT_VERIFIED",
                "pr_presence_meaning": git_verify.PR_PRESENCE_MEANING,
                "github_behavior_check": "NOT_VERIFIED",
                "main_observation_note": git_verify.MAIN_OBSERVATION_NOTE,
                **contract_fields(record, task_id, command_succeeded=False, verification_attempt_succeeded=False, stored_delivery=False),
            },
            2,
            ledger,
        )
    mismatch = bridge_v2.binding_error(record)
    if mismatch and record.get("last_observation") != "binding_error":
        return _binding_rejected(ledger, record, mismatch, task_id)
    if mismatch:
        record = ledger.get(task_id)
    production_ok = bridge_v2.delivery_current(record, task_id)
    approved = bridge_v2.current_approval(record, task_id)
    return emit(
        {
            "task_id": task_id,
            "state": record["state"],
            "qa_result": record.get("qa_result"),
            **browser_target_fields(record),
            **host_wait_guidance(record),
            "branch": record["branch"],
            "request_sha256": record.get("request_sha256"),
            "source_repository": record.get("source_repository"),
            "baseline_sha": record.get("baseline_sha"),
            "source_sha": record.get("source_sha"),
            "nonce": record.get("nonce"),
            "result_evidence": record.get("result_evidence"),
            "current_verification": production_ok,
            "historical_verification": _historical(record),
            "production_snapshot": record.get("production_snapshot"),
            "delivery_integrity": record.get("delivery_integrity"),
            "review_verdict": record.get("review_verdict"),
            "review_approved": approved,
            "evidence_origin": record.get("evidence_origin"),
            "verification_generation": record.get("verification_generation"),
            "markdown_is_evidence_not_authorization": True,
            "automatic_high_risk_continue": False,
            "do_not_resend": True if mismatch or record["send_attempts"] > 0 or record["state"] != "PREPARED" else False,
            "error": mismatch,
            "pr_presence": "NOT_VERIFIED",
            "pr_presence_meaning": git_verify.PR_PRESENCE_MEANING,
            "github_behavior_check": "NOT_VERIFIED",
            "main_observation_note": git_verify.MAIN_OBSERVATION_NOTE,
            "git_content_check": "VERIFIED" if production_ok else "NOT_VERIFIED",
            **contract_fields(record, task_id, command_succeeded=True, verification_attempt_succeeded=False),
        },
        0 if production_ok and not mismatch else 2,
        ledger,
    )


def cmd_check_target(args: argparse.Namespace) -> int:
    try:
        result = browser_target.check_target(browser_target.read_object(args.target),
                                             browser_target.read_object(args.observation), now=time.time())
    except (OSError, ValueError) as exc:
        return emit({"error": str(exc), "check_passed": False, "send_authorized": False}, 3)
    return emit(result, 0 if result["check_passed"] else 3)


def cmd_check_selection(args: argparse.Namespace) -> int:
    try:
        result = ui_selection.check_selection(browser_target.read_object(args.evidence),
                   model=args.model, effort=args.effort, now=time.time())
    except (OSError, ValueError) as exc:
        return emit({"error": str(exc), "check_passed": False, "send_authorized": False}, 3)
    return emit(result, 0 if result["check_passed"] else 3)


def cmd_identify(args: argparse.Namespace) -> int:
    result = source_identity(args.trusted_manifest_sha256)
    # Without a trust anchor this is a diagnostic, not verification success.
    trusted = args.trusted_manifest_sha256 is not None or bool(os.environ.get(runtime_identity.TRUST_ENV))
    code = 3 if trusted and result["provenance_status"] != "VERIFIED" else 0
    return emit(result, code)


def cmd_route(args: argparse.Namespace) -> int:
    reasons = []
    if args.small_edit:
        reasons.append("small_edit")
    if args.live_ui:
        reasons.append("live_ui")
    if args.short_local:
        reasons.append("short_local")
    if args.frequent_roundtrip:
        reasons.append("frequent_roundtrip")
    if args.permission_blocked:
        reasons.append("permission_blocked")
    return emit(
        {
            "eligible": not reasons,
            "reasons": reasons,
            "policy": "initial_route_not_cost_proven",
            "cost_claim": "NOT_OBSERVABLE",
        },
        0 if not reasons else 2,
    )


def positive_timeout(value: str) -> float:
    import math
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be finite and between 0 and 300 seconds") from exc
    if not math.isfinite(number) or not 0 < number <= 300:
        raise argparse.ArgumentTypeError("timeout must be finite and between 0 and 300 seconds")
    return number


def cmd_doctor(args: argparse.Namespace) -> int:
    report, code = diagnostics.doctor(args, source_identity(args.trusted_manifest_sha256))
    return diagnostics.present(report, code, args, Path(__file__).resolve().parents[2])


def cmd_inspect(args: argparse.Namespace) -> int:
    report, code = diagnostics.inspect_task(args)
    return diagnostics.present(report, code, args, Path(__file__).resolve().parents[2])


def cmd_demo(args: argparse.Namespace) -> int:
    report, code = offline_demo.run_demo(args)
    if not report["command_succeeded"] and code == 0:
        code = 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.json else 2))
    return code


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(prog="pro-bridge", allow_abbrev=False)
    sub = main.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", allow_abbrev=False)
    demo.add_argument("--scenario", choices=("all", *offline_demo.SCENARIOS), default="all")
    demo.add_argument("--keep", action="store_true")
    demo.add_argument("--temp-parent", help="existing directory outside installations and ledgers")
    demo.add_argument("--json", action="store_true")
    demo.set_defaults(func=cmd_demo)

    for name, function in (("doctor", cmd_doctor), ("inspect", cmd_inspect)):
        diagnostic = sub.add_parser(name, allow_abbrev=False)
        diagnostic.add_argument("--ledger", required=name == "inspect")
        diagnostic.add_argument("--task")
        diagnostic.add_argument("--json", action="store_true")
        diagnostic.add_argument("--export", help="explicit new redacted JSON file outside installation and ledger")
        diagnostic.set_defaults(func=function)
    doctor = sub.choices["doctor"]
    doctor.add_argument("--skill", help="explicit existing skill; literal Python entrypoint check only")
    doctor.add_argument("--trusted-manifest-sha256")
    doctor.add_argument("--expect-runtime-digest")
    doctor.add_argument("--expect-entrypoint", help="expected absolute CLI script path")
    inspect = sub.choices["inspect"]
    remote = inspect.add_mutually_exclusive_group()
    remote.add_argument("--remote", default="", help="explicit task repository only; isolated read-only observation")
    remote.add_argument("--local-repo", default="", help="explicit local fixture Git repository")
    inspect.add_argument("--fixture", action="store_true")
    inspect.add_argument("--timeout-seconds", type=positive_timeout, default=30.0)

    prepare = sub.add_parser("prepare", allow_abbrev=False)
    prepare.add_argument("--ledger", default=str(default_ledger()))
    prepare.add_argument("--task-type", required=True)
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--source-sha", required=True)
    prepare.add_argument("--source-ref", required=True)
    prepare.add_argument("--read", action="append")
    prepare.add_argument("--write", action="append")
    prepare.add_argument("--orchestration", required=True)
    prepare.add_argument("--expected-output", default="review report")
    prepare.add_argument("--acceptance", action="append")
    prepare.add_argument("--deadline-seconds", type=float, default=3600)
    prepare.add_argument("--ui-model-label", type=ui_label, help="model label observed and selected in the real web UI")
    prepare.add_argument("--ui-effort-label", type=ui_label, help="observed effort/tier label; use NOT_AVAILABLE if the UI has no control")
    prepare.add_argument("--browser-target", help="UTF-8 JSON identifying the selected mode, project, conversation and browser tab")
    prepare.add_argument("--selection-evidence", help="completed host control-discovery trace; checked before creating a task")
    prepare.add_argument("--task-id")
    prepare.add_argument("--branch")
    prepare.add_argument("--authorize-code-write", action="store_true")
    prepare.add_argument("--code-branch")
    prepare.add_argument("--allow-pr", action="store_true")
    prepare.set_defaults(func=cmd_prepare)

    for name, func in (
        ("claim", cmd_claim),
        ("mark-submitted", cmd_mark_submitted),
        ("mark-unknown", cmd_mark_unknown),
        ("block", cmd_block),
        ("status", cmd_status),
        ("prompt", cmd_prompt),
        ("resume", cmd_resume),
        ("verify", cmd_verify),
        ("report", cmd_report),
    ):
        item = sub.add_parser(name, allow_abbrev=False)
        item.add_argument("--ledger", default=str(default_ledger()))
        item.add_argument("--task")
        item.set_defaults(func=func)
    sub.choices["mark-submitted"].add_argument("--session-url", default="")
    sub.choices["claim"].add_argument("--browser-observation", help="fresh host observation matching the immutable browser target")
    sub.choices["mark-submitted"].add_argument("--evidence", default="")
    sub.choices["mark-unknown"].add_argument("--reason", default="")
    sub.choices["block"].add_argument("--reason", required=True)
    for name in ("resume", "verify"):
        sub.choices[name].add_argument("--remote", default="")
        sub.choices[name].add_argument("--local-repo", default="")
        sub.choices[name].add_argument("--rev", default="")
        sub.choices[name].add_argument("--fixture", action="store_true")
        sub.choices[name].add_argument("--timeout-seconds", type=positive_timeout, default=30.0)
    sub.choices["resume"].add_argument("--verify", action="store_true")
    for name in ("status", "resume", "report", "doctor", "inspect"):
        item = sub.choices[name]
        item.add_argument("--cross-workspace", action="store_true")
        item.add_argument("--receipt", help="saved six-field receipt or successful prepare JSON; conflicts fail closed")
        item.add_argument("--expect-request-sha256", default=None)
        item.add_argument("--expect-repository", default=None)
        item.add_argument("--expect-baseline", default=None)
        item.add_argument("--expect-branch", default=None)
        item.add_argument("--expect-ledger-id", default=None)

    target_check = sub.add_parser("check-target", allow_abbrev=False)
    target_check.add_argument("--target", required=True)
    target_check.add_argument("--observation", required=True)
    target_check.set_defaults(func=cmd_check_target)

    selection_check = sub.add_parser("check-selection", allow_abbrev=False)
    selection_check.add_argument("--evidence", required=True)
    selection_check.add_argument("--model", required=True, type=ui_label)
    selection_check.add_argument("--effort", required=True, type=ui_label)
    selection_check.set_defaults(func=cmd_check_selection)

    identify = sub.add_parser("identify", allow_abbrev=False)
    identify.add_argument("--trusted-manifest-sha256", help="independently trusted release manifest byte hash")
    identify.set_defaults(func=cmd_identify)

    route = sub.add_parser("route", allow_abbrev=False)
    route.add_argument("--small-edit", action="store_true")
    route.add_argument("--live-ui", action="store_true")
    route.add_argument("--short-local", action="store_true")
    route.add_argument("--frequent-roundtrip", action="store_true")
    route.add_argument("--permission-blocked", action="store_true")
    route.set_defaults(func=cmd_route)
    return main


def main(argv: list[str] | None = None) -> int:
    configure_output()
    args = parser().parse_args(argv)
    # New views own their safe errors and export schema. Never enrich an export
    # with emit(), which intentionally includes private local paths for legacy calls.
    if args.command in {"doctor", "inspect", "demo"}:
        return args.func(args)
    if args.command in {"claim", "mark-submitted", "mark-unknown", "block", "prompt", "verify"} and not args.task:
        return emit({"error": "--task is required", "do_not_resend": True, "replacement_prepared": False}, 1)
    try:
        receipts.apply_file(args)
    except receipts.ReceiptInputError as exc:
        return emit({"error": str(exc), "receipt_mismatches": exc.mismatches,
                     "do_not_resend": True, "replacement_prepared": False,
                     "replacement_ledger_created": False, "ledger_mutated": False,
                     "review_approved": False,
                     **contract_fields(None, None, command_succeeded=False, verification_attempt_succeeded=False)}, 3)
    if hasattr(args, "ledger") and Path(args.ledger).is_file():
        return emit({"error": "--ledger requires a directory, not ledger.json",
                     "provided": str(Path(args.ledger).resolve()), "ledger_argument_kind": "directory_required",
                     "do_not_resend": True, "replacement_prepared": False, "ledger_mutated": False}, 3)
    try:
        if args.command == "verify" or (args.command == "resume" and args.verify):
            with process_runtime.operation_budget(args.timeout_seconds):
                return args.func(args)
        return args.func(args)
    except bridge_v2.TaskNotFound:
        return emit(
            {
                "error": "task_not_found",
                "task_id": args.task,
                "do_not_resend": True,
                "replacement_prepared": False,
                "replacement_ledger_created": False,
                "review_approved": False,
                "current_verification": False,
                **contract_fields(None, None, command_succeeded=False, verification_attempt_succeeded=False),
            },
            3,
        )
    except bridge_v2.LedgerMissing as exc:
        return emit(
            {
                "error": "ledger_missing",
                "resolved_ledger_path": str(exc),
                "do_not_resend": True,
                "review_approved": False,
                "replacement_ledger_created": False,
                "replacement_prepared": False,
                "command_succeeded": False,
                "verification_attempt_succeeded": False,
                "current_delivery_valid": False,
                "stored_delivery_valid": False,
                "stored_review_approved": False,
                "exit_is_not_approval": True,
            },
            3,
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except bridge_v2.LockTimeout:
        print(json.dumps({"error": "lock_timeout"}))
        raise SystemExit(5)
    except bridge_v2.StaleRecord:
        print(json.dumps({"error": "stale_record", "do_not_resend": True}))
        raise SystemExit(2)
    except subprocess.CalledProcessError:
        raise
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1)

"""Read-only installation/task diagnostics. Never instantiate the mutating Ledger.

Normal output is local-only and may show explicit paths. Export is a NEW object
built from typed, enumerated fields, never a redacted copy of arbitrary input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import bridge_v2
import deploy
import git_verify
import process_runtime
import receipts
import runtime_identity

MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_LEDGER_NESTING = 64
MIN_GIT = process_runtime.MIN_GIT
PROVENANCE = {"VERIFIED", "UNMANIFESTED", "UNTRUSTED_MANIFEST", "MISMATCH",
              "TRUST_ANCHOR_MISMATCH", "UNREADABLE_OR_INVALID_SOURCE"}
INTEGRITY = {"VERIFIED", "NOT_CURRENT", "FAILED", "NOT_VERIFIED", "NOT_SEEN", "NOT_CHECKED"}
NEXT = {
    "CHECK_INSTALLATION": "Check the named interpreter, Git and source pin; do not alter an existing task.",
    "LOCATE_ORIGINAL_EVIDENCE": "Locate the original ledger and receipt; do not substitute diagnostic values or create another task.",
    "HOST_CONFIRM_BEFORE_CLAIM": "The host must confirm login, tools, page and selection before the original task's normal claim flow. This view grants no send permission.",
    "OBSERVE_SAME_TASK_NO_RESEND": "Observe the same task and its original receipt within the existing deadline. Do not resend or prepare a replacement.",
    "STOP_NO_RESEND": "Stop sending and preserve the original receipt and evidence for review. Do not resend, reset a claim, renew the deadline or create a replacement.",
    "REVIEW_EXISTING_DELIVERY": "Review the existing report and any independent QA. Delivery integrity and exit status are not write or merge authorization.",
    "CHECK_ACCESS_SAME_TASK": "Check connectivity or access to this task's repository, then explicitly inspect the same task. Do not resend or create a replacement.",
}
PHASES = {"PREPARED": "AWAITING_HOST", "SUBMISSION_UNKNOWN": "SUBMISSION_OUTCOME_UNKNOWN",
          "SUBMITTED": "AWAITING_RESULT", "RESULT_SEEN": "AWAITING_VERIFICATION",
          "VERIFIED": "DELIVERY_RECORDED", "BLOCKED": "TERMINAL_BLOCKED", "TIMEOUT": "TERMINAL_TIMEOUT"}
OBSERVATIONS = {"NOT_REQUESTED", "RESULT_ABSENT", "NETWORK_FAILURE", "AUTH_FAILURE", "REPOSITORY_UNAVAILABLE",
                "FETCH_FAILURE", "HISTORY_INCOMPLETE", "EVIDENCE_INVALID", "LATE_RESULT", "CONTENT_VERIFIED",
                "RESULT_NOT_ACCEPTED", "OPERATION_TIMEOUT", "CANCELLED", "REMOTE_MISMATCH", "LOCAL_REPO_INVALID"}
CAUSES = {"NO_RESULT_RECORDED", "SUBMISSION_UNKNOWN", "DEADLINE_REACHED", "BLOCKED", "DELIVERY_RECORDED",
          "PREPARED", "RESULT_SEEN", "NETWORK_FAILURE", "AUTH_FAILURE", "REPOSITORY_UNAVAILABLE", "RESULT_ABSENT",
          "OPERATION_TIMEOUT", "EVIDENCE_INVALID", "VERIFICATION_NOT_CURRENT", "CANCELLED"}
KNOWN_CAUSES = {
    "report sha256 mismatch": "REPORT_HASH_MISMATCH",
    "report hash mismatch": "REPORT_HASH_MISMATCH",
    "task, nonce, request sha, or baseline mismatch": "RESULT_IDENTITY_MISMATCH",
    "diff or intermediate commit left the allowlist": "SCOPE_VIOLATION",
    "result tree or history contains a non-regular file mode": "FILE_MODE_INVALID",
    "merge history is not accepted; intermediate changes must stay visible": "MERGE_HISTORY",
    "result json rejected": "RESULT_JSON_INVALID",
    "history command failed; fail closed": "HISTORY_CHECK_FAILED",
}
CAUSES.update(KNOWN_CAUSES.values())

CHECK_IDS = {"operating_system", "python", "git", "entrypoint", "source", "expected_digest", "ledger", "skill"}
CHECK_CODES = {"supported_windows", "unsupported_os", "supported_python", "unsupported_python", "git_ok",
               "git_missing", "git_unreadable", "git_too_old", "entrypoint_ok", "entrypoint_mismatch",
               "source_verified", "source_unmanifested", "source_untrusted", "source_invalid", "digest_ok",
               "digest_mismatch", "not_requested", "ledger_readable", "ledger_missing", "ledger_unreadable",
               "ledger_invalid", "ledger_path_invalid", "skill_match", "skill_unresolved", "skill_unreadable"}
ERROR_CODES = {"ledger_missing", "ledger_unreadable", "ledger_invalid", "ledger_path_invalid", "ledger_required",
               "task_required", "task_not_found", "no_tasks", "multiple_tasks", "receipt_field_required",
               "receipt_invalid", "receipt_mismatch", "evidence_invalid", "export_refused"}


class DiagnosticError(ValueError):
    def __init__(self, code: str, fields: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.fields = fields or []


def _no_constant(value: str):
    raise ValueError("non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _bounded_json(text: str):
    # Reuse the protocol scanner; braces inside JSON strings are not nesting.
    # Run BEFORE parsing, recursive binding checks, or copying any input.
    if bridge_v2.json_nesting_exceeds(text, MAX_LEDGER_NESTING):
        raise DiagnosticError("ledger_invalid")
    return json.loads(text, object_pairs_hook=receipts.unique_object,
                      parse_constant=_no_constant, parse_float=_finite_float)


def _record_types(record: dict) -> None:
    """Validate fields consumed by diagnostics, without coercion or migration.

    Unknown bounded extensions/display hints remain readable and are omitted or
    enumerated by explain/export. Authority and numeric fields are not hints.
    The immutable binding check remains separate from structural validation.
    """
    strings = ("task_id", "nonce", "request_sha256", "request_bytes",
               "source_repository", "baseline_sha", "branch", "state")
    if any(not isinstance(record.get(key), str) for key in strings):
        raise DiagnosticError("ledger_invalid")
    if (record["state"] not in bridge_v2.ALLOWED_STATES
            or type(record.get("send_attempts")) is not int
            or record["send_attempts"] not in (0, 1)):
        raise DiagnosticError("ledger_invalid")
    manifest = _bounded_json(record["request_bytes"])
    if not isinstance(manifest, dict):
        raise DiagnosticError("ledger_invalid")
    for obj in (record, manifest):
        # bool is an int subclass; float('123') must NOT validate a string.
        deadline = obj.get("deadline_at")
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise DiagnosticError("ledger_invalid")
        for key in ("task_id", "nonce", "request_sha256", "source_repository",
                    "result_repository", "source_sha", "baseline_sha", "branch", "mode", "code_branch"):
            if key in obj and obj[key] is not None and not isinstance(obj[key], str):
                raise DiagnosticError("ledger_invalid")
        for key in ("allowed_paths", "code_paths"):
            if key in obj and (not isinstance(obj[key], list)
                               or any(not isinstance(v, str) for v in obj[key])):
                raise DiagnosticError("ledger_invalid")
        for key in ("allow_pr", "code_write_permission", "review_approved"):
            if key in obj and type(obj[key]) is not bool:
                raise DiagnosticError("ledger_invalid")
    for obj in (record, record.get("production_snapshot")):
        if obj is None:
            continue
        if not isinstance(obj, dict):
            raise DiagnosticError("ledger_invalid")
        for key in ("verification_generation", "current_approval_generation", "revision"):
            value = obj.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise DiagnosticError("ledger_invalid")


def read_ledger(root: str | Path) -> tuple[Path, dict]:
    """One bounded byte snapshot; no lock file, mkdir, repair, creation, expiry transition or state mutation."""
    try:
        directory = deploy._safe_location(Path(root))
        if directory.exists() and not directory.is_dir():
            raise DiagnosticError("ledger_path_invalid")
        path = directory / "ledger.json"
        if not path.exists():
            raise DiagnosticError("ledger_missing")
        runtime_identity.reject_link(path)
        if not stat.S_ISREG(path.stat().st_mode):
            raise DiagnosticError("ledger_path_invalid")
        with path.open("rb") as stream:
            raw = stream.read(MAX_LEDGER_BYTES + 1)
        if len(raw) > MAX_LEDGER_BYTES:
            raise DiagnosticError("ledger_invalid")
        data = _bounded_json(raw.decode("utf-8"))
        if (not isinstance(data, dict) or not isinstance(data.get("tasks"), dict)
                or any(not isinstance(k, str) or not isinstance(v, dict) for k, v in data["tasks"].items())):
            raise DiagnosticError("ledger_invalid")
        for record in data["tasks"].values():
            _record_types(record)
        return path, data
    except DiagnosticError:
        raise
    except PermissionError as exc:
        raise DiagnosticError("ledger_unreadable") from exc
    except OSError as exc:
        raise DiagnosticError("ledger_unreadable") from exc
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise DiagnosticError("ledger_invalid") from exc


def select_snapshot(args: argparse.Namespace) -> tuple[Path, str, dict]:
    if not getattr(args, "ledger", None):
        raise DiagnosticError("ledger_required")
    try:
        receipts.apply_file(args)
    except receipts.ReceiptInputError as exc:
        raise DiagnosticError("receipt_invalid", [x["field"] for x in exc.mismatches]) from exc
    path, data = read_ledger(args.ledger)
    tasks = data["tasks"]
    task = getattr(args, "task", None)
    if getattr(args, "cross_workspace", False):
        if not task:
            raise DiagnosticError("task_required")
        if any(not getattr(args, name, None) for name in receipts.ARGUMENTS):
            raise DiagnosticError("receipt_field_required")
    if task is None:
        if not tasks:
            raise DiagnosticError("no_tasks")
        if len(tasks) != 1:
            raise DiagnosticError("multiple_tasks")
        task = next(iter(tasks))
    if task not in tasks:
        raise DiagnosticError("task_not_found")
    # json.loads created an owned snapshot. No downstream reader mutates it;
    # another recursive deep copy is unnecessary and can itself exhaust recursion.
    record = tasks[task]
    ledger_id = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    differences = receipts.mismatches(record, args, ledger_id)
    if differences:
        raise DiagnosticError("receipt_mismatch", [x["field"] for x in differences])
    try:
        invalid = (not bridge_v2.UUID_RE.fullmatch(task) or bridge_v2.identity_error(task, record)
                   or bridge_v2.binding_error(record) or record.get("state") not in bridge_v2.ALLOWED_STATES
                   or type(record.get("send_attempts")) is not int or record["send_attempts"] not in (0, 1)
                   or isinstance(record.get("deadline_at"), bool)
                   or not math.isfinite(float(record["deadline_at"])))
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        invalid = True
    if invalid:
        raise DiagnosticError("evidence_invalid")
    return path, task, record


def _reason(record: dict, expired: bool) -> str:
    if expired or record["state"] == "TIMEOUT":
        return "DEADLINE_REACHED"
    notes = record.get("notes")
    latest = notes[-1] if isinstance(notes, list) and notes and isinstance(notes[-1], str) else ""
    observation = record.get("last_observation")
    if latest in KNOWN_CAUSES:
        return KNOWN_CAUSES[latest]
    for token, code in (("network_failure", "NETWORK_FAILURE"), ("auth_failure", "AUTH_FAILURE"),
                        ("repository_unavailable", "REPOSITORY_UNAVAILABLE"), ("branch_absent", "RESULT_ABSENT"),
                        ("timeout", "OPERATION_TIMEOUT"), ("cancelled", "CANCELLED")):
        if (isinstance(observation, str) and token in observation) or latest.startswith("fetch " + token):
            return code
    if isinstance(observation, str) and observation in {"binding_error", "identity_error", "reader_error", "fixture_failed"}:
        return "EVIDENCE_INVALID"
    if record["state"] == "BLOCKED":
        return "BLOCKED"
    if record["state"] == "VERIFIED" and not bridge_v2.delivery_current(record) and observation != "fixture_checked":
        return "VERIFICATION_NOT_CURRENT"
    return {"PREPARED": "PREPARED", "SUBMISSION_UNKNOWN": "SUBMISSION_UNKNOWN", "RESULT_SEEN": "RESULT_SEEN",
            "VERIFIED": "DELIVERY_RECORDED"}.get(record["state"], "NO_RESULT_RECORDED")


def explain(task: str, record: dict, *, now: float | None = None) -> dict:
    elapsed = (time.time() if now is None else now) > record["deadline_at"]
    expired = elapsed and record["state"] not in {"VERIFIED", "BLOCKED"}
    current = bridge_v2.delivery_current(record, task)
    approved = bridge_v2.current_approval(record, task)
    cause = _reason(record, expired)
    next_code = ("STOP_NO_RESEND" if expired or record["state"] in {"BLOCKED", "TIMEOUT"}
                 else "REVIEW_EXISTING_DELIVERY" if current or (record.get("evidence_origin") == "fixture" and record.get("last_observation") == "fixture_checked")
                 else "HOST_CONFIRM_BEFORE_CLAIM" if record["state"] == "PREPARED"
                 else "CHECK_ACCESS_SAME_TASK" if cause in {"NETWORK_FAILURE", "AUTH_FAILURE", "REPOSITORY_UNAVAILABLE"}
                 else "OBSERVE_SAME_TASK_NO_RESEND")
    return {
        "task_present": True, "state": record["state"], "phase": "EXPIRED_UNFINISHED" if expired else PHASES[record["state"]],
        "cause": cause, "cause_source": "ledger_snapshot_and_deadline", "deadline_elapsed": elapsed,
        "evidence_fields": ["state", "deadline_at", "last_observation", "notes:last_known_code"],
        "expired_unfinished": expired, "send_attempts": record["send_attempts"],
        "task_succeeded": current, "current_delivery_valid": current,
        "stored_verification": {
            "delivery_integrity": _enum(record.get("delivery_integrity"), INTEGRITY),
            "evidence_origin": _enum(record.get("evidence_origin"), {"github", "fixture"}),
            "review_verdict": _enum(record.get("review_verdict"), bridge_v2.RESULT_STATUSES, "NOT_AVAILABLE"),
            "review_approved": approved, "is_current": current,
        },
        "review_approved": approved, "do_not_resend": True, "send_authorized": False,
        "next_step_code": next_code, "next_step": NEXT[next_code],
        "approval_note": "Stored GitHub review approval is not human permission to write or merge.",
    }


def _base(command: str) -> dict:
    return {"schema_version": 1, "command": command, "command_succeeded": True,
            "ledger_mutated": False, "replacement_prepared": False, "replacement_ledger_created": False,
            "automatic_high_risk_continue": False, "exit_is_not_approval": True, "network_attempted": False,
            "host_checks": {"login": "NOT_CHECKED", "browser_tool": "NOT_CHECKED", "current_page": "NOT_CHECKED"}}


def _selection_failure(error: DiagnosticError) -> dict:
    return {"command_succeeded": False, "error": error.code, "mismatch_fields": error.fields,
            "task_present": False, "task_succeeded": False, "review_approved": False,
            "current_delivery_valid": False, "do_not_resend": True, "send_authorized": False,
            "next_step_code": "LOCATE_ORIGINAL_EVIDENCE", "next_step": NEXT["LOCATE_ORIGINAL_EVIDENCE"]}


def _observe(record: dict, args: argparse.Namespace) -> dict:
    local = getattr(args, "local_repo", None)
    remote = getattr(args, "remote", None)
    if local and not getattr(args, "fixture", False):
        return {"outcome": "LOCAL_REPO_INVALID", "command_succeeded": False}
    # Unlike formal verify, remote viewing never accepts arbitrary local/file URLs.
    repository = record.get("result_repository") or record["source_repository"]
    if remote and remote not in {f"https://github.com/{repository}", f"https://github.com/{repository}.git"}:
        return {"outcome": "REMOTE_MISMATCH", "command_succeeded": False}
    try:
        with process_runtime.operation_budget(args.timeout_seconds):
            if local:
                root = deploy._safe_location(Path(local))
                if not root.is_dir() or process_runtime.git(["rev-parse", "--git-dir"], repo=root, check=False).returncode:
                    return {"outcome": "LOCAL_REPO_INVALID", "command_succeeded": False}
                observation = git_verify.read_delivery(root, record["baseline_sha"], record["branch"], record["allowed_paths"])
            else:
                parent = deploy._safe_location(Path(tempfile.gettempdir()))
                forbidden = [Path(__file__).resolve().parents[2], Path(args.ledger).resolve()]
                if any(parent == item or item in parent.parents for item in forbidden):
                    return {"outcome": "FETCH_FAILURE", "command_succeeded": False}
                with tempfile.TemporaryDirectory(prefix="pro-bridge-inspect-", dir=parent) as temporary:
                    with process_runtime.frozen_git_config(repo=Path(temporary)):
                        fetched = git_verify.fetch_result(remote, record["branch"], Path(temporary), record["baseline_sha"])
                        if fetched["kind"] != "ok":
                            outcome = {"branch_absent": "RESULT_ABSENT", "network_failure": "NETWORK_FAILURE",
                                       "auth_failure": "AUTH_FAILURE", "repository_unavailable": "REPOSITORY_UNAVAILABLE",
                                       "remote_mismatch": "REMOTE_MISMATCH",
                                       "timeout": "OPERATION_TIMEOUT", "history_incomplete": "HISTORY_INCOMPLETE"}.get(fetched["kind"], "FETCH_FAILURE")
                            return {"outcome": outcome, "command_succeeded": outcome == "RESULT_ABSENT"}
                        observation = git_verify.read_delivery(Path(temporary), record["baseline_sha"], record["branch"], record["allowed_paths"])
            if observation is None:
                return {"outcome": "RESULT_ABSENT", "command_succeeded": True}
            decision = git_verify.evaluate(record, observation, time.time())
            late = record["state"] in {"BLOCKED", "TIMEOUT"} or (
                time.time() > record["deadline_at"] and record["state"] != "VERIFIED")
            outcome = ("LATE_RESULT" if late else "CONTENT_VERIFIED" if decision["verified"]
                       else "HISTORY_INCOMPLETE" if observation.get("history_incomplete")
                       else "EVIDENCE_INVALID" if decision["state"] == "BLOCKED"
                       else "RESULT_NOT_ACCEPTED")
            return {"outcome": outcome, "command_succeeded": outcome in {"LATE_RESULT", "CONTENT_VERIFIED", "RESULT_NOT_ACCEPTED"},
                    "reason_code": next((KNOWN_CAUSES[n] for n in decision.get("notes", []) if n in KNOWN_CAUSES), "NONE"),
                    "content_check": "VERIFIED" if decision["verified"] else "NOT_VERIFIED",
                    "review_verdict": decision.get("review_verdict") if decision.get("review_verdict") in bridge_v2.RESULT_STATUSES else "NOT_AVAILABLE",
                    "evidence_origin": "fixture" if local else "remote_read_only",
                    "formal_verification_performed": False, "review_approved": False,
                    "ledger_state_unchanged": True}
    except subprocess.TimeoutExpired:
        return {"outcome": "OPERATION_TIMEOUT", "command_succeeded": False}
    except KeyboardInterrupt:
        return {"outcome": "CANCELLED", "command_succeeded": False}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"outcome": "FETCH_FAILURE", "command_succeeded": False}


def inspect_task(args: argparse.Namespace) -> tuple[dict, int]:
    result = _base("inspect")
    try:
        _, task, record = select_snapshot(args)
        result.update(explain(task, record))
    except DiagnosticError as exc:
        result.update(_selection_failure(exc))
        return result, 3
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        result.update(_selection_failure(DiagnosticError("evidence_invalid")))
        return result, 3
    result["observation"] = {"outcome": "NOT_REQUESTED"}
    if getattr(args, "remote", None) or getattr(args, "local_repo", None):
        result["observation"] = _observe(record, args)
        outcome = result["observation"]["outcome"]
        result["network_attempted"] = bool(args.remote) and outcome != "REMOTE_MISMATCH"
        result["command_succeeded"] = result["observation"]["command_succeeded"]
        if outcome in {"NETWORK_FAILURE", "AUTH_FAILURE", "REPOSITORY_UNAVAILABLE", "FETCH_FAILURE"} and result["next_step_code"] != "STOP_NO_RESEND":
            result.update(next_step_code="CHECK_ACCESS_SAME_TASK", next_step=NEXT["CHECK_ACCESS_SAME_TASK"])
        # A new observation is NEVER approval, even if an older snapshot was approved.
        result["review_approved"] = False
        result["task_succeeded"] = False
        result["current_delivery_valid"] = False
        result["observation_is_not_formal_verification"] = True
    code = 0 if result["command_succeeded"] else 130 if result["observation"]["outcome"] == "CANCELLED" else 2
    return result, code


def check(identifier: str, status: str, code: str, message: str) -> dict:
    return {"id": identifier, "status": status, "code": code, "message": message}


def environment_checks() -> tuple[list[dict], dict]:
    checks = [check("operating_system", "PASS" if os.name == "nt" else "FAIL",
                    "supported_windows" if os.name == "nt" else "unsupported_os", "Ledger/demo support is Windows only."),
              check("python", "PASS" if sys.version_info[:2] == (3, 12) else "FAIL",
                    "supported_python" if sys.version_info[:2] == (3, 12) else "unsupported_python", "Supported interpreter: Python 3.12.")]
    version = None
    if shutil.which("git") is None:
        checks.append(check("git", "FAIL", "git_missing", "Git was not found on PATH."))
    else:
        try:
            output = process_runtime.git(["--version"], timeout=3).stdout.decode("ascii", "replace")
            match = re.fullmatch(r"git version (\d+)\.(\d+)\.(\d+)(?:[^\r\n]*)\s*", output)
            if not match:
                raise ValueError("unrecognized version")
            version = list(map(int, match.groups()))
            ok = tuple(version) >= MIN_GIT
            checks.append(check("git", "PASS" if ok else "FAIL", "git_ok" if ok else "git_too_old", "Git 2.32 or later is required."))
        except (OSError, ValueError, subprocess.SubprocessError):
            checks.append(check("git", "FAIL", "git_unreadable", "Git version check failed or timed out."))
    system = platform.system()
    return checks, {"os": system if system in {"Windows", "Linux", "Darwin"} else "Other",
                    "python": list(sys.version_info[:3]), "git": version}


def doctor(args: argparse.Namespace, identity: dict) -> tuple[dict, int]:
    result = _base("doctor")
    checks, environment = environment_checks()
    root = Path(identity["runtime_source_root"])
    entry = (root / "tools/pro_bridge/cli.py").resolve()
    modules = identity.get("runtime_module_files", {})
    entry_ok = entry.is_file() and isinstance(modules, dict) and modules.get("cli") == str(entry) and all(
        isinstance(name, str) and isinstance(path, str) and Path(path).resolve() == root / "tools/pro_bridge" / (name + ".py")
        for name, path in modules.items())
    if getattr(args, "expect_entrypoint", None):
        entry_ok = entry_ok and Path(args.expect_entrypoint).resolve() == entry
    checks.append(check("entrypoint", "PASS" if entry_ok else "FAIL", "entrypoint_ok" if entry_ok else "entrypoint_mismatch",
                        "Entry and imported modules must belong to this same installation."))
    status = identity["provenance_status"]
    source_code = {"VERIFIED": "source_verified", "UNMANIFESTED": "source_unmanifested",
                   "UNTRUSTED_MANIFEST": "source_untrusted"}.get(status, "source_invalid")
    checks.append(check("source", "PASS" if status == "VERIFIED" else "WARN" if status in {"UNMANIFESTED", "UNTRUSTED_MANIFEST"} else "FAIL",
                        source_code, "Source provenance requires matching bytes and an independently trusted manifest pin."))
    if getattr(args, "expect_runtime_digest", None):
        ok = args.expect_runtime_digest == identity.get("runtime_source_digest")
        checks.append(check("expected_digest", "PASS" if ok else "FAIL", "digest_ok" if ok else "digest_mismatch", "Compare with the explicitly supplied runtime digest."))
    if getattr(args, "ledger", None):
        try:
            path, data = read_ledger(args.ledger)
            checks.append(check("ledger", "PASS", "ledger_readable", "Explicit ledger passes bounded JSON and consumed-field type checks; no state transition was performed."))
            result["ledger"] = {"path": str(path), "task_count": len(data["tasks"]), "readable": True}
        except DiagnosticError as exc:
            checks.append(check("ledger", "FAIL", exc.code, "The explicit ledger is missing, unreadable or invalid; it was not created or repaired."))
    else:
        checks.append(check("ledger", "NOT_CHECKED", "not_requested", "No explicit ledger requested; environment fallback is ignored."))
    # Explicit identity constraints request a task check even without a selector.
    # Keep the existing sole-task selection rule outside cross-workspace mode.
    if (getattr(args, "cross_workspace", False)
            or any(getattr(args, name, None) is not None for name in (*receipts.ARGUMENTS, "receipt"))):
        try:
            # Legacy receipt comparison ignores empty strings. An explicitly
            # empty expectation must not silently turn a requested check off.
            if any(getattr(args, name, None) == "" for name in receipts.ARGUMENTS[1:]):
                raise DiagnosticError("receipt_field_required")
            _, task, record = select_snapshot(args)
            result["task"] = explain(task, record)
        except DiagnosticError as exc:
            result["task"] = _selection_failure(exc)
        except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
            result["task"] = _selection_failure(DiagnosticError("evidence_invalid"))
    if getattr(args, "skill", None):
        try:
            skill = deploy.inspect_skill(Path(args.skill), root)
            ok = skill["status"] == "MATCH"
            checks.append(check("skill", "PASS" if ok else "FAIL", "skill_match" if ok else "skill_unresolved",
                                "Literal Python script-operand check only; not host execution attestation."))
            result["skill"] = skill
        except (OSError, ValueError):
            checks.append(check("skill", "FAIL", "skill_unreadable", "Explicit skill could not be inspected."))
    else:
        checks.append(check("skill", "NOT_CHECKED", "not_requested", "No explicit skill was read."))
    result.update(environment=environment, checks=checks, source=identity,
                  entrypoint=str(entry), imported_modules=modules,
                  next_step_code="CHECK_INSTALLATION", next_step=NEXT["CHECK_INSTALLATION"])
    requested = [item for item in checks if item["status"] != "NOT_CHECKED"]
    passed = all(item["status"] == "PASS" for item in requested) and result.get("task", {}).get("command_succeeded", True)
    result["checks_passed"] = passed
    result["assessment"] = "PASS" if passed else "FAIL" if any(c["status"] == "FAIL" for c in requested) or result.get("task", {}).get("command_succeeded") is False else "INCOMPLETE"
    return result, 0 if passed else 2


def _enum(value, choices: set[str], fallback: str = "UNKNOWN") -> str:
    return value if isinstance(value, str) and value in choices else fallback


def _numbers(value) -> list[int] | None:
    return list(value) if isinstance(value, list) and len(value) == 3 and all(type(n) is int and 0 <= n <= 100000 for n in value) else None


def _safe_task(task: dict) -> dict:
    result = {name: task.get(name) is True for name in ("task_present", "task_succeeded", "current_delivery_valid",
              "deadline_elapsed", "expired_unfinished", "review_approved")}
    result.update(state=_enum(task.get("state"), bridge_v2.ALLOWED_STATES),
                  phase=_enum(task.get("phase"), set(PHASES.values()) | {"EXPIRED_UNFINISHED"}),
                  cause=_enum(task.get("cause"), CAUSES),
                  next_step_code=_enum(task.get("next_step_code"), set(NEXT)), do_not_resend=True, send_authorized=False)
    attempts = task.get("send_attempts")
    result["send_attempts"] = attempts if type(attempts) is int and attempts in (0, 1) else None
    stored = task.get("stored_verification")
    if isinstance(stored, dict):
        result["stored_verification"] = {"delivery_integrity": _enum(stored.get("delivery_integrity"), INTEGRITY),
            "evidence_origin": _enum(stored.get("evidence_origin"), {"github", "fixture"}),
            "review_verdict": _enum(stored.get("review_verdict"), bridge_v2.RESULT_STATUSES),
            "review_approved": stored.get("review_approved") is True, "is_current": stored.get("is_current") is True}
    result["error"] = _enum(task.get("error"), ERROR_CODES, "NONE")
    return result


def sanitized_export(report: dict) -> dict:
    """White-listed reconstruction: no free-form strings, task IDs or user paths."""
    result = {"diagnostic_schema_version": 1, "bridge_version": bridge_v2.BRIDGE_VERSION,
              "command": _enum(report.get("command"), {"doctor", "inspect"}),
              "command_succeeded": report.get("command_succeeded") is True,
              "assessment": _enum(report.get("assessment"), {"PASS", "FAIL", "INCOMPLETE"}),
              "host_checks": {"login": "NOT_CHECKED", "browser_tool": "NOT_CHECKED", "current_page": "NOT_CHECKED"},
              "automatic_high_risk_continue": False, "send_authorized": False,
              "redaction": {"method": "typed_field_allowlist_v1",
                  "retained": ["software_versions", "source_digests", "check_codes", "state_and_verdict_enums", "safety_booleans"],
                  "omitted": ["prompts", "reports", "cookies", "tokens", "chat_urls", "user_paths", "repository_names",
                              "task_ids", "nonces", "request_hashes", "branches", "raw_errors", "notes", "skill_commands"],
                  "generalized": ["operating_system_family", "task_phase", "next_step_code"], "uploaded": False}}
    environment = report.get("environment")
    if isinstance(environment, dict):
        result["environment"] = {"os": _enum(environment.get("os"), {"Windows", "Linux", "Darwin", "Other"}),
                                 "python": _numbers(environment.get("python")), "git": _numbers(environment.get("git"))}
    source = report.get("source")
    if isinstance(source, dict):
        result["source"] = {"provenance_status": _enum(source.get("provenance_status"), PROVENANCE)}
        for key, pattern in (("runtime_source_digest", runtime_identity.HEX64), ("source_manifest_sha256", runtime_identity.HEX64),
                             ("source_commit", runtime_identity.COMMIT)):
            value = source.get(key)
            result["source"][key] = value if isinstance(value, str) and pattern.fullmatch(value) else None
    result["checks"] = [{"id": item["id"], "status": _enum(item.get("status"), {"PASS", "WARN", "FAIL", "NOT_CHECKED"}),
                         "code": _enum(item.get("code"), CHECK_CODES)} for item in report.get("checks", [])
                        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] in CHECK_IDS]
    task = report.get("task", report if report.get("command") == "inspect" else None)
    if isinstance(task, dict):
        result["task"] = _safe_task(task)
    observation = report.get("observation")
    if isinstance(observation, dict):
        result["observation"] = {"outcome": _enum(observation.get("outcome"), OBSERVATIONS), "review_approved": False,
                                 "reason_code": _enum(observation.get("reason_code"), set(KNOWN_CAUSES.values()), "NONE"),
                                 "formal_verification_performed": False}
    return result


def present(report: dict, code: int, args: argparse.Namespace, source_root: Path) -> int:
    output = report
    if getattr(args, "export", None):
        try:
            destination = deploy._safe_location(Path(args.export))
            forbidden = [source_root.resolve()]
            if getattr(args, "ledger", None):
                forbidden.append(Path(args.ledger).resolve())
            if any(destination == root or root in destination.parents for root in forbidden):
                raise ValueError("export must be outside installation and ledger")
            output = sanitized_export(report)
            # Exclusive create; no directory creation or replacement of user evidence.
            with destination.open("xb") as stream:
                stream.write((json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
        except (OSError, ValueError):
            output = {"error": "export_refused", "command_succeeded": False, "do_not_resend": True,
                      "automatic_high_risk_continue": False, "export_written": False}
            code = 3
    if getattr(args, "json", False):
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    elif getattr(args, "export", None):
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"{report['command'].upper()}: command_succeeded={str(report['command_succeeded']).lower()}")
        for item in report.get("checks", []):
            print(f"[{item['status']}] {item['id']}: {item['code']} — {item['message']}")
        if "entrypoint" in report:
            print("Actual entrypoint: " + report["entrypoint"])
            for name, path in sorted(report.get("imported_modules", {}).items()):
                print(f"Imported {name}: {path}")
        task = report.get("task", report if report["command"] == "inspect" else None)
        if task:
            print(json.dumps(task, ensure_ascii=False, sort_keys=True, indent=2))
        print("Host login, browser tool and page: NOT_CHECKED; host confirmation required.")
        print("No send, state transition, approval or automatic continuation is authorized by this view.")
    return code

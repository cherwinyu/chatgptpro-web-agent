"""Disposable, explicitly simulated walkthrough using the REAL protocol and CLI."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

import diagnostics
import process_runtime

SCENARIOS = ("success", "tampered-report", "duplicate-claim")
LABEL = "OFFLINE SIMULATION / 离线模拟"


class DemoFailure(RuntimeError):
    pass


def exercise(root: Path, scenario: str, *, progress=None, verdict: str = "REQUEST_CHANGES") -> dict:
    """Only call with a newly created owned root. Tests may inject a test-only lock.

    Production CLI never bypasses the Windows preflight or replaces the mutex.
    """
    import cli  # Avoid importing the entrypoint while it is importing this module.
    if scenario not in SCENARIOS:
        raise ValueError("unknown scenario")
    repository = root / "演示 repository"
    repository.mkdir()
    ledger = root / "演示 ledger"
    template = root / "empty-git-template"
    template.mkdir()
    environment = process_runtime.git_environment(offline_root=root)
    events = []

    def event(stage: str, **details) -> None:
        item = {"mode": "OFFLINE_SIMULATION", "stage": stage, **details}
        events.append(item)
        if progress:
            progress(f"{scenario}: {stage} " + json.dumps(details, ensure_ascii=False, sort_keys=True))

    def invoke(command: str, *arguments: str) -> tuple[int, dict]:
        capture = io.StringIO()
        with redirect_stdout(capture):
            code = cli.main([command, "--ledger", str(ledger), *arguments])
        try:
            payload = json.loads(capture.getvalue())
        except (TypeError, ValueError) as exc:
            raise DemoFailure("CLI did not return its JSON contract") from exc
        return code, payload

    def git(*arguments: str) -> bytes:
        return process_runtime.git(["-c", "user.name=Offline Simulation", "-c", "user.email=offline@example.invalid",
            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={template}", *arguments], repo=repository).stdout

    with process_runtime.operation_budget(90), process_runtime.git_context(environment):
        git("init", "-b", "main")
        (repository / "README.md").write_bytes(b"# OFFLINE SIMULATION\nDisposable fixture, not a consumer project.\n")
        git("add", "README.md")
        git("commit", "-m", "OFFLINE SIMULATION baseline")
        baseline = git("rev-parse", "HEAD").decode("ascii").strip()
        event("isolated_repository_created", network_allowed=False)
        code, prepared = invoke("prepare", "--task-type", "READ_ONLY_REVIEW", "--repository", "offline-simulation/demo",
            "--source-sha", baseline, "--source-ref", "main", "--orchestration", "OFFLINE_SIMULATION",
            "--read", "README.md", "--ui-model-label", "OFFLINE_SIMULATION", "--ui-effort-label", "NOT_A_MODEL",
            "--deadline-seconds", "180")
        if code != 0:
            raise DemoFailure("prepare refused a disposable fixture")
        task = prepared["task_id"]
        (root / "prepare-receipt.json").write_text(json.dumps(prepared["receipt"], indent=2), encoding="utf-8")
        event("prepare", exit_code=code, state=prepared["state"])
        claim_code, claimed = invoke("claim", "--task", task)
        if claim_code != 0:
            raise DemoFailure("first simulated claim was refused")
        event("simulated_claim", exit_code=claim_code, state=claimed["state"], real_browser_send=False)
        duplicate_code = None
        if scenario == "duplicate-claim":
            duplicate_code, duplicate = invoke("claim", "--task", task)
            if duplicate_code != 2 or duplicate.get("do_not_resend") is not True:
                raise DemoFailure("duplicate claim was not refused")
            event("duplicate_claim_refused", exit_code=duplicate_code, do_not_resend=True)
        code, submitted = invoke("mark-submitted", "--task", task, "--session-url", "offline://simulation",
                                 "--evidence", "OFFLINE_SIMULATION_ONLY_NO_BROWSER_SEND")
        if code != 0:
            raise DemoFailure("simulated submission marker was refused")
        event("simulated_submission_recorded", exit_code=code, state=submitted["state"], real_browser_send=False)
        _, data = diagnostics.read_ledger(ledger)
        record = data["tasks"][task]
        git("checkout", "-b", record["branch"])
        report = (f"# OFFLINE SIMULATION — {scenario}\n\nThis is a local fixture, not a ChatGPT response.\n"
                  f"Simulated review verdict: {verdict}. No write or merge authorization.\n").encode("utf-8")
        result = {"protocol_version": 2, "task_id": task, "nonce": record["nonce"],
                  "request_sha256": record["request_sha256"], "baseline_sha": baseline, "status": verdict,
                  "report_sha256": hashlib.sha256(report).hexdigest()}
        json_path, report_path = (repository / name for name in record["allowed_paths"])
        json_path.parent.mkdir(parents=True)
        report_path.parent.mkdir(parents=True)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_bytes(report + (b"\nSIMULATED TAMPERING AFTER HASHING\n" if scenario == "tampered-report" else b""))
        git("add", "--", *record["allowed_paths"])
        git("commit", "-m", "OFFLINE SIMULATION result delivery")
        event("local_result_branch_created", report_tampered=scenario == "tampered-report")
        code, verified = invoke("verify", "--task", task, "--local-repo", str(repository), "--fixture")
        expected = 3 if scenario == "tampered-report" else 0
        if code != expected or verified.get("verified") is not (scenario != "tampered-report"):
            raise DemoFailure("real verifier returned an unexpected outcome")
        if verified.get("review_approved") is not False or verified.get("current_verification") is not False or verified.get("evidence_origin") != "fixture":
            raise DemoFailure("fixture evidence escaped its approval boundary")
        _, data = diagnostics.read_ledger(ledger)
        final = data["tasks"][task]
        if final["send_attempts"] != 1 or git("rev-parse", "main").decode().strip() != baseline:
            raise DemoFailure("send count or baseline was modified")
        event("real_git_verifier", exit_code=code, state=final["state"], local_git_integrity=verified["git_content_check"],
              review_verdict=verified["review_verdict"], github_delivery_current=False, review_approved=False)
        return {"mode": "OFFLINE_SIMULATION", "scenario": scenario, "scenario_passed": True,
                "verify_exit_code": code, "duplicate_claim_exit_code": duplicate_code,
                "state": final["state"], "send_attempts": final["send_attempts"],
                "local_git_integrity": verified["git_content_check"], "review_verdict": verified["review_verdict"],
                "github_delivery_current": False, "review_approved": False, "events": events}



def _retry_owned_readonly(root: Path, function, path: str, error: BaseException) -> None:
    """Retry one Windows read-only file unlink, never an ACL/lock failure.

    Reject links/reparse ancestors and multiply linked files before chmod so
    cleanup cannot change permissions on an outside target. No broad retry.
    """
    if not isinstance(error, PermissionError) or function not in (os.unlink, os.remove):
        raise error
    try:
        owned = diagnostics.deploy._safe_location(root)
        item = diagnostics.deploy._safe_location(Path(path))
    except ValueError:
        raise error from None
    info = item.lstat()
    if (owned not in item.parents or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1 or not getattr(info, "st_file_attributes", 0) & 1):
        raise error
    os.chmod(item, info.st_mode | stat.S_IWRITE)
    function(item)


def cleanup_owned(root: Path) -> None:
    """Delete this run's private tree, handling Git's Windows read-only objects."""
    shutil.rmtree(root, onexc=lambda function, path, error: _retry_owned_readonly(root, function, path, error))


def run_demo(args) -> tuple[dict, int]:
    result = {"schema_version": 1, "mode": "OFFLINE_SIMULATION", "command": "demo", "command_succeeded": False,
              "automatic_high_risk_continue": False, "review_approved": False, "web_send_count": 0,
              "network_attempted": False, "real_ledger_used": False, "scenarios": [],
              "explanation": "Local Git integrity is separate from review verdict and GitHub approval. All evidence here is simulated."}
    checks, environment = diagnostics.environment_checks()
    result.update(preflight=checks, environment=environment)
    progress = None if args.json else lambda line: print(f"[{LABEL}] {line}", flush=True)
    if progress:
        progress("Preflight: Windows / Python 3.12 / Git; no login, browser or credentials required.")
    if any(item["status"] != "PASS" for item in checks):
        result["error"] = "unsupported_or_incomplete_environment"
        result["temporary_directory_created"] = False
        return result, 3
    temporary = None
    try:
        parent = deploy_parent(args.temp_parent)
        temporary = Path(tempfile.mkdtemp(prefix="pro-bridge 离线 模拟-", dir=parent))
        result.update(temporary_directory=str(temporary), temporary_directory_created=True)
        if progress:
            progress("Temporary directory: " + str(temporary))
        for scenario in SCENARIOS if args.scenario == "all" else (args.scenario,):
            root = temporary / scenario
            root.mkdir()
            result["scenarios"].append(exercise(root, scenario, progress=progress))
        result["command_succeeded"] = all(case["scenario_passed"] for case in result["scenarios"])
        return result, 0
    except KeyboardInterrupt:
        result["error"] = "cancelled_no_resend"
        return result, 130
    except Exception as exc:
        result["error"] = "offline_demo_failed"
        result["failure_type"] = type(exc).__name__
        return result, 2
    finally:
        if temporary is not None:
            if args.keep:
                result.update(temporary_directory_retained=True, cleanup_succeeded=False)
            else:
                try:
                    cleanup_owned(temporary)
                    result.update(temporary_directory_retained=False, cleanup_succeeded=not temporary.exists())
                except OSError:
                    result.update(temporary_directory_retained=True, cleanup_succeeded=False, command_succeeded=False,
                                  error="cleanup_failed")
            if progress:
                progress(("Retained: " if result["temporary_directory_retained"] else "Cleaned: ") + str(temporary))


def deploy_parent(value: str | None) -> Path | None:
    parent = diagnostics.deploy._safe_location(Path(tempfile.gettempdir() if value is None else value))
    source = Path(__file__).resolve().parents[2]
    forbidden = [source]
    for name in ("PRO_BRIDGE_LEDGER", "PRO_BRIDGE_PROTECTED_LEDGER"):
        if os.environ.get(name):
            forbidden.append(Path(os.environ[name]).resolve())
    if not parent.is_dir() or any(parent == root or root in parent.parents for root in forbidden):
        raise ValueError("temporary parent must be an existing directory outside installation and known ledgers")
    return parent

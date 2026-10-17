"""Regressions for the F1-F9 audit findings."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "pro_bridge"
CLI = TOOLS / "cli.py"
sys.path.insert(0, str(TOOLS))

import bridge_v2  # noqa: E402
import git_verify  # noqa: E402

SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
RESULTS: list[tuple[str, bool, str]] = []


def check(case: str, ok: bool, detail: str) -> None:
    RESULTS.append((case, ok, detail))
    print(("PASS " if ok else "FAIL ") + case + " " + detail)


def manifest_for(task_id: str, clock: bridge_v2.Clock, **overrides) -> dict:
    paths = bridge_v2.exact_result_paths(task_id)
    payload = {
        "task_id": task_id,
        "nonce": bridge_v2.new_nonce(),
        "orchestration_id": "poc6",
        "mode": "read_only",
        "allowed_paths": paths,
        "write_targets": list(paths),
        "baseline_sha": SHA,
        "branch": f"test/pro-result-{task_id[:8]}",
        "deadline_at": clock.time() + 1000,
        "allow_pr": False,
    }
    payload.update(overrides)
    return payload


def run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, encoding="utf-8")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def commit(repo: Path, *args: str) -> None:
    git(repo, "-c", "user.email=pro-bridge@localhost", "-c", "user.name=pro-bridge", "-c", "core.autocrlf=false", *args)


def test_process_race(root: Path) -> None:
    clock = bridge_v2.Clock()
    task_id = bridge_v2.new_task_id()
    payload = manifest_for(task_id, clock)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    script = textwrap.dedent(
        """
        import json, sys
        sys.path.insert(0, sys.argv[1])
        import bridge_v2
        manifest = json.loads(open(sys.argv[3], encoding="utf-8").read())
        ledger = bridge_v2.Ledger(sys.argv[2], bridge_v2.Clock(1700000000))
        ledger.prepare(manifest)
        print("CLAIM" if ledger.claim_send(manifest["task_id"]) else "NO")
        """
    )
    script_path = root / "race.py"
    script_path.write_text(script, encoding="utf-8")
    ledger = root / "ledger"
    procs = [
        subprocess.Popen([sys.executable, str(script_path), str(TOOLS), str(ledger), str(manifest_path)], stdout=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    outputs = [proc.communicate()[0].strip() for proc in procs]
    final = bridge_v2.Ledger(ledger, clock).get(task_id)
    check("S01", outputs.count("CLAIM") == 1 and final["send_attempts"] == 1, str(outputs))


def test_lock(root: Path) -> None:
    clock = bridge_v2.Clock()
    ledger_root = root / "locked"
    task_id = bridge_v2.new_task_id()
    ledger = bridge_v2.Ledger(ledger_root, clock)
    ledger.prepare(manifest_for(task_id, clock))
    ledger.claim_send(task_id)
    hold = textwrap.dedent(
        """
        import sys, time
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import bridge_v2
        flag = Path(sys.argv[3])
        with bridge_v2.FileLock(Path(sys.argv[2]), timeout=5):
            flag.write_text("held", encoding="utf-8")
            time.sleep(30)
        """
    )
    wait = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import bridge_v2
        try:
            with bridge_v2.FileLock(Path(sys.argv[2]), timeout=float(sys.argv[3])):
                print("ACQUIRED")
        except bridge_v2.LockTimeout:
            print("TIMEOUT")
        """
    )
    (root / "hold.py").write_text(hold, encoding="utf-8")
    (root / "wait.py").write_text(wait, encoding="utf-8")
    flag = root / "held.txt"
    holder = subprocess.Popen([sys.executable, str(root / "hold.py"), str(TOOLS), str(ledger_root / "ledger.lock"), str(flag)])
    for _ in range(50):
        if flag.exists():
            break
        time.sleep(0.05)
    active = subprocess.run(
        [sys.executable, str(root / "wait.py"), str(TOOLS), str(ledger_root / "ledger.lock"), "0.4"],
        capture_output=True,
        text=True,
    )
    held_row = json.loads((ledger_root / "ledger.json").read_text(encoding="utf-8"))["tasks"][task_id]
    attempts_while_held = held_row["send_attempts"]
    waiter = subprocess.Popen(
        [sys.executable, str(root / "wait.py"), str(TOOLS), str(ledger_root / "ledger.lock"), "8"],
        stdout=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.2)
    holder.kill()
    recovered = waiter.communicate(timeout=10)[0].strip()
    final = bridge_v2.Ledger(ledger_root, clock).get(task_id)
    check(
        "S02",
        active.stdout.strip() == "TIMEOUT" and recovered == "ACQUIRED" and attempts_while_held == 1 and final["send_attempts"] == 1 and final["state"] == "SUBMISSION_UNKNOWN",
        f"active={active.stdout.strip()} recovered={recovered} attempts={final['send_attempts']}",
    )


def test_contract(root: Path) -> None:
    clock = bridge_v2.Clock()
    ledger = bridge_v2.Ledger(root / "contract", clock)
    task_id = bridge_v2.new_task_id()
    item = manifest_for(task_id, clock)
    ledger.prepare(item)
    denied = False
    try:
        ledger.update(task_id, send_attempts=5)
    except ValueError:
        denied = True
    stale = False
    current = ledger.get(task_id)
    ledger.update(task_id, notes=["newer"])
    try:
        ledger.update(task_id, expected_revision=int(current["revision"]), state="VERIFIED")
    except bridge_v2.StaleRecord:
        stale = True
    fresh = ledger.get(task_id)
    bad_paths = 0
    for overrides in (
        {"write_targets": ["../secret.txt"]},
        {"branch": "main"},
        {"allowed_paths": ["C:/Windows/note.txt"], "write_targets": ["C:/Windows/note.txt"]},
        {"allow_pr": True},
    ):
        try:
            ledger.prepare(manifest_for(bridge_v2.new_task_id(), clock, **overrides))
        except (ValueError, PermissionError):
            bad_paths += 1
    record = ledger.get(task_id)
    record["send_attempts"] = 1
    record["state"] = "SUBMITTED"
    record["deadline_at"] = clock.time() + 1000
    blocked = dict(record)
    blocked["state"] = "BLOCKED"
    bodies = ["null", "[]", "1", '"hello"', json.dumps({"protocol_version": 2.0, "status": "PASS"})]
    decisions = []
    for body in bodies:
        observation = {"ancestor_ok": True, "changed_paths": record["allowed_paths"], "history_paths": record["allowed_paths"], "files": {record["allowed_paths"][0]: body, record["allowed_paths"][1]: "x"}, "blobs": {}}
        try:
            decisions.append(git_verify.evaluate(record, observation, 1)["state"])
        except Exception as exc:
            decisions.append(type(exc).__name__)
    missing = dict(json.loads(bridge_v2.canonical_bytes({"task_id": task_id, "nonce": record["nonce"], "request_sha256": record["request_sha256"], "baseline_sha": SHA, "protocol_version": 2, "report_sha256": "a" * 64})))
    # canonical_bytes is not the result file. Build the missing-status object directly.
    missing = {"task_id": task_id, "nonce": record["nonce"], "request_sha256": record["request_sha256"], "baseline_sha": SHA, "protocol_version": 2, "report_sha256": "a" * 64}
    observation = {"ancestor_ok": True, "changed_paths": record["allowed_paths"], "history_paths": record["allowed_paths"], "files": {record["allowed_paths"][0]: json.dumps(missing), record["allowed_paths"][1]: "x"}, "blobs": {}}
    missing_state = git_verify.evaluate(record, observation, 1)["state"]
    stayed = git_verify.evaluate(blocked, observation, 1)
    check(
        "S03",
        denied and stale and fresh["state"] == "PREPARED" and fresh["send_attempts"] == 0 and bad_paths == 4 and "VERIFIED" not in decisions and missing_state != "VERIFIED" and stayed["state"] == "BLOCKED",
        f"denied={denied} stale={stale} paths={bad_paths} decisions={decisions} missing={missing_state}",
    )


def add_result(repo: Path, record: dict, report: str, status: str = "REQUEST_CHANGES") -> None:
    payload = {
        "baseline_sha": record["baseline_sha"],
        "nonce": record["nonce"],
        "protocol_version": 2,
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        "request_sha256": record["request_sha256"],
        "status": status,
        "task_id": record["task_id"],
    }
    json_path = repo / record["allowed_paths"][0]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload) + "\n", encoding="utf-8", newline="\n")
    report_path = repo / record["allowed_paths"][1]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(report.encode("utf-8"))
    git(repo, "-c", "core.autocrlf=false", "add", ".")
    commit(repo, "commit", "-m", "result")
    git(repo, "branch", record["branch"])


def test_git_history(root: Path) -> None:
    canary = root / "CANARY"
    report = f"do not run open(r'{canary}','w')\n"
    shallow = root / "shallow"
    shallow.mkdir()
    git_init = subprocess.run(["git", "init", str(shallow)], capture_output=True)
    commit(shallow, "commit", "--allow-empty", "-m", "baseline")
    baseline = subprocess.run(["git", "-C", str(shallow), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ledger = root / "git-ledger"
    prepared = run_cli(["prepare", "--ledger", str(ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", baseline, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc6", "--branch", "test/pro-result-shallow"])
    body = json.loads(prepared.stdout)
    run_cli(["claim", "--ledger", str(ledger), "--task", body["task_id"]])
    record = json.loads(run_cli(["status", "--ledger", str(ledger), "--task", body["task_id"]]).stdout)
    for _ in range(3):
        commit(shallow, "commit", "--allow-empty", "-m", "step")
    add_result(shallow, record, report)
    dest = root / "fetch-shallow"
    fetched = git_verify.fetch_result(str(shallow), record["branch"], dest, baseline)
    observed = git_verify.read_delivery(dest, baseline, record["branch"])
    decision = git_verify.evaluate(record, observed, 1)
    deep = root / "deep"
    deep.mkdir()
    subprocess.run(["git", "init", str(deep)], capture_output=True, check=True)
    commit(deep, "commit", "--allow-empty", "-m", "baseline")
    deep_base = subprocess.run(["git", "-C", str(deep), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    for _ in range(120):
        commit(deep, "commit", "--allow-empty", "-m", "deep")
    deep_ledger = root / "deep-ledger"
    deep_prepared = run_cli(["prepare", "--ledger", str(deep_ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", deep_base, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc6", "--branch", "test/pro-result-deep"])
    deep_body = json.loads(deep_prepared.stdout)
    run_cli(["claim", "--ledger", str(deep_ledger), "--task", deep_body["task_id"]])
    deep_record = json.loads(run_cli(["status", "--ledger", str(deep_ledger), "--task", deep_body["task_id"]]).stdout)
    add_result(deep, deep_record, report)
    deep_fetch = git_verify.fetch_result(str(deep), deep_record["branch"], root / "fetch-deep", deep_base)
    deep_observed = git_verify.read_delivery(root / "fetch-deep", deep_base, deep_record["branch"])
    deep_decision = git_verify.evaluate(deep_record, deep_observed, 1)
    unrelated = root / "unrelated"
    unrelated.mkdir()
    subprocess.run(["git", "init", str(unrelated)], capture_output=True, check=True)
    commit(unrelated, "commit", "--allow-empty", "-m", "base")
    other_base = subprocess.run(["git", "-C", str(unrelated), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    git(unrelated, "checkout", "--orphan", "other")
    commit(unrelated, "commit", "--allow-empty", "-m", "other")
    git(unrelated, "branch", "test/pro-result-other")
    other = git_verify.read_delivery(unrelated, other_base, "test/pro-result-other")
    other_decision = git_verify.evaluate(deep_record, other, 1)
    missing_base_fetch = git_verify.fetch_result(str(unrelated), "test/pro-result-other", root / "fetch-other", other_base)
    check("S04", git_init.returncode == 0 and fetched["kind"] == "ok" and decision["verified"] and not canary.exists(), f"shallow={fetched['kind']} verified={decision['verified']}")
    check("S05", deep_fetch["kind"] == "ok" and deep_decision["verified"] and deep_decision["review_approved"] is False, f"deep={deep_fetch['kind']} verdict={deep_decision['review_verdict']}")
    check(
        "S06",
        other_decision["state"] == "BLOCKED" and other_decision["verified"] is False and missing_base_fetch["kind"] == "history_incomplete",
        f"unrelated={other_decision['state']} fetch={missing_base_fetch['kind']}",
    )


def test_reverify_and_remote(root: Path) -> None:
    repo = root / "again"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    commit(repo, "commit", "--allow-empty", "-m", "baseline")
    baseline = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ledger = root / "again-ledger"
    prepared = run_cli(["prepare", "--ledger", str(ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", baseline, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc6"])
    body = json.loads(prepared.stdout)
    run_cli(["claim", "--ledger", str(ledger), "--task", body["task_id"]])
    record = json.loads(run_cli(["status", "--ledger", str(ledger), "--task", body["task_id"]]).stdout)
    add_result(repo, record, "report\n")
    first = run_cli(["verify", "--ledger", str(ledger), "--task", body["task_id"], "--local-repo", str(repo), "--rev", record["branch"], "--fixture"])
    second = run_cli(["verify", "--ledger", str(ledger), "--task", body["task_id"], "--local-repo", str(repo), "--rev", "refs/heads/missing-result", "--fixture"])
    second_body = json.loads(second.stdout)
    mismatch = run_cli(["verify", "--ledger", str(ledger), "--task", body["task_id"], "--remote", "https://github.com/example/not-this.git"])
    check(
        "S07",
        first.returncode == 0 and second.returncode != 0 and second_body["current_verification"] is False and second_body["state"] == "VERIFIED" and mismatch.returncode == 3,
        f"first={first.returncode} second={second.returncode} remote={mismatch.returncode}",
    )


def test_followup(root: Path) -> None:
    ledger_root = root / "follow"
    clock = bridge_v2.Clock(1_700_000_000)
    ledger = bridge_v2.Ledger(ledger_root, clock)
    task_id = bridge_v2.new_task_id()
    item = manifest_for(task_id, clock)
    ledger.prepare(item)
    ledger.claim_send(task_id)
    seen = threading.Event()
    release = threading.Event()
    original = bridge_v2.Ledger.get

    def pausing_get(self, requested):
        value = original(self, requested)
        if requested == task_id and value["state"] == "SUBMISSION_UNKNOWN":
            seen.set()
            release.wait(5)
        return value

    bridge_v2.Ledger.get = pausing_get
    holder = {}

    def run_mark():
        import cli
        holder["code"] = cli.cmd_mark_submitted(type("A", (), {"ledger": str(ledger_root), "task": task_id, "session_url": "", "evidence": ""})())

    worker = threading.Thread(target=run_mark)
    worker.start()
    seen.wait(5)
    conflict = dict(item)
    conflict["expected_output"] = "different"
    conflict["request_sha256"] = bridge_v2.request_sha256(conflict)
    ledger.prepare(conflict)
    release.set()
    worker.join(5)
    bridge_v2.Ledger.get = original
    fresh = ledger.get(task_id)
    check("S08", holder.get("code") == 2 and fresh["state"] == "BLOCKED" and fresh["send_attempts"] == 1, f"code={holder.get('code')} state={fresh['state']}")

    nested = "[" * 40 + "]" * 40
    record = ledger.get(task_id)
    record["state"] = "SUBMITTED"
    record["send_attempts"] = 1
    observation = {"ancestor_ok": True, "changed_paths": record["allowed_paths"], "history_paths": [], "files": {record["allowed_paths"][0]: nested, record["allowed_paths"][1]: "x"}, "decode_error": False, "too_large": False}
    deep = git_verify.evaluate(record, observation, 1)
    bad = dict(item)
    bad["protocol_version"] = 1
    rejected = False
    try:
        bridge_v2.Ledger(root / "bad", clock).prepare(bad)
    except ValueError:
        rejected = True
    check("S09", deep["state"] == "BLOCKED" and rejected, f"deep={deep['state']} protocol={rejected}")


def test_merge_and_mode(root: Path) -> None:
    repo = root / "merge"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    commit(repo, "commit", "--allow-empty", "-m", "base")
    baseline = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ledger = root / "merge-ledger"
    prepared = run_cli(["prepare", "--ledger", str(ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", baseline, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc6"])
    body = json.loads(prepared.stdout)
    run_cli(["claim", "--ledger", str(ledger), "--task", body["task_id"]])
    record = json.loads(run_cli(["status", "--ledger", str(ledger), "--task", body["task_id"]]).stdout)
    current = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    git(repo, "checkout", "-b", "side")
    (repo / "guarded.txt").write_text("secret\n", encoding="utf-8")
    git(repo, "add", "guarded.txt")
    commit(repo, "commit", "-m", "outside")
    git(repo, "checkout", current)
    git(repo, "-c", "user.email=pro-bridge@localhost", "-c", "user.name=pro-bridge", "merge", "--no-ff", "side", "-m", "merge outside")
    (repo / "guarded.txt").write_text("restored\n", encoding="utf-8")
    git(repo, "add", "guarded.txt")
    commit(repo, "commit", "-m", "restore")
    add_result(repo, record, "report\n")
    observed = git_verify.read_delivery(repo, baseline, record["branch"])
    decision = git_verify.evaluate(record | {"send_attempts": 1, "state": "SUBMITTED", "deadline_at": 9_999_999_999}, observed, 1)
    mode_repo = root / "mode"
    mode_repo.mkdir()
    subprocess.run(["git", "init", str(mode_repo)], capture_output=True, check=True)
    commit(mode_repo, "commit", "--allow-empty", "-m", "base")
    mode_base = subprocess.run(["git", "-C", str(mode_repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    link = mode_repo / ".pro_bridge" / "reports"
    link.mkdir(parents=True)
    target = "elsewhere"
    blob = subprocess.run(["git", "-C", str(mode_repo), "hash-object", "-w", "--stdin"], input=target.encode(), capture_output=True).stdout.decode().strip()
    report_path = f".pro_bridge/reports/{record['task_id']}.md"
    json_path = f".pro_bridge/results/{record['task_id']}.json"
    git(mode_repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},{report_path}")
    payload = {"protocol_version": 2, "task_id": record["task_id"], "nonce": record["nonce"], "request_sha256": record["request_sha256"], "baseline_sha": mode_base, "status": "APPROVE", "report_sha256": hashlib.sha256(target.encode()).hexdigest()}
    json_file = mode_repo / json_path
    json_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.write_bytes(json.dumps(payload).encode())
    git(mode_repo, "add", json_path)
    commit(mode_repo, "commit", "-m", "symlink report")
    git(mode_repo, "branch", record["branch"])
    mode_obs = git_verify.read_delivery(mode_repo, mode_base, record["branch"])
    mode_decision = git_verify.evaluate(record | {"baseline_sha": mode_base, "send_attempts": 1, "state": "SUBMITTED", "deadline_at": 9_999_999_999}, mode_obs, 1)
    check("S10", decision["state"] == "BLOCKED" and mode_decision["state"] == "BLOCKED", f"merge={decision['state']} mode={mode_decision['state']}")


def test_deadline_wait(root: Path) -> None:
    ledger = root / "deadline-ledger"
    prepared = run_cli(["prepare", "--ledger", str(ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", SHA, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc6", "--deadline-seconds", "1"])
    body = json.loads(prepared.stdout)
    holder_script = root / "hold_deadline.py"
    holder_script.write_text(
        "import sys, time\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nimport bridge_v2\nflag = Path(sys.argv[3])\n"
        "with bridge_v2.FileLock(Path(sys.argv[2]), timeout=5):\n    flag.write_text('held', encoding='utf-8')\n    time.sleep(2.5)\n",
        encoding="utf-8",
    )
    flag = root / "deadline-held"
    holder = subprocess.Popen([sys.executable, str(holder_script), str(TOOLS), str(ledger / "ledger.lock"), str(flag)])
    for _ in range(50):
        if flag.exists():
            break
        time.sleep(0.05)
    claim = subprocess.run([sys.executable, str(CLI), "claim", "--ledger", str(ledger), "--task", body["task_id"]], capture_output=True, text=True, encoding="utf-8")
    holder.wait(timeout=5)
    status = json.loads(run_cli(["status", "--ledger", str(ledger), "--task", body["task_id"]]).stdout)
    check("S11", claim.returncode == 2 and status["state"] == "TIMEOUT" and status["send_attempts"] == 0, f"code={claim.returncode} state={status['state']} attempts={status['send_attempts']}")


def _prepare(ledger: Path, *extra: str) -> dict:
    result = run_cli(
        [
            "prepare",
            "--ledger",
            str(ledger),
            "--repository",
            "exampleowner/example-repo",
            "--source-sha",
            SHA,
            "--source-ref",
            "main",
            "--read",
            "tools/pro_bridge",
            "--orchestration",
            "poc7",
            *extra,
        ]
    )
    return {"code": result.returncode, "body": json.loads(result.stdout), "raw": result}


def test_prompt_and_authority(root: Path) -> None:
    ledger = root / "prompt"
    prepared = _prepare(ledger, "--task-type", "READ_ONLY_REVIEW")
    task_id = prepared["body"]["task_id"]
    canonical = prepared["body"]["prompt"]
    stored = json.loads((ledger / "ledger.json").read_text(encoding="utf-8"))
    stored["tasks"][task_id]["prompt"] = "MALICIOUS PROMPT"
    (ledger / "ledger.json").write_text(json.dumps(stored), encoding="utf-8")
    shown = run_cli(["prompt", "--ledger", str(ledger), "--task", task_id])
    rejected = False
    try:
        bridge_v2.Ledger(ledger, bridge_v2.LiveClock()).update(task_id, prompt="other")
    except ValueError:
        rejected = True
    authorized = _prepare(
        root / "authorized",
        "--task-type",
        "CODE_WRITE_DRAFT",
        "--authorize-code-write",
        "--code-branch",
        "test/pro-code-s12",
    )
    auth = authorized["body"]
    auth_status = json.loads(run_cli(["status", "--ledger", str(root / "authorized"), "--task", auth["task_id"]]).stdout)
    flipped = json.loads((root / "authorized" / "ledger.json").read_text(encoding="utf-8"))
    flipped["tasks"][auth["task_id"]]["browser_send_allowed"] = False
    (root / "authorized" / "ledger.json").write_text(json.dumps(flipped), encoding="utf-8")
    status_after = json.loads(run_cli(["status", "--ledger", str(root / "authorized"), "--task", auth["task_id"]]).stdout)
    source = json.loads(flipped["tasks"][auth["task_id"]]["request_bytes"])
    conflict = json.loads(json.dumps(source))
    conflict["permissions"]["code_branch"] = "test/pro-other"
    conflict_error = ""
    try:
        bridge_v2.Ledger(root / "conflict", bridge_v2.LiveClock()).prepare(conflict)
    except ValueError as exc:
        conflict_error = str(exc)
    false_write = json.loads(json.dumps(source))
    false_write["permissions"]["explicit_code_write"] = False
    false_error = ""
    try:
        bridge_v2.Ledger(root / "false-write", bridge_v2.LiveClock()).prepare(false_write)
    except PermissionError as exc:
        false_error = str(exc)
    foreign = json.loads((ledger / "ledger.json").read_text(encoding="utf-8"))
    foreign_manifest = json.loads(foreign["tasks"][task_id]["request_bytes"])
    foreign_manifest["permissions"]["read_repositories"] = ["other/repo"]
    foreign_error = ""
    try:
        bridge_v2.Ledger(root / "foreign", bridge_v2.LiveClock()).prepare(foreign_manifest)
    except ValueError as exc:
        foreign_error = str(exc)
    check(
        "S12",
        shown.returncode == 0
        and shown.stdout == canonical
        and "MALICIOUS" not in shown.stdout
        and rejected
        and auth["browser_send_allowed"] is True
        and "code_branch: test/pro-code-s12" in auth["prompt"]
        and auth_status["browser_send_allowed"] is True
        and status_after["browser_send_allowed"] is True
        and "code_branch" in conflict_error
        and "explicit_code_write" in false_error
        and "read_repositories" in foreign_error,
        f"cache={rejected} send={auth.get('browser_send_allowed')} conflict={conflict_error} false={false_error} foreign={foreign_error}",
    )


def test_fixture_and_history(root: Path) -> None:
    repo = root / "fixture-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    commit(repo, "commit", "--allow-empty", "-m", "baseline")
    baseline = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ledger = root / "fixture-ledger"
    prepared = run_cli(
        [
            "prepare",
            "--ledger",
            str(ledger),
            "--task-type",
            "READ_ONLY_REVIEW",
            "--repository",
            "exampleowner/example-repo",
            "--source-sha",
            baseline,
            "--source-ref",
            "main",
            "--read",
            "tools/pro_bridge",
            "--orchestration",
            "poc7",
        ]
    )
    body = json.loads(prepared.stdout)
    run_cli(["claim", "--ledger", str(ledger), "--task", body["task_id"]])
    record = json.loads(run_cli(["status", "--ledger", str(ledger), "--task", body["task_id"]]).stdout)
    add_result(repo, record, "fixture report\n", status="APPROVE")
    verified = run_cli(["verify", "--ledger", str(ledger), "--task", body["task_id"], "--local-repo", str(repo), "--rev", record["branch"], "--fixture"])
    verified_body = json.loads(verified.stdout)
    report = run_cli(["report", "--ledger", str(ledger), "--task", body["task_id"]])
    report_body = json.loads(report.stdout)
    check(
        "S13",
        verified.returncode == 0
        and verified_body["state"] == "VERIFIED"
        and verified_body["evidence_origin"] == "fixture"
        and verified_body["review_approved"] is False
        and verified_body["current_verification"] is False
        and report.returncode != 0
        and report_body["review_approved"] is False
        and report_body["current_verification"] is False
        and report_body["evidence_origin"] == "fixture",
        f"verify={verified.returncode} report={report.returncode} origin={report_body.get('evidence_origin')}",
    )
    original = git_verify._git

    def fail_log(repo_path, args, check=True, timeout=git_verify.GIT_TIMEOUT_SECONDS):
        if args and args[0] == "log":
            return subprocess.CompletedProcess(args, 128, b"", b"fatal")
        return original(repo_path, args, check=check, timeout=timeout)

    git_verify._git = fail_log
    try:
        observed = git_verify.read_delivery(repo, baseline, record["branch"])
        decision = git_verify.evaluate(record, observed, time.time())
    finally:
        git_verify._git = original
    check(
        "S14",
        observed is not None and observed.get("history_error") is True and decision["verified"] is False and "fail closed" in " ".join(decision["notes"]),
        f"error={None if observed is None else observed.get('history_error')} notes={decision['notes']}",
    )
    import cli

    saved = git_verify.read_delivery

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    git_verify.read_delivery = timed_out
    try:
        held = cli.open_ledger(ledger)
        code = cli.verify_record(
            held,
            held.get(body["task_id"]),
            type("A", (), {"local_repo": str(repo), "remote": None, "rev": record["branch"], "fixture": True})(),
        )
    finally:
        git_verify.read_delivery = saved
    after = json.loads(run_cli(["report", "--ledger", str(ledger), "--task", body["task_id"]]).stdout)
    historical = after.get("historical_verification") or {}
    check(
        "S15",
        code == 2
        and after["state"] == "VERIFIED"
        and after["current_verification"] is False
        and after["review_approved"] is False
        and historical.get("state") == "VERIFIED"
        and historical.get("delivery_integrity") == "VERIFIED",
        f"code={code} current={after.get('current_verification')} historical={historical.get('state')}",
    )


def test_unauthorized_write_and_status(root: Path) -> None:
    prepared = _prepare(root / "draft", "--task-type", "CODE_WRITE_DRAFT")
    body = prepared["body"]
    claim = run_cli(["claim", "--ledger", str(root / "draft"), "--task", body["task_id"]])
    clock = bridge_v2.Clock()
    task_id = bridge_v2.new_task_id()
    ledger = bridge_v2.Ledger(root / "status-enum", clock)
    ledger.prepare(manifest_for(task_id, clock))
    ledger.claim_send(task_id)
    record = ledger.get(task_id)
    payload = {
        "protocol_version": 2,
        "task_id": task_id,
        "nonce": record["nonce"],
        "request_sha256": record["request_sha256"],
        "baseline_sha": record["baseline_sha"],
        "status": "SHIP_IT",
        "report_sha256": "a" * 64,
    }
    observation = {
        "ancestor_ok": True,
        "changed_paths": record["allowed_paths"],
        "history_paths": record["allowed_paths"],
        "files": {record["allowed_paths"][0]: json.dumps(payload), record["allowed_paths"][1]: "x"},
        "blobs": {},
    }
    decision = git_verify.evaluate(record, observation, clock.time())
    check(
        "S16",
        body["browser_send_allowed"] is False and claim.returncode == 3 and decision["state"] == "BLOCKED" and decision["verified"] is False,
        f"send={body.get('browser_send_allowed')} claim={claim.returncode} status={decision['state']} notes={decision['notes']}",
    )


def _seed_github(ledger: Path, task_id: str) -> None:
    bridge_v2.Ledger(ledger, bridge_v2.LiveClock()).update(
        task_id,
        state="VERIFIED",
        last_observation="current_ok",
        evidence_origin="github",
        review_approved=True,
        delivery_integrity="VERIFIED",
        review_verdict="APPROVE",
        result_evidence={"commit": "github-commit", "delivery_integrity": "VERIFIED", "review_verdict": "APPROVE", "evidence_origin": "github"},
    )


def test_review_followups(root: Path) -> None:
    import cli

    ledger = root / "tamper"
    prepared = _prepare(ledger, "--task-type", "CODE_WRITE_DRAFT", "--authorize-code-write", "--code-branch", "test/pro-code-s17")
    task_id = prepared["body"]["task_id"]
    stored = json.loads((ledger / "ledger.json").read_text(encoding="utf-8"))
    manifest = json.loads(stored["tasks"][task_id]["request_bytes"])
    manifest["expected_output"] = "TAMPERED"
    manifest["code_branch"] = "test/pro-other"
    manifest["permissions"]["code_branch"] = "test/pro-other"
    stored["tasks"][task_id]["request_bytes"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    (ledger / "ledger.json").write_text(json.dumps(stored), encoding="utf-8")
    shown = run_cli(["prompt", "--ledger", str(ledger), "--task", task_id])
    claim = run_cli(["claim", "--ledger", str(ledger), "--task", task_id])
    status = json.loads(run_cli(["status", "--ledger", str(ledger), "--task", task_id]).stdout)
    check(
        "S17",
        shown.returncode == 3 and "TAMPERED" not in shown.stdout and claim.returncode == 3 and status["send_attempts"] == 0,
        f"prompt={shown.returncode} claim={claim.returncode} attempts={status.get('send_attempts')}",
    )

    unexpected = root / "unexpected"
    seeded = _prepare(unexpected, "--task-type", "READ_ONLY_REVIEW")
    seeded_id = seeded["body"]["task_id"]
    run_cli(["claim", "--ledger", str(unexpected), "--task", seeded_id])
    _seed_github(unexpected, seeded_id)
    saved = git_verify.read_delivery

    def explode(*args, **kwargs):
        raise RuntimeError("unexpected reader failure")

    git_verify.read_delivery = explode
    try:
        held = cli.open_ledger(unexpected)
        code = cli.verify_record(held, held.get(seeded_id), type("A", (), {"local_repo": str(root), "remote": None, "rev": "HEAD", "fixture": True})())
    finally:
        git_verify.read_delivery = saved
    report = json.loads(run_cli(["report", "--ledger", str(unexpected), "--task", seeded_id]).stdout)
    check(
        "S18",
        code == 2 and report["current_verification"] is False and report["review_approved"] is False and (report.get("historical_verification") or {}).get("delivery_integrity") == "VERIFIED",
        f"code={code} current={report.get('current_verification')}",
    )

    raced = root / "raced"
    raced_body = _prepare(raced, "--task-type", "READ_ONLY_REVIEW")
    raced_id = raced_body["body"]["task_id"]
    run_cli(["claim", "--ledger", str(raced), "--task", raced_id])
    _seed_github(raced, raced_id)

    def interleave(*args, **kwargs):
        bridge_v2.Ledger(raced, bridge_v2.LiveClock()).update(raced_id, notes=["benign note"])
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    git_verify.read_delivery = interleave
    try:
        held = cli.open_ledger(raced)
        race_code = cli.verify_record(held, held.get(raced_id), type("A", (), {"local_repo": str(root), "remote": None, "rev": "HEAD", "fixture": True})())
    finally:
        git_verify.read_delivery = saved
    race_report = json.loads(run_cli(["report", "--ledger", str(raced), "--task", raced_id]).stdout)
    check(
        "S19",
        race_code == 2 and race_report["current_verification"] is False and race_report["review_approved"] is False,
        f"code={race_code} current={race_report.get('current_verification')} approved={race_report.get('review_approved')}",
    )

    repeated = root / "repeated"
    repeated_body = _prepare(repeated, "--task-type", "READ_ONLY_REVIEW")
    repeated_id = repeated_body["body"]["task_id"]
    run_cli(["claim", "--ledger", str(repeated), "--task", repeated_id])
    _seed_github(repeated, repeated_id)

    def denied(*args, **kwargs):
        raise PermissionError("git permission")

    git_verify.read_delivery = denied
    try:
        held = cli.open_ledger(repeated)
        cli.verify_record(held, held.get(repeated_id), type("A", (), {"local_repo": str(root), "remote": None, "rev": "HEAD", "fixture": True})())
        held = cli.open_ledger(repeated)
        cli.verify_record(held, held.get(repeated_id), type("A", (), {"local_repo": str(root), "remote": None, "rev": "HEAD", "fixture": True})())
    finally:
        git_verify.read_delivery = saved
    repeated_report = json.loads(run_cli(["report", "--ledger", str(repeated), "--task", repeated_id]).stdout)
    repeated_history = repeated_report.get("historical_verification") or {}
    check(
        "S21",
        repeated_history.get("delivery_integrity") == "VERIFIED" and repeated_history.get("review_verdict") == "APPROVE" and repeated_report["current_verification"] is False,
        f"history={repeated_history.get('delivery_integrity')} verdict={repeated_history.get('review_verdict')}",
    )

    clean = root / "fixture-keep"
    clean.mkdir()
    subprocess.run(["git", "init", str(clean)], capture_output=True, check=True)
    commit(clean, "commit", "--allow-empty", "-m", "baseline")
    clean_base = subprocess.run(["git", "-C", str(clean), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    clean_ledger = root / "fixture-keep-ledger"
    clean_prepared = run_cli(["prepare", "--ledger", str(clean_ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", clean_base, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc7"])
    clean_body = json.loads(clean_prepared.stdout)
    run_cli(["claim", "--ledger", str(clean_ledger), "--task", clean_body["task_id"]])
    clean_record = json.loads(run_cli(["status", "--ledger", str(clean_ledger), "--task", clean_body["task_id"]]).stdout)
    add_result(clean, clean_record, "keep github\n", status="APPROVE")
    _seed_github(clean_ledger, clean_body["task_id"])
    kept = run_cli(["verify", "--ledger", str(clean_ledger), "--task", clean_body["task_id"], "--local-repo", str(clean), "--rev", clean_record["branch"], "--fixture"])
    kept_status = json.loads(run_cli(["status", "--ledger", str(clean_ledger), "--task", clean_body["task_id"]]).stdout)
    check(
        "S20",
        kept.returncode == 0 and kept_status.get("result_evidence", {}).get("commit") == "github-commit" and kept_status.get("evidence_origin") == "github" and kept_status.get("fixture_observation", {}).get("review_approved") is False,
        f"code={kept.returncode} commit={kept_status.get('result_evidence', {}).get('commit')} origin={kept_status.get('evidence_origin')}",
    )

    repo = root / "space-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    commit(repo, "commit", "--allow-empty", "-m", "baseline")
    baseline = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    space_ledger = root / "space-ledger"
    space = run_cli(["prepare", "--ledger", str(space_ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", baseline, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc7"])
    space_body = json.loads(space.stdout)
    run_cli(["claim", "--ledger", str(space_ledger), "--task", space_body["task_id"]])
    record = json.loads(run_cli(["status", "--ledger", str(space_ledger), "--task", space_body["task_id"]]).stdout)
    weird_name = Path(record["allowed_paths"][1]).name + " "
    weird = str(Path(record["allowed_paths"][1]).parent / weird_name).replace("\\", "/")

    def git_object(kind: str, body: bytes) -> str:
        return subprocess.run(["git", "-C", str(repo), "hash-object", "-t", kind, "-w", "--stdin"], input=body, capture_output=True, check=True).stdout.decode().strip()

    def tree_entry(mode: str, name: str, sha: str) -> bytes:
        return f"{mode} {name}".encode() + b"\0" + bytes.fromhex(sha)

    blob = git_object("blob", b"hidden\n")
    hidden_leaf = git_object("tree", tree_entry("100644", weird_name, blob))
    hidden_reports = git_object("tree", tree_entry("40000", "reports", hidden_leaf))
    hidden_tree = git_object("tree", tree_entry("40000", ".pro_bridge", hidden_reports))
    hidden = subprocess.run(["git", "-C", str(repo), "-c", "user.email=pro-bridge@localhost", "-c", "user.name=pro-bridge", "commit-tree", hidden_tree, "-p", baseline, "-m", "hidden"], capture_output=True, text=True, check=True).stdout.strip()
    report = "space report\n"
    payload = {"baseline_sha": record["baseline_sha"], "nonce": record["nonce"], "protocol_version": 2, "report_sha256": hashlib.sha256(report.encode()).hexdigest(), "request_sha256": record["request_sha256"], "status": "APPROVE", "task_id": record["task_id"]}
    json_blob = git_object("blob", (json.dumps(payload) + "\n").encode())
    report_blob = git_object("blob", report.encode())
    reports_tree = git_object("tree", tree_entry("100644", Path(record["allowed_paths"][1]).name, report_blob))
    results_tree = git_object("tree", tree_entry("100644", Path(record["allowed_paths"][0]).name, json_blob))
    bridge_tree = git_object("tree", tree_entry("40000", "reports", reports_tree) + tree_entry("40000", "results", results_tree))
    final_tree = git_object("tree", tree_entry("40000", ".pro_bridge", bridge_tree))
    final = subprocess.run(["git", "-C", str(repo), "-c", "user.email=pro-bridge@localhost", "-c", "user.name=pro-bridge", "commit-tree", final_tree, "-p", hidden, "-m", "result"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "update-ref", f"refs/heads/{record['branch']}", final], check=True)
    observed = git_verify.read_delivery(repo, baseline, record["branch"])
    decision = git_verify.evaluate(record, observed, time.time())
    listed = " ".join(observed.get("history_paths") or []) if observed else ""
    check(
        "S22",
        observed is not None and decision["verified"] is False and weird in (observed.get("history_paths") or []),
        f"verified={decision['verified']} history={listed!r}",
    )

    inactive = manifest_for(bridge_v2.new_task_id(), bridge_v2.Clock(), code_write_permission=False, code_branch="test/unauthorized-code", code_paths=["game/unauthorized.gd"])
    inactive["permissions"] = {"explicit_code_write": False, "code_branch": "test/unauthorized-code", "code_paths": ["game/unauthorized.gd"]}
    rejected = ""
    try:
        bridge_v2.Ledger(root / "inactive", bridge_v2.LiveClock()).prepare(inactive)
    except PermissionError as exc:
        rejected = str(exc)
    inactive.update(read_targets=[], task_type="READ_ONLY_REVIEW", source_repository="exampleowner/example-repo", source_sha=SHA, source_ref="main", expected_output="x", acceptance_criteria=["x"])
    rendered = cli.render_prompt(inactive, "a" * 64)
    check(
        "S23",
        "code scope" in rejected and "game/unauthorized.gd" not in rendered,
        f"error={rejected} rendered={ 'game/unauthorized.gd' in rendered }",
    )


def _fetch_from(local: Path):
    def fetch_result(url: str, branch: str, dest: Path, baseline: str) -> dict:
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(dest)], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(dest), "fetch", str(local), f"{branch}:refs/heads/{branch}"], capture_output=True, check=True)
        if subprocess.run(["git", "-C", str(dest), "cat-file", "-e", baseline], capture_output=True).returncode != 0:
            raise RuntimeError("baseline missing after fetch")
        return {"kind": "ok", "detail": "local-transport"}

    return fetch_result


def _invoke(args: list[str]) -> tuple[int, str]:
    import cli

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = cli.main(args)
    return code, stdout.getvalue()


def _claimed(root: Path, name: str) -> dict:
    repo = root / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    commit(repo, "commit", "--allow-empty", "-m", "baseline")
    baseline = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ledger = root / f"{name}-ledger"
    prepared = run_cli(["prepare", "--ledger", str(ledger), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", baseline, "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "poc8"])
    body = json.loads(prepared.stdout)
    run_cli(["claim", "--ledger", str(ledger), "--task", body["task_id"]])
    record = json.loads(run_cli(["status", "--ledger", str(ledger), "--task", body["task_id"]]).stdout)
    return {"repo": repo, "ledger": ledger, "task": body["task_id"], "baseline": baseline, "record": record}


def _approve(root: Path, name: str) -> dict:
    claimed = _claimed(root, name)
    add_result(claimed["repo"], claimed["record"], f"{name} report\n", status="APPROVE")
    fetch = _fetch_from(claimed["repo"])
    saved = git_verify.fetch_result
    git_verify.fetch_result = fetch
    try:
        code, output = _invoke(["verify", "--ledger", str(claimed["ledger"]), "--task", claimed["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.fetch_result = saved
    claimed["code"] = code
    claimed["output"] = output
    claimed["fetch"] = fetch
    return claimed


def _verify_remote(env: dict) -> tuple[int, str]:
    saved = git_verify.fetch_result
    git_verify.fetch_result = env["fetch"]
    try:
        return _invoke(["verify", "--ledger", str(env["ledger"]), "--task", env["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.fetch_result = saved


def _row(ledger: Path, task: str) -> dict:
    return json.loads((ledger / "ledger.json").read_text(encoding="utf-8"))["tasks"][task]


def _write_row(ledger: Path, task: str, row: dict) -> None:
    path = ledger / "ledger.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"][task] = row
    path.write_text(json.dumps(data), encoding="utf-8")


def _git_object(repo: Path, kind: str, payload: bytes) -> str:
    return subprocess.run(["git", "-C", str(repo), "hash-object", "-t", kind, "-w", "--stdin"], input=payload, capture_output=True, check=True).stdout.decode().strip()


def _tree_entry(mode: str, name: str, sha: str) -> bytes:
    return f"{mode} {name}".encode() + b"\0" + bytes.fromhex(sha)


def _commit_tree(repo: Path, tree: str, parent: str, message: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=pro-bridge@localhost", "-c", "user.name=pro-bridge", "commit-tree", tree, "-p", parent, "-m", message], capture_output=True, text=True, check=True).stdout.strip()


def test_f1_f4(root: Path) -> None:
    base = _approve(root, "f1")
    if base["code"] != 0:
        check("S24", False, f"setup {base['output']}")
        check("S25", False, "setup")
        check("S26", False, "setup")
        check("S27", False, "setup")
        check("S28", False, "setup")
        return
    original = json.loads(json.dumps(_row(base["ledger"], base["task"])))
    rejected: list[str] = []
    original_fetch = git_verify.fetch_result

    def run_rejected(label: str, mutate) -> None:
        _write_row(base["ledger"], base["task"], json.loads(json.dumps(original)))
        row = _row(base["ledger"], base["task"])
        mutate(row)
        _write_row(base["ledger"], base["task"], row)
        saved = git_verify.fetch_result
        git_verify.fetch_result = base["fetch"]
        try:
            verified, _ = _invoke(["verify", "--ledger", str(base["ledger"]), "--task", base["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
            resumed, _ = _invoke(["resume", "--ledger", str(base["ledger"]), "--task", base["task"], "--verify", "--remote", "https://github.com/exampleowner/example-repo.git"])
        finally:
            git_verify.fetch_result = saved
        report = run_cli(["report", "--ledger", str(base["ledger"]), "--task", base["task"]])
        prompt = run_cli(["prompt", "--ledger", str(base["ledger"]), "--task", base["task"]])
        claim = run_cli(["claim", "--ledger", str(base["ledger"]), "--task", base["task"]])
        report_body = json.loads(report.stdout)
        ok = verified != 0 and resumed != 0 and report.returncode != 0 and report_body["review_approved"] is False and report_body["current_verification"] is False and prompt.returncode != 0 and claim.returncode != 0
        rejected.append(f"{label}:{'closed' if ok else 'OPEN'} v={verified} resume={resumed} report={report.returncode} prompt={prompt.returncode} claim={claim.returncode}")

    def edit_bytes(row: dict) -> None:
        manifest = json.loads(row["request_bytes"])
        manifest["expected_output"] = "TAMPERED"
        row["request_bytes"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    run_rejected("request_bytes", edit_bytes)
    run_rejected("allowed_paths", lambda row: row.__setitem__("allowed_paths", ["outside.json", "outside.md"]))
    run_rejected("nonce", lambda row: row.__setitem__("nonce", "0" * 32))
    run_rejected("source_repository", lambda row: row.__setitem__("source_repository", "other/repo"))
    run_rejected("result_repository", lambda row: row.__setitem__("result_repository", "other/repo"))
    run_rejected("source_sha", lambda row: row.__setitem__("source_sha", "b" * 40))
    run_rejected("deadline_at", lambda row: row.__setitem__("deadline_at", float(row["deadline_at"]) + 100))

    outside = _claimed(root, "outside")
    record = outside["record"]
    report_text = "outside report\n"
    payload = {"baseline_sha": record["baseline_sha"], "nonce": record["nonce"], "protocol_version": 2, "report_sha256": hashlib.sha256(report_text.encode()).hexdigest(), "request_sha256": record["request_sha256"], "status": "APPROVE", "task_id": record["task_id"]}
    json_blob = _git_object(outside["repo"], "blob", (json.dumps(payload) + "\n").encode())
    report_blob = _git_object(outside["repo"], "blob", report_text.encode())
    tree = _git_object(outside["repo"], "tree", _tree_entry("100644", "outside.json", json_blob) + _tree_entry("100644", "outside.md", report_blob))
    final = _commit_tree(outside["repo"], tree, outside["baseline"], "outside")
    subprocess.run(["git", "-C", str(outside["repo"]), "update-ref", f"refs/heads/{record['branch']}", final], check=True)
    row = _row(outside["ledger"], outside["task"])
    row["allowed_paths"] = ["outside.json", "outside.md"]
    _write_row(outside["ledger"], outside["task"], row)
    outside["fetch"] = _fetch_from(outside["repo"])
    outside_code, _ = _verify_remote(outside)
    outside_report = run_cli(["report", "--ledger", str(outside["ledger"]), "--task", outside["task"]])
    outside_closed = outside_code != 0 and outside_report.returncode != 0 and json.loads(outside_report.stdout)["review_approved"] is False
    rejected.append(f"matching_outside:{'closed' if outside_closed else 'OPEN'} v={outside_code} report={outside_report.returncode}")
    check("S24", all(item.split(":", 1)[1].startswith("closed") for item in rejected) and len(rejected) == 8, " ".join(rejected))

    colon = _claimed(root, "colon")
    record = colon["record"]
    hidden = _git_object(colon["repo"], "blob", b"hidden\n")
    added_tree = _git_object(colon["repo"], "tree", _tree_entry("100644", ":outside.txt", hidden))
    added = _commit_tree(colon["repo"], added_tree, colon["baseline"], "add")
    base_tree = subprocess.run(["git", "-C", str(colon["repo"]), "rev-parse", f"{colon['baseline']}^{{tree}}"], capture_output=True, text=True, check=True).stdout.strip()
    removed = _commit_tree(colon["repo"], base_tree, added, "delete")
    report_text = "colon report\n"
    payload = {"baseline_sha": record["baseline_sha"], "nonce": record["nonce"], "protocol_version": 2, "report_sha256": hashlib.sha256(report_text.encode()).hexdigest(), "request_sha256": record["request_sha256"], "status": "APPROVE", "task_id": record["task_id"]}
    json_blob = _git_object(colon["repo"], "blob", (json.dumps(payload) + "\n").encode())
    report_blob = _git_object(colon["repo"], "blob", report_text.encode())
    reports_tree = _git_object(colon["repo"], "tree", _tree_entry("100644", Path(record["allowed_paths"][1]).name, report_blob))
    results_tree = _git_object(colon["repo"], "tree", _tree_entry("100644", Path(record["allowed_paths"][0]).name, json_blob))
    bridge_tree = _git_object(colon["repo"], "tree", _tree_entry("40000", "reports", reports_tree) + _tree_entry("40000", "results", results_tree))
    final_tree = _git_object(colon["repo"], "tree", _tree_entry("40000", ".pro_bridge", bridge_tree))
    final = _commit_tree(colon["repo"], final_tree, removed, "result")
    subprocess.run(["git", "-C", str(colon["repo"]), "update-ref", f"refs/heads/{record['branch']}", final], check=True)
    observed = git_verify.read_delivery(colon["repo"], colon["baseline"], record["branch"])
    decision = git_verify.evaluate(record, observed, time.time())
    colon["fetch"] = _fetch_from(colon["repo"])
    colon_code, _ = _verify_remote(colon)
    colon_report = run_cli(["report", "--ledger", str(colon["ledger"]), "--task", colon["task"]])
    history = observed.get("history_paths") if observed else []
    check("S25", observed is not None and ":outside.txt" in history and decision["verified"] is False and colon_code != 0 and colon_report.returncode != 0, f"history={history!r} verified={None if observed is None else decision['verified']} code={colon_code} report={colon_report.returncode}")

    raced = _approve(root, "f3")
    if raced["code"] != 0:
        check("S26", False, raced["output"])
        check("S27", False, "setup")
        check("S28", False, "setup")
        return
    saved_read = git_verify.read_delivery
    saved_update = bridge_v2.Ledger.update

    def reapprove() -> int:
        git_verify.read_delivery = saved_read
        git_verify.fetch_result = raced["fetch"]
        code, _ = _invoke(["verify", "--ledger", str(raced["ledger"]), "--task", raced["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
        return code

    def after_collision(times: int) -> dict:
        bridge_v2.Ledger.update = saved_update
        approved = reapprove()
        seen = {"n": 0}

        def wrapped(self, task_id, expected_revision=None, expected_state=None, **changes):
            if task_id == raced["task"] and changes.get("last_observation") in {"reader_error", "current_verification_failed"} and seen["n"] < times:
                seen["n"] += 1
                saved_update(self, task_id, notes=["benign"])
            return saved_update(self, task_id, expected_revision=expected_revision, expected_state=expected_state, **changes)

        def reader(*args, **kwargs):
            for _ in range(times):
                saved_update(bridge_v2.Ledger(raced["ledger"], bridge_v2.LiveClock()), raced["task"], notes=["benign"])
            raise RuntimeError("reader")

        bridge_v2.Ledger.update = wrapped
        git_verify.read_delivery = reader
        git_verify.fetch_result = raced["fetch"]
        try:
            _invoke(["verify", "--ledger", str(raced["ledger"]), "--task", raced["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
        finally:
            bridge_v2.Ledger.update = saved_update
            git_verify.read_delivery = saved_read
            git_verify.fetch_result = original_fetch
        report = json.loads(run_cli(["report", "--ledger", str(raced["ledger"]), "--task", raced["task"]]).stdout)
        report["setup_approved"] = approved == 0
        report["injected"] = seen["n"]
        return report

    one = after_collision(1)
    three = after_collision(3)
    four = after_collision(4)
    subprocess.run(["git", "-C", str(raced["repo"]), "update-ref", f"refs/heads/{raced['record']['branch']}", raced["baseline"]], check=True)
    git_verify.fetch_result = raced["fetch"]
    try:
        production_negative, _ = _invoke(["verify", "--ledger", str(raced["ledger"]), "--task", raced["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.fetch_result = original_fetch
    production_report = json.loads(run_cli(["report", "--ledger", str(raced["ledger"]), "--task", raced["task"]]).stdout)
    # Restore the approved commit for the generation test by replaying add_result's branch. The negative test already consumed this ledger.
    check(
        "S26",
        one["setup_approved"] and three["setup_approved"] and four["setup_approved"] and one["current_verification"] is False and one["review_approved"] is False and three["current_verification"] is False and three["review_approved"] is False and four["current_verification"] is False and four["review_approved"] is False and production_negative != 0 and production_report["review_approved"] is False,
        f"one={one.get('current_verification')}/{one.get('injected')} three={three.get('current_verification')}/{three.get('injected')} four={four.get('current_verification')}/{four.get('injected')} negative={production_negative}",
    )

    newer = _approve(root, "f3gen")
    depth = {"n": 0}

    def nested(*args, **kwargs):
        depth["n"] += 1
        if depth["n"] == 1:
            _invoke(["verify", "--ledger", str(newer["ledger"]), "--task", newer["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
            raise RuntimeError("outer reader")
        return saved_read(*args, **kwargs)

    git_verify.fetch_result = newer["fetch"]
    git_verify.read_delivery = nested
    try:
        outer_code, _ = _invoke(["verify", "--ledger", str(newer["ledger"]), "--task", newer["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.read_delivery = saved_read
        git_verify.fetch_result = original_fetch
    newer_report = json.loads(run_cli(["report", "--ledger", str(newer["ledger"]), "--task", newer["task"]]).stdout)
    check("S27", newer["code"] == 0 and newer_report["current_verification"] is True and newer_report["review_approved"] is True and depth["n"] >= 2, f"outer={outer_code} current={newer_report.get('current_verification')} approved={newer_report.get('review_approved')} depth={depth['n']}")

    snap = _approve(root, "f4")
    before = _row(snap["ledger"], snap["task"])
    production = {
        "evidence_origin": before.get("evidence_origin"),
        "review_verdict": before.get("review_verdict"),
        "verified_commit_sha": (before.get("result_evidence") or {}).get("commit"),
        "request_sha256": before.get("request_sha256"),
    }
    add_result_text = "fixture requests changes\n"
    changed_payload = {"baseline_sha": snap["record"]["baseline_sha"], "nonce": snap["record"]["nonce"], "protocol_version": 2, "report_sha256": hashlib.sha256(add_result_text.encode()).hexdigest(), "request_sha256": snap["record"]["request_sha256"], "status": "REQUEST_CHANGES", "task_id": snap["task"]}
    json_path = snap["repo"] / snap["record"]["allowed_paths"][0]
    report_path = snap["repo"] / snap["record"]["allowed_paths"][1]
    json_path.write_text(json.dumps(changed_payload) + "\n", encoding="utf-8", newline="\n")
    report_path.write_bytes(add_result_text.encode())
    git(snap["repo"], "-c", "core.autocrlf=false", "add", ".")
    commit(snap["repo"], "commit", "-m", "fixture")
    git(snap["repo"], "branch", "-f", snap["record"]["branch"])
    fixture = run_cli(["verify", "--ledger", str(snap["ledger"]), "--task", snap["task"], "--local-repo", str(snap["repo"]), "--rev", snap["record"]["branch"], "--fixture"])
    git_verify.read_delivery = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reader"))
    try:
        _invoke(["verify", "--ledger", str(snap["ledger"]), "--task", snap["task"], "--local-repo", str(snap["repo"]), "--rev", snap["record"]["branch"], "--fixture"])
    finally:
        git_verify.read_delivery = saved_read
    after = json.loads(run_cli(["report", "--ledger", str(snap["ledger"]), "--task", snap["task"]]).stdout)
    historical = after.get("production_snapshot") or after.get("historical_verification") or {}
    nested_evidence = historical.get("result_evidence") or {}
    check(
        "S28",
        snap["code"] == 0
        and fixture.returncode == 0
        and after["current_verification"] is False
        and after["review_approved"] is False
        and historical.get("evidence_origin") == "github"
        and historical.get("review_verdict") == "APPROVE"
        and nested_evidence.get("review_verdict") == "APPROVE"
        and (historical.get("verified_commit_sha") or nested_evidence.get("commit")) == production["verified_commit_sha"]
        and historical.get("request_sha256", production["request_sha256"]) == production["request_sha256"]
        and production["review_verdict"] == "APPROVE"
        and production["evidence_origin"] == "github",
        f"fixture={fixture.returncode} origin={historical.get('evidence_origin')} verdict={historical.get('review_verdict')} nested={nested_evidence.get('review_verdict')}",
    )

    renamed, rename_ok = git_verify._raw_paths(b":100644 100644 aaaaaaa bbbbbbb R100\0old.txt\0:new.txt\0:100644 000000 ccccccc 0000000 D\0:gone.txt\0")
    broken, broken_ok = git_verify._raw_paths(b"not-a-header\0")
    names, names_ok = git_verify._name_only_paths(b":outside.txt\0\0:outside.txt\0")
    check("S29", rename_ok and renamed == ["old.txt", ":new.txt", ":gone.txt"] and broken_ok is False and names_ok and names == [":outside.txt", ":outside.txt"], f"rename={renamed!r} broken={broken_ok} names={names!r}")

    seq = _approve(root, "f4seq")
    opened = _row(seq["ledger"], seq["task"])
    original_snapshot = {
        "evidence_origin": opened.get("evidence_origin"),
        "review_verdict": opened.get("review_verdict"),
        "result_evidence": opened.get("result_evidence"),
        "verified_commit_sha": (opened.get("result_evidence") or {}).get("commit"),
        "request_sha256": opened.get("request_sha256"),
        "verification_generation": opened.get("verification_generation"),
    }

    def fail_reader(*args, **kwargs):
        raise RuntimeError("reader")

    git_verify.fetch_result = seq["fetch"]
    git_verify.read_delivery = fail_reader
    try:
        _invoke(["verify", "--ledger", str(seq["ledger"]), "--task", seq["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.read_delivery = saved_read
        git_verify.fetch_result = original_fetch
    seq_text = "sequence fixture\n"
    seq_payload = {"baseline_sha": seq["record"]["baseline_sha"], "nonce": seq["record"]["nonce"], "protocol_version": 2, "report_sha256": hashlib.sha256(seq_text.encode()).hexdigest(), "request_sha256": seq["record"]["request_sha256"], "status": "REQUEST_CHANGES", "task_id": seq["task"]}
    (seq["repo"] / seq["record"]["allowed_paths"][0]).write_text(json.dumps(seq_payload) + "\n", encoding="utf-8", newline="\n")
    (seq["repo"] / seq["record"]["allowed_paths"][1]).write_bytes(seq_text.encode())
    git(seq["repo"], "-c", "core.autocrlf=false", "add", ".")
    commit(seq["repo"], "commit", "-m", "sequence fixture")
    git(seq["repo"], "branch", "-f", seq["record"]["branch"])
    seq_fixture = run_cli(["verify", "--ledger", str(seq["ledger"]), "--task", seq["task"], "--local-repo", str(seq["repo"]), "--rev", seq["record"]["branch"], "--fixture"])
    git_verify.read_delivery = fail_reader
    try:
        _invoke(["verify", "--ledger", str(seq["ledger"]), "--task", seq["task"], "--local-repo", str(seq["repo"]), "--rev", seq["record"]["branch"], "--fixture"])
    finally:
        git_verify.read_delivery = saved_read
    seq_report = json.loads(run_cli(["report", "--ledger", str(seq["ledger"]), "--task", seq["task"]]).stdout)
    seq_snapshot = seq_report.get("production_snapshot") or {}
    seq_nested = seq_snapshot.get("result_evidence") or {}
    check(
        "S30",
        seq["code"] == 0
        and seq_fixture.returncode == 0
        and seq_report["current_verification"] is False
        and seq_report["review_approved"] is False
        and seq_snapshot.get("evidence_origin") == "github"
        and seq_snapshot.get("review_verdict") == "APPROVE"
        and seq_nested.get("review_verdict") == "APPROVE"
        and seq_snapshot.get("verified_commit_sha") == original_snapshot["verified_commit_sha"]
        and seq_snapshot.get("request_sha256") == original_snapshot["request_sha256"]
        and seq_snapshot.get("verification_generation") == original_snapshot["verification_generation"]
        and original_snapshot["evidence_origin"] == "github"
        and original_snapshot["review_verdict"] == "APPROVE"
        and (seq_report.get("fixture_observation") or _row(seq["ledger"], seq["task"]).get("fixture_observation") or {}).get("review_verdict") == "REQUEST_CHANGES",
        f"gen={seq_snapshot.get('verification_generation')} verdict={seq_snapshot.get('review_verdict')} nested={seq_nested.get('review_verdict')}",
    )


def test_r1_r4(root: Path) -> None:
    original_fetch = git_verify.fetch_result
    approved = _approve(root, "r1")
    if approved["code"] != 0:
        check("S31", False, approved["output"])
        check("S32", False, "setup")
        check("S33", False, "setup")
        check("S34", False, "setup")
        return
    original = json.loads(json.dumps(_row(approved["ledger"], approved["task"])))
    fake_id = bridge_v2.new_task_id()
    mutated = json.loads(json.dumps(original))
    mutated["task_id"] = fake_id
    _write_row(approved["ledger"], approved["task"], mutated)
    report_a = run_cli(["report", "--ledger", str(approved["ledger"]), "--task", approved["task"]])
    status_a = run_cli(["status", "--ledger", str(approved["ledger"]), "--task", approved["task"]])
    claim_a = run_cli(["claim", "--ledger", str(approved["ledger"]), "--task", approved["task"]])
    prompt_a = run_cli(["prompt", "--ledger", str(approved["ledger"]), "--task", approved["task"]])
    resume_a = run_cli(["resume", "--ledger", str(approved["ledger"]), "--task", approved["task"]])
    keys_after = set(json.loads((approved["ledger"] / "ledger.json").read_text(encoding="utf-8"))["tasks"])
    body_a = json.loads(report_a.stdout)
    case_a = report_a.returncode != 0 and body_a["review_approved"] is False and body_a["current_verification"] is False and body_a["task_id"] == approved["task"] and fake_id not in keys_after and claim_a.returncode != 0 and prompt_a.returncode != 0 and json.loads(status_a.stdout)["review_approved"] is not True and json.loads(resume_a.stdout).get("review_approved") is not True

    other = run_cli(["prepare", "--ledger", str(approved["ledger"]), "--task-type", "READ_ONLY_REVIEW", "--repository", "exampleowner/example-repo", "--source-sha", approved["baseline"], "--source-ref", "main", "--read", "tools/pro_bridge", "--orchestration", "r1b"])
    other_body = json.loads(other.stdout)
    preserved = json.loads(json.dumps(_row(approved["ledger"], approved["task"])))
    copied = json.loads(json.dumps(original))
    _write_row(approved["ledger"], other_body["task_id"], copied)
    report_b = run_cli(["report", "--ledger", str(approved["ledger"]), "--task", other_body["task_id"]])
    body_b = json.loads(report_b.stdout)
    after_a = _row(approved["ledger"], approved["task"])
    case_b = report_b.returncode != 0 and body_b["review_approved"] is False and body_b["current_verification"] is False and body_b["task_id"] == other_body["task_id"] and after_a.get("request_sha256") == preserved.get("request_sha256") and after_a.get("production_snapshot") == preserved.get("production_snapshot") and after_a.get("review_approved") == preserved.get("review_approved")
    check("S31", case_a and case_b, f"A={report_a.returncode}/{body_a.get('review_approved')} B={report_b.returncode}/{body_b.get('review_approved')} task={body_b.get('task_id')}")

    rebound = _approve(root, "r2")
    row = _row(rebound["ledger"], rebound["task"])
    manifest = json.loads(row["request_bytes"])
    manifest["expected_output"] = "A different request"
    row["request_bytes"] = bridge_v2.canonical_bytes(manifest).decode("utf-8")
    row["request_sha256"] = bridge_v2.request_sha256(manifest)
    _write_row(rebound["ledger"], rebound["task"], row)
    rebound_report = run_cli(["report", "--ledger", str(rebound["ledger"]), "--task", rebound["task"]])
    rebound_body = json.loads(rebound_report.stdout)
    snapshot = rebound_body.get("production_snapshot") or {}
    legacy = _claimed(root, "legacy")
    legacy_row = _row(legacy["ledger"], legacy["task"])
    legacy_row.update(state="VERIFIED", last_observation="current_ok", evidence_origin="github", review_approved=True, review_verdict="APPROVE", delivery_integrity="VERIFIED", result_evidence={"commit": "legacy-commit", "review_verdict": "APPROVE"})
    _write_row(legacy["ledger"], legacy["task"], legacy_row)
    legacy_report = run_cli(["report", "--ledger", str(legacy["ledger"]), "--task", legacy["task"]])
    legacy_body = json.loads(legacy_report.stdout)
    check(
        "S32",
        rebound["code"] == 0
        and row["request_sha256"] != (original and snapshot.get("request_sha256"))
        and snapshot.get("request_sha256") not in (None, row["request_sha256"])
        and rebound_report.returncode != 0
        and rebound_body["review_approved"] is False
        and rebound_body["current_verification"] is False
        and legacy_report.returncode != 0
        and legacy_body["review_approved"] is False,
        f"report={rebound_report.returncode} approved={rebound_body.get('review_approved')} snap={snapshot.get('request_sha256')} legacy={legacy_report.returncode}",
    )

    locked = _approve(root, "r3")
    real_begin = bridge_v2.Ledger.begin_verification
    state = {"raise": 0, "allow_nested": False}

    def begin(self, task_id):
        if state["allow_nested"]:
            return real_begin(self, task_id)
        if state["raise"] > 0:
            state["raise"] -= 1
            if state.get("nested_first"):
                state["nested_first"] = False
                state["allow_nested"] = True
                try:
                    _invoke(["verify", "--ledger", str(locked["ledger"]), "--task", locked["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
                finally:
                    state["allow_nested"] = False
            raise bridge_v2.LockTimeout()
        return real_begin(self, task_id)

    def fail_once() -> tuple[int, dict]:
        git_verify.fetch_result = locked["fetch"]
        bridge_v2.Ledger.begin_verification = begin
        try:
            code, text = _invoke(["verify", "--ledger", str(locked["ledger"]), "--task", locked["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
        finally:
            bridge_v2.Ledger.begin_verification = real_begin
            git_verify.fetch_result = git_verify.fetch_result
        return code, json.loads(text)

    state["raise"] = 1
    one_code, one_body = fail_once()
    one_report = json.loads(run_cli(["report", "--ledger", str(locked["ledger"]), "--task", locked["task"]]).stdout)
    # Restore a current approval before the next independent failure series.
    git_verify.fetch_result = locked["fetch"]
    restored, _ = _invoke(["verify", "--ledger", str(locked["ledger"]), "--task", locked["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    codes = []
    state["raise"] = 3
    for _ in range(3):
        code, _body = fail_once()
        codes.append(code)
    three_report = json.loads(run_cli(["report", "--ledger", str(locked["ledger"]), "--task", locked["task"]]).stdout)
    git_verify.fetch_result = locked["fetch"]
    again, _ = _invoke(["verify", "--ledger", str(locked["ledger"]), "--task", locked["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    state["raise"] = 1
    state["nested_first"] = True
    git_verify.fetch_result = locked["fetch"]
    newer_code, newer_body = fail_once()
    newer_report = json.loads(run_cli(["report", "--ledger", str(locked["ledger"]), "--task", locked["task"]]).stdout)
    real_note = bridge_v2.Ledger.note_start_failed

    def note_times_out(self, task_id, observed_generation):
        raise bridge_v2.LockTimeout()

    state["raise"] = 1
    state["nested_first"] = False
    git_verify.fetch_result = locked["fetch"]
    bridge_v2.Ledger.begin_verification = begin
    bridge_v2.Ledger.note_start_failed = note_times_out
    try:
        gap_code, gap_text = _invoke(["verify", "--ledger", str(locked["ledger"]), "--task", locked["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        bridge_v2.Ledger.begin_verification = real_begin
        bridge_v2.Ledger.note_start_failed = real_note
        git_verify.fetch_result = original_fetch
    gap_body = json.loads(gap_text)
    gap_report = json.loads(run_cli(["report", "--ledger", str(locked["ledger"]), "--task", locked["task"]]).stdout)
    check(
        "S33",
        locked["code"] == 0
        and one_code != 0
        and one_body.get("error") == "verification_start_failed"
        and one_body.get("current_verification") is False
        and one_report["review_approved"] is False
        and restored == 0
        and len(codes) == 3
        and all(code != 0 for code in codes)
        and three_report["review_approved"] is False
        and again == 0
        and newer_report["review_approved"] is True
        and newer_report["current_verification"] is True
        and (newer_report.get("production_snapshot") or {}).get("verification_generation") == _row(locked["ledger"], locked["task"]).get("verification_generation")
        and gap_code != 0
        and gap_body.get("error") == "verification_start_failed"
        and gap_body.get("failure_persisted") is False
        and gap_body.get("current_verification") is False
        and gap_body.get("cross_process_recovery") == "not_guaranteed"
        and gap_report["review_approved"] is True,
        f"one={one_code}/{one_body.get('error')} codes={codes} newer={newer_code}/{newer_report.get('review_approved')} gap={gap_code}/{gap_body.get('failure_persisted')}",
    )

    snap = _approve(root, "r4")
    before = _row(snap["ledger"], snap["task"])
    production_commit = (before.get("result_evidence") or {}).get("commit")
    text = "fixture requests changes\n"
    payload = {"baseline_sha": snap["record"]["baseline_sha"], "nonce": snap["record"]["nonce"], "protocol_version": 2, "report_sha256": hashlib.sha256(text.encode()).hexdigest(), "request_sha256": snap["record"]["request_sha256"], "status": "REQUEST_CHANGES", "task_id": snap["task"]}
    (snap["repo"] / snap["record"]["allowed_paths"][0]).write_text(json.dumps(payload) + "\n", encoding="utf-8", newline="\n")
    (snap["repo"] / snap["record"]["allowed_paths"][1]).write_bytes(text.encode())
    git(snap["repo"], "-c", "core.autocrlf=false", "add", ".")
    commit(snap["repo"], "commit", "-m", "fixture")
    git(snap["repo"], "branch", "-f", snap["record"]["branch"])
    fixture_commit = subprocess.run(["git", "-C", str(snap["repo"]), "rev-parse", snap["record"]["branch"]], capture_output=True, text=True, check=True).stdout.strip()
    fixture = run_cli(["verify", "--ledger", str(snap["ledger"]), "--task", snap["task"], "--local-repo", str(snap["repo"]), "--rev", snap["record"]["branch"], "--fixture"])
    emitted = json.loads(fixture.stdout)
    stored = _row(snap["ledger"], snap["task"])
    emitted_commit = (emitted.get("result_evidence") or {}).get("commit") or (emitted.get("fixture_observation") or {}).get("commit")
    check(
        "S34",
        snap["code"] == 0
        and fixture.returncode == 0
        and emitted.get("evidence_origin") == "fixture"
        and emitted.get("review_verdict") == "REQUEST_CHANGES"
        and emitted.get("current_verification") is False
        and emitted.get("review_approved") is False
        and emitted_commit == fixture_commit
        and emitted_commit != production_commit
        and (emitted.get("production_snapshot") or {}).get("review_verdict") == "APPROVE"
        and (emitted.get("production_snapshot") or {}).get("verified_commit_sha") == production_commit
        and (stored.get("production_snapshot") or {}).get("review_verdict") == "APPROVE"
        and (stored.get("production_snapshot") or {}).get("verified_commit_sha") == production_commit
        and (stored.get("fixture_observation") or {}).get("review_verdict") == "REQUEST_CHANGES",
        f"origin={emitted.get('evidence_origin')} verdict={emitted.get('review_verdict')} commit={emitted_commit} production={production_commit}",
    )

    mutex = root / "mutex-ledger"
    mutex.mkdir()
    (mutex / "ledger.json").write_text('{"tasks":{}}', encoding="utf-8")
    flag = root / "mutex-flag"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import bridge_v2; lock=bridge_v2.FileLock(Path(sys.argv[2]), timeout=5); lock.__enter__(); Path(sys.argv[3]).write_text('held', encoding='utf-8'); os._exit(97)",
            str(TOOLS),
            str(mutex / "ledger.lock"),
            str(flag),
        ]
    )
    deadline = time.time() + 10
    while not flag.exists() and time.time() < deadline:
        time.sleep(0.05)
    holder.wait(timeout=10)
    acquired = False
    try:
        with bridge_v2.FileLock(mutex / "ledger.lock", timeout=5):
            acquired = (mutex / "ledger.json").exists()
    except bridge_v2.LockTimeout:
        acquired = False
    check("S35", acquired and holder.returncode == 97, f"acquired={acquired} exit={holder.returncode}")


def _shift_request(ledger: Path, task: str, label: str) -> tuple[str, str]:
    row = _row(ledger, task)
    previous = row["request_sha256"]
    manifest = json.loads(row["request_bytes"])
    manifest["expected_output"] = label
    row["request_bytes"] = bridge_v2.canonical_bytes(manifest).decode("utf-8")
    row["request_sha256"] = bridge_v2.request_sha256(manifest)
    row["revision"] = int(row.get("revision", 0)) + 1
    _write_row(ledger, task, row)
    return previous, row["request_sha256"]


def _restore_approval(env: dict, original: dict) -> None:
    _write_row(env["ledger"], env["task"], json.loads(json.dumps(original)))


def _race_verify(env: dict, when: str) -> tuple[int, dict, dict]:
    saved_read = git_verify.read_delivery
    real_finish = bridge_v2.Ledger.finish_verification
    shifted = {"done": False}

    def read_delivery(*args, **kwargs):
        if when == "during" and not shifted["done"]:
            shifted["done"] = True
            _shift_request(env["ledger"], env["task"], f"during-{when}")
        observed = saved_read(*args, **kwargs)
        if when == "after_read" and not shifted["done"]:
            shifted["done"] = True
            _shift_request(env["ledger"], env["task"], f"after-{when}")
        return observed

    def finish(self, task_id, generation, **changes):
        if when == "before_finish" and task_id == env["task"] and not shifted["done"]:
            shifted["done"] = True
            _shift_request(env["ledger"], env["task"], "before-finish")
        return real_finish(self, task_id, generation, **changes)

    git_verify.fetch_result = env["fetch"]
    git_verify.read_delivery = read_delivery
    bridge_v2.Ledger.finish_verification = finish
    try:
        code, text = _invoke(["verify", "--ledger", str(env["ledger"]), "--task", env["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.read_delivery = saved_read
        bridge_v2.Ledger.finish_verification = real_finish
        git_verify.fetch_result = git_verify.fetch_result
    body = json.loads(text)
    report = json.loads(run_cli(["report", "--ledger", str(env["ledger"]), "--task", env["task"]]).stdout)
    return code, body, report


def _not_transferred(env: dict, original_hash: str, body: dict, report: dict) -> bool:
    row = _row(env["ledger"], env["task"])
    snapshot = row.get("production_snapshot") or {}
    return (
        body.get("review_approved") is not True
        and body.get("current_verification") is not True
        and report.get("review_approved") is not True
        and report.get("current_verification") is not True
        and snapshot.get("request_sha256") == original_hash
        and snapshot.get("request_sha256") != row.get("request_sha256")
        and row.get("send_attempts") == 1
        and run_cli(["claim", "--ledger", str(env["ledger"]), "--task", env["task"]]).returncode != 0
    )


def test_n1_n2(root: Path) -> None:
    raced = _approve(root, "n1")
    if raced["code"] != 0:
        check("S36", False, raced["output"])
        check("S37", False, "setup")
        check("S38", False, "setup")
        check("S39", False, "setup")
        check("S40", False, "setup")
        check("S41", False, "setup")
        check("S42", False, "setup")
        check("S43", False, "setup")
        return
    original = json.loads(json.dumps(_row(raced["ledger"], raced["task"])))
    original_hash = original["request_sha256"]
    runs = []
    for index in range(3):
        _restore_approval(raced, original)
        code, body, report = _race_verify(raced, "after_read")
        runs.append(_not_transferred(raced, original_hash, body, report) and code != 0)
    check("S36", all(runs) and len(runs) == 3, f"runs={runs}")

    timings = []
    for when in ("during", "before_finish", "before_start"):
        _restore_approval(raced, original)
        if when == "before_start":
            _shift_request(raced["ledger"], raced["task"], "before-start")
            git_verify.fetch_result = raced["fetch"]
            code, text = _invoke(["verify", "--ledger", str(raced["ledger"]), "--task", raced["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
            body = json.loads(text)
            report = json.loads(run_cli(["report", "--ledger", str(raced["ledger"]), "--task", raced["task"]]).stdout)
        else:
            code, body, report = _race_verify(raced, when)
        timings.append(_not_transferred(raced, original_hash, body, report) and code != 0)
    check("S37", all(timings), f"timings={timings}")

    _restore_approval(raced, original)
    saved_read = git_verify.read_delivery

    def noted(*args, **kwargs):
        bridge_v2.Ledger(raced["ledger"], bridge_v2.LiveClock()).update(raced["task"], notes=["benign note"])
        return saved_read(*args, **kwargs)

    git_verify.fetch_result = raced["fetch"]
    git_verify.read_delivery = noted
    try:
        note_code, note_text = _invoke(["verify", "--ledger", str(raced["ledger"]), "--task", raced["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.read_delivery = saved_read
    note_body = json.loads(note_text)
    noted_row = _row(raced["ledger"], raced["task"])

    def explode(*args, **kwargs):
        raise RuntimeError("reader")

    git_verify.fetch_result = raced["fetch"]
    git_verify.read_delivery = explode
    try:
        _invoke(["verify", "--ledger", str(raced["ledger"]), "--task", raced["task"], "--remote", "https://github.com/exampleowner/example-repo.git"])
    finally:
        git_verify.read_delivery = saved_read
    reader_report = json.loads(run_cli(["report", "--ledger", str(raced["ledger"]), "--task", raced["task"]]).stdout)
    fresh = _approve(root, "n1ok")
    fresh_report = json.loads(run_cli(["report", "--ledger", str(fresh["ledger"]), "--task", fresh["task"]]).stdout)
    check(
        "S38",
        note_code == 0
        and note_body.get("review_approved") is True
        and noted_row.get("request_sha256") == original_hash
        and "benign note" in " ".join(noted_row.get("notes") or [])
        and reader_report.get("review_approved") is False
        and reader_report.get("current_verification") is False
        and fresh["code"] == 0
        and fresh_report.get("review_approved") is True
        and fresh_report.get("automatic_high_risk_continue") is False,
        f"note={note_code}/{note_body.get('review_approved')} reader={reader_report.get('review_approved')} fresh={fresh['code']}",
    )

    def failed_fixture(env: dict, args: list[str], forbidden_commits: set[str]) -> tuple[int, dict]:
        completed = run_cli(["verify", "--ledger", str(env["ledger"]), "--task", env["task"], "--local-repo", str(env["repo"]), *args, "--fixture"])
        emitted = json.loads(completed.stdout)
        return completed.returncode, emitted

    def fixture_isolated(emitted: dict, production_commit: str, forbidden: set[str]) -> bool:
        evidence = emitted.get("result_evidence") or {}
        historical = emitted.get("historical_fixture_observation") or {}
        return (
            emitted.get("evidence_origin") == "fixture"
            and emitted.get("verified") is False
            and emitted.get("review_approved") is False
            and emitted.get("current_verification") is False
            and emitted.get("review_verdict") in {None, "REJECTED", "UNKNOWN"}
            and evidence.get("review_verdict") != "APPROVE"
            and evidence.get("commit") not in forbidden
            and evidence.get("commit") != production_commit
            and (emitted.get("production_snapshot") or {}).get("review_verdict") == "APPROVE"
            and (emitted.get("production_snapshot") or {}).get("verified_commit_sha") == production_commit
            and (not historical.get("commit") or historical.get("commit") != evidence.get("commit"))
        )

    sample = _approve(root, "n2")
    production_commit = (_row(sample["ledger"], sample["task"]).get("result_evidence") or {}).get("commit")
    absent_code, absent = failed_fixture(sample, ["--rev", "refs/heads/does-not-exist"], set())
    check(
        "S39",
        sample["code"] == 0 and absent_code != 0 and fixture_isolated(absent, production_commit, set()),
        f"code={absent_code} origin={absent.get('evidence_origin')} verdict={absent.get('review_verdict')} commit={(absent.get('result_evidence') or {}).get('commit')}",
    )

    bad = _approve(root, "n2bad")
    bad_commit = (_row(bad["ledger"], bad["task"]).get("result_evidence") or {}).get("commit")
    bad_row = bad["record"]
    text = "fixture hash mismatch\n"
    payload = {"baseline_sha": bad_row["baseline_sha"], "nonce": bad_row["nonce"], "protocol_version": 2, "report_sha256": "ab" * 32, "request_sha256": bad_row["request_sha256"], "status": "REQUEST_CHANGES", "task_id": bad["task"]}
    (bad["repo"] / bad_row["allowed_paths"][0]).parent.mkdir(parents=True, exist_ok=True)
    (bad["repo"] / bad_row["allowed_paths"][0]).write_text(json.dumps(payload) + "\n", encoding="utf-8", newline="\n")
    (bad["repo"] / bad_row["allowed_paths"][1]).write_bytes(text.encode())
    git(bad["repo"], "-c", "core.autocrlf=false", "add", ".")
    commit(bad["repo"], "commit", "-m", "bad fixture")
    git(bad["repo"], "branch", "-f", bad_row["branch"])
    bad_code, bad_body = failed_fixture(bad, ["--rev", bad_row["branch"]], {bad_commit})
    check(
        "S40",
        bad["code"] == 0 and bad_code != 0 and fixture_isolated(bad_body, bad_commit, {bad_commit}),
        f"code={bad_code} verdict={bad_body.get('review_verdict')} commit={(bad_body.get('result_evidence') or {}).get('commit')}",
    )

    prior = _approve(root, "n2prior")
    prior_commit = (_row(prior["ledger"], prior["task"]).get("result_evidence") or {}).get("commit")
    prior_text = "fixture requests changes\n"
    prior_row = prior["record"]
    prior_payload = {"baseline_sha": prior_row["baseline_sha"], "nonce": prior_row["nonce"], "protocol_version": 2, "report_sha256": hashlib.sha256(prior_text.encode()).hexdigest(), "request_sha256": prior_row["request_sha256"], "status": "REQUEST_CHANGES", "task_id": prior["task"]}
    (prior["repo"] / prior_row["allowed_paths"][0]).write_text(json.dumps(prior_payload) + "\n", encoding="utf-8", newline="\n")
    (prior["repo"] / prior_row["allowed_paths"][1]).write_bytes(prior_text.encode())
    git(prior["repo"], "-c", "core.autocrlf=false", "add", ".")
    commit(prior["repo"], "commit", "-m", "fixture")
    git(prior["repo"], "branch", "-f", prior_row["branch"])
    first = run_cli(["verify", "--ledger", str(prior["ledger"]), "--task", prior["task"], "--local-repo", str(prior["repo"]), "--rev", prior_row["branch"], "--fixture"])
    first_body = json.loads(first.stdout)
    fixture_commit = (first_body.get("result_evidence") or {}).get("commit")
    second_code, second = failed_fixture(prior, ["--rev", "refs/heads/does-not-exist"], {prior_commit, fixture_commit})
    check(
        "S41",
        prior["code"] == 0
        and first.returncode == 0
        and first_body.get("review_verdict") == "REQUEST_CHANGES"
        and fixture_commit
        and fixture_commit != prior_commit
        and second_code != 0
        and fixture_isolated(second, prior_commit, {prior_commit, fixture_commit})
        and (second.get("historical_fixture_observation") or {}).get("commit") == fixture_commit,
        f"first={first_body.get('review_verdict')} second={second.get('review_verdict')} commit={(second.get('result_evidence') or {}).get('commit')} historical={(second.get('historical_fixture_observation') or {}).get('commit')}",
    )

    broken = _approve(root, "n2reader")
    broken_commit = (_row(broken["ledger"], broken["task"]).get("result_evidence") or {}).get("commit")
    saved_read = git_verify.read_delivery

    def crash(*args, **kwargs):
        raise RuntimeError("fixture reader")

    git_verify.read_delivery = crash
    try:
        crash_code, crash_text = _invoke(["verify", "--ledger", str(broken["ledger"]), "--task", broken["task"], "--local-repo", str(broken["repo"]), "--rev", broken["record"]["branch"], "--fixture"])
    finally:
        git_verify.read_delivery = saved_read
    crash_body = json.loads(crash_text)
    check(
        "S42",
        broken["code"] == 0 and crash_code != 0 and fixture_isolated(crash_body, broken_commit, {broken_commit}),
        f"code={crash_code} origin={crash_body.get('evidence_origin')} verdict={crash_body.get('review_verdict')} commit={(crash_body.get('result_evidence') or {}).get('commit')}",
    )
    gate = json.loads(run_cli(["report", "--ledger", str(fresh["ledger"]), "--task", fresh["task"]]).stdout)
    check("S43", gate.get("review_approved") is True and gate.get("automatic_high_risk_continue") is False, f"approved={gate.get('review_approved')} auto={gate.get('automatic_high_risk_continue')}")


def test_audit_branches(root: Path) -> None:
    invalid = ["bad branch", "a..b", "a@{b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b",
               "a\\b", "a\tb", "a\nb", "a\x7fb", "a.", ".hidden", "a/.hidden", "a.lock", "a.lock/b",
               "a//b", "/a", "a/", "-option", "@", "HEAD", "main", "master", "refs/heads/result"]
    valid = ["result", "test/pro-result-1234", "audit.v2/result_1", "release+review"]
    ledger_root = root / "branch-validation"
    refused = []
    for branch in invalid:
        completed = run_cli(["prepare", "--ledger", str(ledger_root), "--task-type", "READ_ONLY_REVIEW",
                             "--repository", "exampleowner/example-repo", "--source-sha", SHA,
                             "--source-ref", "main", "--orchestration", "audit", "--branch=" + branch])
        refused.append(completed.returncode == 3 and json.loads(completed.stdout).get("error") == "branch")
    tasks = json.loads((ledger_root / "ledger.json").read_text(encoding="utf-8"))["tasks"]
    accepted = all(bridge_v2.acceptable_branch(branch) and subprocess.run(
        ["git", "check-ref-format", "--branch", branch], capture_output=True).returncode == 0 for branch in valid)
    check("S44", all(refused) and not tasks and accepted, f"rejected={len(refused)} tasks={len(tasks)} valid={accepted}")

    clock = bridge_v2.Clock()
    ledger = bridge_v2.Ledger(root / "code-branch-validation", clock)
    outcomes = []
    for branch in invalid:
        task = bridge_v2.new_task_id()
        manifest = manifest_for(task, clock, mode="write", code_write_permission=True,
                                code_branch=branch, code_paths=[f"tests/pro_bridge/fixtures/pro_codewrite_canary_{task}.json"])
        try:
            ledger.prepare(manifest)
            outcomes.append(False)
        except PermissionError:
            outcomes.append(True)
    check("S45", all(outcomes) and not ledger._read()["tasks"], "invalid code branches rejected without claiming")


def test_audit_history_modes(root: Path) -> None:
    for case, mode in (("S46", "120000"), ("S47", "100755"), ("S48", "160000"), ("S49", "100644")):
        repo = root / case
        repo.mkdir()
        git(repo, "init")
        commit(repo, "commit", "--allow-empty", "-m", "baseline")
        baseline = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        clock = bridge_v2.LiveClock()
        task = bridge_v2.new_task_id()
        ledger = bridge_v2.Ledger(root / (case + "-ledger"), clock)
        ledger.prepare(manifest_for(task, clock, baseline_sha=baseline))
        ledger.claim_send(task)
        record = ledger.get(task)
        add_result(repo, record, "history mode regression\n")
        report = record["allowed_paths"][1]
        blob = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD:" + report], capture_output=True, text=True, check=True).stdout.strip()
        if mode == "100644":
            git(repo, "update-index", "--force-remove", report)
        else:
            oid = baseline if mode == "160000" else blob
            git(repo, "update-index", "--cacheinfo", f"{mode},{oid},{report}")
        commit(repo, "commit", "-m", "intermediate change")
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{report}")
        commit(repo, "commit", "-m", "restore regular report")
        observation = git_verify.read_delivery(repo, baseline, "HEAD")
        decision = git_verify.evaluate(record, observation, clock.time())
        expected = "VERIFIED" if mode == "100644" else "BLOCKED"
        check(case, decision["state"] == expected and not observation["history_error"]
              and observation["bad_mode"] == (mode != "100644"), f"intermediate={mode} final=100644 state={decision['state']}")


def test_audit_missing_task_and_prompt(root: Path) -> None:
    ledger_root = root / "missing-task"
    clock = bridge_v2.LiveClock()
    ledger = bridge_v2.Ledger(ledger_root, clock)
    # A complete CLI manifest also exercises the plain-text prompt contract.
    prepared = run_cli(["prepare", "--ledger", str(ledger_root), "--task-type", "READ_ONLY_REVIEW",
                        "--repository", "exampleowner/example-repo", "--source-sha", SHA,
                        "--source-ref", "main", "--orchestration", "audit"])
    body = json.loads(prepared.stdout)
    before = ledger.path.read_bytes()
    absent = bridge_v2.new_task_id()
    outcomes = []
    for command in ("claim", "mark-submitted", "mark-unknown", "block", "prompt", "verify", "status", "resume", "report"):
        extra = ["--reason", "audit"] if command == "block" else []
        completed = run_cli([command, "--ledger", str(ledger_root), "--task", absent, *extra])
        error = json.loads(completed.stdout)
        outcomes.append(completed.returncode == 3 and error.get("error") == "task_not_found"
                        and error.get("task_id") == absent and error.get("do_not_resend") is True
                        and error.get("replacement_prepared") is False and error.get("review_approved") is False
                        and error.get("command_succeeded") is False and error.get("automatic_high_risk_continue") is False)
    check("S50", all(outcomes) and ledger.path.read_bytes() == before, f"{len(outcomes)} commands refuse; ledger unchanged")
    prompt = run_cli(["prompt", "--ledger", str(ledger_root), "--task", body["task_id"]])
    check("S51", prompt.returncode == 0 and prompt.stdout == body["prompt"]
          and ledger.path.read_bytes() == before, "prompt stdout is the exact prepared text")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pro-bridge-sec-") as directory:
        root = Path(directory)
        test_process_race(root)
        test_lock(root)
        test_contract(root)
        test_git_history(root)
        test_reverify_and_remote(root)
        test_followup(root)
        test_merge_and_mode(root)
        test_deadline_wait(root)
        test_prompt_and_authority(root)
        test_fixture_and_history(root)
        test_unauthorized_write_and_status(root)
        test_review_followups(root)
        test_f1_f4(root)
        test_r1_r4(root)
        test_n1_n2(root)
        test_audit_branches(root)
        test_audit_history_modes(root)
        test_audit_missing_task_and_prompt(root)
    failed = [item for item in RESULTS if not item[1]]
    print(f"SECURITY_CASES={len(RESULTS)} FAILED={len(failed)}")
    if not failed:
        print("BRIDGE_SECURITY_REGRESSION_PASS")
        raise SystemExit(0)
    print("BRIDGE_SECURITY_REGRESSION_FAILED")
    raise SystemExit(1)


if __name__ == "__main__":
    main()

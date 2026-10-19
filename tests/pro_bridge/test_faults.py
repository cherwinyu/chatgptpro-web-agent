"""Fault cases F01-F12 and Windows ledger cases W01-W11."""

from __future__ import annotations

import contextlib
import ctypes
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "pro_bridge"))
import bridge_v2 as bridge


RESULTS: list[tuple[str, bool, str]] = []


def check(case: str, ok: bool, detail: str) -> None:
    RESULTS.append((case, ok, detail))
    print(("PASS " if ok else "FAIL ") + case + " " + detail)


def manifest(clock: bridge.Clock, **overrides) -> dict:
    task_id = overrides.pop("task_id", bridge.new_task_id())
    payload = {
        "task_id": task_id,
        "nonce": bridge.new_nonce(),
        "orchestration_id": "8a3ff0a8-8b3f-4619-a2e3-b9c8b89fbaf8",
        "mode": "read_only",
        "allowed_paths": [
            f".pro_bridge/results/{task_id}.json",
            f".pro_bridge/reports/{task_id}.md",
        ],
        "baseline_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "branch": f"test/poc5-{task_id[:8]}",
        "deadline_at": clock.time() + 1000,
        "allow_pr": False,
        "write_targets": [
            f".pro_bridge/results/{task_id}.json",
            f".pro_bridge/reports/{task_id}.md",
        ],
    }
    payload.update(overrides)
    payload["request_sha256"] = bridge.request_sha256(payload)
    return payload


def world(root: Path):
    clock = bridge.Clock()
    ledger = bridge.Ledger(root, clock)
    github = bridge.MockGitHub()
    browser = bridge.MockBrowser(github)
    workspace = bridge.WorkspaceGuard(
        r"D:\example-workspace",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "main",
        True,
    )
    return clock, ledger, github, browser, workspace, bridge.Bridge(ledger, github, browser, workspace)


def result_files(item: dict, report: str, status: str = "PASS") -> dict[str, str]:
    report_hash = bridge.content_sha256(report)
    payload = {
        "task_id": item["task_id"],
        "nonce": item["nonce"],
        "request_sha256": item["request_sha256"],
        "baseline_sha": item["baseline_sha"],
        "protocol_version": 2,
        "status": status,
        "report_sha256": report_hash,
    }
    return {
        item["allowed_paths"][0]: json.dumps(payload, ensure_ascii=False, sort_keys=True),
        item["allowed_paths"][1]: report,
    }


def test_f01(root: Path) -> None:
    clock, ledger, github, browser, workspace, client = world(root / "f01")
    item = manifest(clock)
    ledger.prepare(item)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker() -> None:
        local = bridge.Bridge(ledger, github, browser, workspace)
        barrier.wait()
        outcomes.append(local.submit(item)["state"])

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    conflict = dict(item)
    conflict["nonce"] = bridge.new_nonce()
    conflict["request_sha256"] = bridge.request_sha256(conflict)
    blocked = ledger.prepare(conflict)
    record = ledger.get(item["task_id"])
    check(
        "F01",
        github.post_count == 1
        and record["send_attempts"] == 1
        and blocked["state"] == "BLOCKED"
        and not any(name.endswith("-retry") for name in github.branches),
        f"posts={github.post_count} attempts={record['send_attempts']} states={outcomes} conflict={blocked['state']}",
    )


def test_f02(root: Path) -> None:
    clock, ledger, github, browser, _workspace, client = world(root / "f02")
    item = manifest(clock)
    ledger.prepare(item)
    github.drop_ack = True
    first = client.submit(item)
    github.drop_ack = False
    second = client.submit(item)
    github.publish_snapshot(item["branch"], item["task_id"], {"only": "partial"}, "commit-json")
    # Partial file is outside the allowlist, so replace it with the real JSON only.
    files = result_files(item, "report body")
    github.publish_snapshot(item["branch"], item["task_id"], {item["allowed_paths"][0]: files[item["allowed_paths"][0]]}, "commit-json-only")
    seen = client.recover(item["task_id"])
    check(
        "F02",
        first["state"] == "SUBMISSION_UNKNOWN"
        and second["state"] == "SUBMISSION_UNKNOWN"
        and seen["state"] == "RESULT_SEEN"
        and github.post_count == 1,
        f"first={first['state']} second={second['state']} seen={seen['state']} posts={github.post_count}",
    )


def test_f03(root: Path) -> None:
    clock, ledger, github, browser, workspace, client = world(root / "f03")
    item = manifest(clock)
    ledger.prepare(item)
    try:
        client.submit(item, crash_after_claim=True)
        crashed = False
    except RuntimeError:
        crashed = True
    restarted = bridge.Bridge(ledger, github, browser, workspace)
    recovered = restarted.submit(item)
    clock.advance(1001)
    timed_out = restarted.recover(item["task_id"])
    check(
        "F03",
        crashed
        and github.post_count == 0
        and recovered["state"] == "SUBMISSION_UNKNOWN"
        and "may_not_have_been_delivered" in " ".join(recovered["notes"])
        and timed_out["state"] == "TIMEOUT"
        and github.post_count == 0,
        f"state={timed_out['state']} posts={github.post_count}",
    )


def test_f04(root: Path) -> None:
    clock, ledger, github, _browser, _workspace, client = world(root / "f04")
    item = manifest(clock)
    ledger.prepare(item)
    files = result_files(item, "# report\n")
    github.ci[item["task_id"]] = {"conclusion": "success", "unknown_errors": 0, "marker_present": True, "exit_code": 0}
    github.publish_snapshot(item["branch"], item["task_id"], {item["allowed_paths"][0]: files[item["allowed_paths"][0]]}, "c1")
    missing_md = client.recover(item["task_id"])
    github.publish_snapshot(item["branch"], item["task_id"], {item["allowed_paths"][1]: files[item["allowed_paths"][1]]}, "c2")
    md_only = client.recover(item["task_id"])
    github.publish_snapshot(item["branch"], item["task_id"], files, "c3")
    verified = client.recover(item["task_id"])
    check(
        "F04",
        missing_md["state"] == "RESULT_SEEN"
        and md_only["state"] != "VERIFIED"
        and verified["state"] == "VERIFIED",
        f"partial={missing_md['state']} md_only={md_only['state']} final={verified['state']}",
    )


def test_f05(root: Path) -> None:
    clock, ledger, github, browser, _workspace, client = world(root / "f05")
    item = manifest(clock)
    ledger.prepare(item)
    github.publish_snapshot(item["branch"], "someone-else", {"foreign": "1"}, "other")
    browser.online = False
    result = client.submit(item)
    check(
        "F05",
        result["state"] == "BLOCKED"
        and github.post_count == 0
        and f"{item['branch']}-retry" not in github.branches
        and github.branches[item["branch"]]["head"] == "other",
        f"state={result['state']} posts={github.post_count} head={github.branches[item['branch']]['head']}",
    )


def test_f06(root: Path) -> None:
    clock, ledger, github, _browser, _workspace, client = world(root / "f06")
    item = manifest(clock)
    ledger.prepare(item)
    files = result_files(item, "body")
    payload = json.loads(files[item["allowed_paths"][0]])
    payload["nonce"] = "0" * 32
    files[item["allowed_paths"][0]] = json.dumps(payload)
    github.publish_snapshot(item["branch"], item["task_id"], files, "bad")
    result = client.recover(item["task_id"])
    check("F06", result["state"] == "BLOCKED" and result["remote"]["commit"] == "bad", f"state={result['state']}")


def test_f07(root: Path) -> None:
    clock, ledger, github, _browser, _workspace, client = world(root / "f07")
    read_only = manifest(clock)
    ledger.prepare(read_only)
    refused = client.create_or_reuse_pr(read_only["task_id"], "abc")
    writable = manifest(clock, mode="write", allow_pr=True, write_targets=[".pro_bridge/results/out.json"])
    ledger.prepare(writable)
    github.drop_pr_response = True
    lost = client.create_or_reuse_pr(writable["task_id"], "abc")
    github.drop_pr_response = False
    reused = client.create_or_reuse_pr(writable["task_id"], "abc")
    duplicated = manifest(clock, mode="write", allow_pr=True, write_targets=[".pro_bridge/results/out.json"])
    ledger.prepare(duplicated)
    github.prs.append({"number": 90, "head": duplicated["branch"], "task_id": duplicated["task_id"], "head_sha": "a"})
    github.prs.append({"number": 91, "head": duplicated["branch"], "task_id": duplicated["task_id"], "head_sha": "b"})
    before = github.create_pr_count
    conflict = client.create_or_reuse_pr(duplicated["task_id"], "abc")
    check(
        "F07",
        github.create_pr_count == 1
        and lost["state"] == "SUBMISSION_UNKNOWN"
        and reused["pr"]["number"] == 1
        and conflict["state"] == "BLOCKED"
        and github.create_pr_count == before
        and github.close_pr_count == 0
        and "PR creation refused" in " ".join(refused["notes"]),
        f"creates={github.create_pr_count} lost={lost['state']} conflict={conflict['state']}",
    )


def test_f08(root: Path) -> None:
    clock, ledger, _github, _browser, workspace, client = world(root / "f08")
    item = manifest(clock)
    ledger.prepare(item)
    before = workspace.fingerprint()
    workspace.junctions[r"D:\temp\link"] = workspace.workspace
    blocked = client.guard_temp_root(item["task_id"], r"D:\temp\link")
    after_block = workspace.fingerprint()
    isolated = manifest(clock)
    ledger.prepare(isolated)
    allowed = client.guard_temp_root(isolated["task_id"], r"D:\temp\poc5-isolated")
    check(
        "F08",
        blocked["state"] == "BLOCKED"
        and after_block == before
        and workspace.stash_count == workspace.reset_count == workspace.clean_count == 0
        and workspace.head == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        and workspace.branch == "main"
        and workspace.dirty is True
        and workspace.writes
        and all(not write.startswith(workspace.workspace) for write in workspace.writes)
        and "protected head unchanged=True" in " ".join(allowed["notes"]),
        f"blocked={blocked['state']} writes={workspace.writes}",
    )


def test_f09(root: Path) -> None:
    clock, ledger, github, _browser, _workspace, client = world(root / "f09")
    item = manifest(clock, mode="write", write_targets=[".pro_bridge/results/out.json"], allowed_paths=[".pro_bridge/results/out.json"])
    ledger.prepare(item)
    moved = client.fast_forward(item["task_id"], "stale-head", {".pro_bridge/results/out.json": "{}"})
    observed = github.head
    outside = client.fast_forward(item["task_id"], observed, {"game/scripts/systems/save_game_store.gd": "nope"})
    check(
        "F09",
        moved["state"] == "BLOCKED"
        and outside["state"] == "BLOCKED"
        and github.force_push_count == 0
        and github.rebase_count == 0
        and github.head == observed,
        f"moved={moved['state']} outside={outside['state']} head={github.head}",
    )


def test_f10(root: Path) -> None:
    clock, ledger, github, _browser, _workspace, client = world(root / "f10")
    item = manifest(clock)
    ledger.prepare(item)
    files = result_files(item, "report", status="PASS")
    github.publish_snapshot(item["branch"], item["task_id"], files, "ci")
    github.ci[item["task_id"]] = {"conclusion": "failure", "unknown_errors": 2, "marker_present": False, "exit_code": 1}
    result = client.recover(item["task_id"])
    check("F10", result["state"] == "RESULT_SEEN" and result["state"] != "VERIFIED", f"state={result['state']}")


def test_f11(root: Path) -> None:
    clock, ledger, github, _browser, workspace, client = world(root / "f11")
    item = manifest(clock, deadline_at=clock.time() + 5)
    ledger.prepare(item)
    submitted = client.submit(item)
    files = result_files(item, "late report")
    github.publish_snapshot(item["branch"], item["task_id"], {item["allowed_paths"][0]: files[item["allowed_paths"][0]]}, "partial")
    clock.advance(6)
    timed_out = client.recover(item["task_id"])
    github.publish_snapshot(item["branch"], item["task_id"], files, "late")
    github.ci[item["task_id"]] = {"conclusion": "success", "unknown_errors": 0, "marker_present": True, "exit_code": 0}
    posts = github.post_count
    still = client.recover(item["task_id"])
    ignored = client.reconcile_late(item["task_id"], human_approved=False)
    noted = client.reconcile_late(item["task_id"], human_approved=True)
    check(
        "F11",
        submitted["state"] == "SUBMITTED"
        and timed_out["state"] == "TIMEOUT"
        and still["state"] == "TIMEOUT"
        and ignored["state"] == "TIMEOUT"
        and noted["state"] == "TIMEOUT"
        and noted["late_evidence"]["commit"] == "late"
        and github.post_count == posts
        and workspace.stash_count == 0,
        f"timeout={timed_out['state']} late={noted['state']} posts={github.post_count}",
    )


def test_f12(root: Path) -> None:
    clock, ledger, github, _browser, _workspace, client = world(root / "f12")
    denied = False
    try:
        ledger.prepare(manifest(clock, write_targets=["game/scripts/systems/core_loop_runtime.gd"]))
    except PermissionError:
        denied = True
    item = manifest(clock)
    ledger.prepare(item)
    ready = client.attempt_forbidden(item["task_id"], "mark_ready")
    files = result_files(item, "v1")
    payload = json.loads(files[item["allowed_paths"][0]])
    payload["protocol_version"] = 1
    payload["self_upgrade"] = True
    files[item["allowed_paths"][0]] = json.dumps(payload)
    github.publish_snapshot(item["branch"], item["task_id"], files, "v1")
    upgraded = client.recover(item["task_id"])
    check(
        "F12",
        denied and ready["state"] == "BLOCKED" and upgraded["state"] == "BLOCKED",
        f"denied={denied} ready={ready['state']} upgrade={upgraded['state']}",
    )


def _sharing_denied() -> PermissionError:
    error = PermissionError(13, "Access is denied")
    error.winerror = 5
    return error


def _hold_without_delete_share(path: Path):
    if sys.platform != "win32":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x00000001, None, 3, 0x80, None)
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        return None
    return kernel, handle


def test_w01(root: Path) -> None:
    real = bridge.os.replace
    calls = {"n": 0}

    def wrapper(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _sharing_denied()
        return real(src, dst)

    bridge.os.replace = wrapper
    try:
        ledger = bridge.Ledger(root / "w01", bridge.Clock())
        item = manifest(bridge.Clock())
        record = ledger.prepare(item)
        loaded = json.loads(ledger.path.read_text(encoding="utf-8"))
        stored = loaded["tasks"][record["task_id"]]
        check(
            "W01",
            2 <= calls["n"] <= 8
            and stored["state"] == "PREPARED"
            and stored["send_attempts"] == 0
            and stored["nonce"] == item["nonce"],
            f"replaces={calls['n']} state={stored['state']}",
        )
    except OSError as exc:
        check("W01", False, type(exc).__name__ + " " + str(exc))
    finally:
        bridge.os.replace = real


def test_w02(root: Path) -> None:
    directory = root / "w02"
    clock = bridge.Clock()
    ledger = bridge.Ledger(directory, clock)
    item = manifest(clock)
    ledger.prepare(item)
    before = ledger.path.read_bytes()
    real = bridge.os.replace
    calls = {"n": 0}

    def wrapper(src, dst):
        calls["n"] += 1
        raise _sharing_denied()

    bridge.os.replace = wrapper
    started = time.monotonic()
    outcome = None
    try:
        try:
            ledger.claim_send(item["task_id"])
            claimed = True
        except bridge.PersistenceError as exc:
            claimed = False
            outcome = exc.outcome
    finally:
        bridge.os.replace = real
    elapsed = time.monotonic() - started
    current = json.loads(ledger.path.read_text(encoding="utf-8"))["tasks"][item["task_id"]]
    check(
        "W02",
        claimed is False
        and outcome == "original_intact"
        and 2 <= calls["n"] <= 6
        and elapsed < 1.5
        and ledger.path.read_bytes() == before
        and current["state"] == "PREPARED"
        and current["send_attempts"] == 0,
        f"outcome={outcome} replaces={calls['n']} elapsed={elapsed:.3f} state={current['state']}",
    )


def test_w03(root: Path) -> None:
    directory = root / "w03"
    clock = bridge.Clock(time.time())
    ledger, github, browser, workspace = world(directory)[1:5]
    item = manifest(clock)
    ledger.prepare(item)
    real = bridge.os.replace

    def deny(src, dst):
        raise _sharing_denied()

    bridge.os.replace = deny
    try:
        try:
            ledger.claim_send(item["task_id"])
            bridge_claimed = True
        except bridge.PersistenceError:
            bridge_claimed = False
        client = bridge.Bridge(ledger, github, browser, workspace)
        try:
            client.submit(item)
        except bridge.PersistenceError:
            pass
        import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["claim", "--ledger", str(directory), "--task", item["task_id"]])
        body = json.loads(buf.getvalue() or "{}")
    finally:
        bridge.os.replace = real
    record = json.loads(ledger.path.read_text(encoding="utf-8"))["tasks"][item["task_id"]]
    check(
        "W03",
        bridge_claimed is False
        and github.post_count == 0
        and code == 4
        and body.get("error") == "critical_persistence_failed"
        and body.get("do_not_resend") is True
        and body.get("automatic_high_risk_continue") is False
        and body.get("recovery") == "read_only_reconcile"
        and body.get("send_authorized") is False
        and body.get("claimed") is not True
        and record["state"] == "PREPARED"
        and record["send_attempts"] == 0,
        f"code={code} posts={github.post_count} error={body.get('error')} state={record['state']}",
    )


def test_w04(root: Path) -> None:
    directory = root / "w04"
    clock, ledger, github, browser, workspace, client = world(directory)
    item = manifest(clock)
    ledger.prepare(item)
    real = bridge.os.replace
    calls = {"n": 0}

    def wrapper(src, dst):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _sharing_denied()
        return real(src, dst)

    bridge.os.replace = wrapper
    try:
        try:
            client.submit(item)
        except OSError:
            pass
    finally:
        bridge.os.replace = real
    posts_after_failure = github.post_count
    restarted = bridge.Bridge(bridge.Ledger(directory, clock), github, browser, workspace)
    second = restarted.submit(item)
    record = json.loads((directory / "ledger.json").read_text(encoding="utf-8"))["tasks"][item["task_id"]]
    check(
        "W04",
        posts_after_failure == 1
        and github.post_count == 1
        and record["send_attempts"] == 1
        and record["state"] == "SUBMISSION_UNKNOWN"
        and second["state"] == "SUBMISSION_UNKNOWN"
        and "may_not_have_been_delivered" in " ".join(second["notes"]),
        f"posts={github.post_count} state={record['state']} attempts={record['send_attempts']}",
    )


def test_w05(root: Path) -> None:
    directory = root / "w05"
    clock, ledger, github, browser, workspace, _client = world(directory)
    item = manifest(clock)
    ledger.prepare(item)
    claimed = ledger.claim_send(item["task_id"])
    reloaded = bridge.Ledger(directory, clock, create=False)
    again = reloaded.claim_send(item["task_id"])
    recovered = bridge.Bridge(reloaded, github, browser, workspace).submit(item)
    check(
        "W05",
        claimed is True
        and again is False
        and recovered["state"] == "SUBMISSION_UNKNOWN"
        and github.post_count == 0
        and reloaded.get(item["task_id"])["send_attempts"] == 1
        and not (directory / "ledger-replacement.json").exists(),
        f"claimed={claimed} again={again} posts={github.post_count} state={recovered['state']}",
    )


def test_w06(root: Path) -> None:
    directory = root / "w06"
    clock = bridge.Clock()
    ledger = bridge.Ledger(directory, clock)
    item = manifest(clock)
    ledger.prepare(item)
    leftover = ledger.path.with_suffix(".json.tmp")
    leftover.write_text('{"tasks":{"not-a-ledger":{"state":"SUBMISSION_UNKNOWN","send_attempts":1}}}', encoding="utf-8")
    reloaded = bridge.Ledger(directory, clock)
    record = reloaded.get(item["task_id"])
    check(
        "W06",
        leftover.is_file()
        and record["state"] == "PREPARED"
        and record["send_attempts"] == 0
        and "not-a-ledger" not in reloaded.path.read_text(encoding="utf-8"),
        f"state={record['state']} tmp={leftover.name}",
    )


def test_w07(root: Path) -> None:
    directory = root / "w07"
    clock = bridge.Clock()
    ledger = bridge.Ledger(directory, clock)
    item = manifest(clock)
    ledger.prepare(item)
    before = ledger.path.read_bytes()
    real = bridge.os.replace
    calls = {"n": 0}

    def wrapper(src, dst):
        calls["n"] += 1
        raise OSError(28, "No space left on device")

    bridge.os.replace = wrapper
    started = time.monotonic()
    outcome = None
    try:
        try:
            data = json.loads(before.decode("utf-8"))
            data["tasks"][item["task_id"]]["notes"].append("must-not-land")
            ledger._write(data)
        except bridge.PersistenceError as exc:
            outcome = exc.outcome
    finally:
        bridge.os.replace = real
    elapsed = time.monotonic() - started
    text = ledger.path.read_text(encoding="utf-8")
    check(
        "W07",
        outcome == "original_intact"
        and calls["n"] == 1
        and elapsed < 0.2
        and ledger.path.read_bytes() == before
        and "must-not-land" not in text,
        f"outcome={outcome} replaces={calls['n']} elapsed={elapsed:.3f}",
    )


def test_w08(root: Path) -> None:
    if sys.platform != "win32":
        check("W08", False, "NOT_VERIFIED non-windows")
        return
    directory = root / "w08"
    clock = bridge.Clock()
    ledger = bridge.Ledger(directory, clock)
    item = manifest(clock)
    ledger.prepare(item)
    before = ledger.path.read_bytes()
    held = _hold_without_delete_share(ledger.path)
    if held is None:
        check("W08", False, "NOT_VERIFIED handle was not opened")
        return
    kernel, handle = held
    sustained = None
    started = time.monotonic()
    try:
        data = json.loads(ledger.path.read_text(encoding="utf-8"))
        data["tasks"][item["task_id"]]["notes"].append("blocked-write")
        try:
            ledger._write(data)
        except bridge.PersistenceError as exc:
            sustained = exc.outcome
    finally:
        kernel.CloseHandle(handle)
    sustained_elapsed = time.monotonic() - started
    intact = ledger.path.read_bytes() == before
    changed = json.loads(before.decode("utf-8"))
    changed["tasks"][item["task_id"]]["notes"].append("released-write")
    holder = _hold_without_delete_share(ledger.path)
    if holder is None:
        check("W08", False, "NOT_VERIFIED second handle was not opened")
        return
    kernel, handle = holder
    errors: list[BaseException] = []
    conflict = threading.Event()
    released = threading.Event()
    # Set only from the real os.replace failure, and only while this handle is still open.
    conflict_before_release = {"ok": False, "winerror": None}
    real_replace = bridge.os.replace

    def observing_replace(src, dst):
        try:
            return real_replace(src, dst)
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if not released.is_set() and isinstance(winerror, int) and winerror in {5, 32, 33, 1224}:
                conflict_before_release["ok"] = True
                conflict_before_release["winerror"] = winerror
                conflict.set()
            raise

    def writer() -> None:
        try:
            ledger._write(changed)
        except OSError as exc:
            errors.append(exc)

    bridge.os.replace = observing_replace
    thread = threading.Thread(target=writer)
    open_handle = handle
    try:
        thread.start()
        observed = conflict.wait(2.0)
        if not observed or not conflict_before_release["ok"] or released.is_set():
            check("W08", False, "NOT_VERIFIED sharing conflict was not observed before handle release")
            return
        kernel.CloseHandle(open_handle)
        open_handle = None
        released.set()
        thread.join(2)
    finally:
        bridge.os.replace = real_replace
        released.set()
        if open_handle is not None:
            kernel.CloseHandle(open_handle)
        thread.join(2)
    text = ledger.path.read_text(encoding="utf-8")
    check(
        "W08",
        sustained == "original_intact"
        and intact
        and sustained_elapsed < 1.5
        and conflict_before_release["ok"]
        and not errors
        and not thread.is_alive()
        and "released-write" in text
        and "blocked-write" not in text,
        f"sustained={sustained} elapsed={sustained_elapsed:.3f} winerror={conflict_before_release['winerror']} errors={len(errors)} alive={thread.is_alive()}",
    )


def test_w09(root: Path) -> None:
    import deploy
    import receipts

    directory = root / "w09"
    directory.mkdir()
    target = directory / "final.json"
    target.write_bytes(b'{"complete": false}\n')
    real = deploy.os.replace

    def deny(src, dst):
        raise _sharing_denied()

    deploy.os.replace = deny
    raised = False
    try:
        try:
            deploy._write_json(target, {"complete": True, "passed": True})
        except OSError:
            raised = True
    finally:
        deploy.os.replace = real
    receipt_failed = False
    try:
        receipts.load(directory / "missing-receipt.json")
    except receipts.ReceiptInputError:
        receipt_failed = True
    body = target.read_bytes()
    check(
        "W09",
        raised
        and receipt_failed
        and body == b'{"complete": false}\n'
        and b"passed" not in body,
        f"raised={raised} receipt_failed={receipt_failed} body={body!r}",
    )


def test_w10(root: Path) -> None:
    directory = root / "w10"
    clock = bridge.Clock()
    ledger = bridge.Ledger(directory, clock)
    item = manifest(clock)
    ledger.prepare(item)
    barrier = threading.Barrier(8)
    outcomes: list[bool] = []

    def worker() -> None:
        local = bridge.Ledger(directory, clock)
        barrier.wait()
        outcomes.append(local.claim_send(item["task_id"]))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    record = bridge.Ledger(directory, clock).get(item["task_id"])
    check(
        "W10",
        outcomes.count(True) == 1
        and outcomes.count(False) == 7
        and record["send_attempts"] == 1
        and record["state"] == "SUBMISSION_UNKNOWN",
        f"claims={outcomes.count(True)} attempts={record['send_attempts']} state={record['state']}",
    )


def test_w11(root: Path) -> None:
    """Temp bytes changed after serialization must not become the expected ledger."""
    directory = root / "w11"
    clock = bridge.Clock()
    ledger = bridge.Ledger(directory, clock)
    item = manifest(clock)
    ledger.prepare(item)
    before = ledger.path.read_bytes()
    tampered = {"done": False}
    foreign = b'{"tampered":"B","send_attempts":1,"state":"SUBMISSION_UNKNOWN"}\n'
    real_fsync = bridge.os.fsync

    def fsync_then_replace_temp(fd: int) -> None:
        real_fsync(fd)
        if tampered["done"]:
            return
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        written = os.write(fd, foreign)
        if written != len(foreign):
            raise OSError("short tamper write")
        real_fsync(fd)
        tampered["done"] = True

    bridge.os.fsync = fsync_then_replace_temp
    claimed = None
    outcome = None
    try:
        try:
            claimed = ledger.claim_send(item["task_id"])
        except bridge.PersistenceError as exc:
            claimed = False
            outcome = exc.outcome
    finally:
        bridge.os.fsync = real_fsync
    current = ledger.path.read_bytes()
    record_state = None
    record_attempts = None
    try:
        record = json.loads(current.decode("utf-8"))["tasks"][item["task_id"]]
        record_state = record.get("state")
        record_attempts = record.get("send_attempts")
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        record_state = None
        record_attempts = None
    check(
        "W11",
        tampered["done"] is True
        and claimed is False
        and outcome == "uncertain"
        and current == before
        and foreign not in current
        and record_state == "PREPARED"
        and record_attempts == 0,
        f"claimed={claimed} outcome={outcome} state={record_state} attempts={record_attempts} tampered={tampered['done']} intact={current == before}",
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="poc5-bridge-") as directory:
        root = Path(directory)
        test_f01(root)
        test_f02(root)
        test_f03(root)
        test_f04(root)
        test_f05(root)
        test_f06(root)
        test_f07(root)
        test_f08(root)
        test_f09(root)
        test_f10(root)
        test_f11(root)
        test_f12(root)
        test_w01(root)
        test_w02(root)
        test_w03(root)
        test_w04(root)
        test_w05(root)
        test_w06(root)
        test_w07(root)
        test_w08(root)
        test_w09(root)
        test_w10(root)
        test_w11(root)
    failed = [item for item in RESULTS if not item[1]]
    print(f"BRIDGE_CASES={len(RESULTS)} FAILED={len(failed)}")
    expected = {f"F{index:02d}" for index in range(1, 13)} | {f"W{index:02d}" for index in range(1, 12)}
    if not failed and {item[0] for item in RESULTS} == expected:
        print("BRIDGE_V2_MOCK_PASS")
        raise SystemExit(0)
    print("BRIDGE_V2_MOCK_FAIL")
    raise SystemExit(1)


if __name__ == "__main__":
    main()

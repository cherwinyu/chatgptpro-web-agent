"""Fault cases F01-F12 for the local bridge protocol mock."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
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
    failed = [item for item in RESULTS if not item[1]]
    print(f"BRIDGE_CASES={len(RESULTS)} FAILED={len(failed)}")
    if not failed and len({item[0] for item in RESULTS}) == 12:
        print("BRIDGE_V2_MOCK_PASS")
        raise SystemExit(0)
    print("BRIDGE_V2_MOCK_FAIL")
    raise SystemExit(1)


if __name__ == "__main__":
    main()

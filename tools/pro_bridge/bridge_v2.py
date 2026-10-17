"""Local at-most-once ledger for the POC-5 bridge protocol mock.

This module never contacts ChatGPT or GitHub. A persisted send claim is
single-use. There is no server idempotency API here, so this is not an
exactly-once guarantee.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path


DELIVERY_GUARANTEE = "at_most_once_local_entitlement_not_exactly_once"
BRIDGE_VERSION = "2.4.0-rc1"
ALLOWED_STATES = {
    "PREPARED",
    "SUBMISSION_UNKNOWN",
    "SUBMITTED",
    "RESULT_SEEN",
    "VERIFIED",
    "TIMEOUT",
    "BLOCKED",
}


def new_task_id() -> str:
    return str(uuid.uuid4())


def new_nonce() -> str:
    return uuid.uuid4().hex


def canonical_bytes(payload: dict) -> bytes:
    body = {key: payload[key] for key in payload if key != "request_sha256"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def request_sha256(payload: dict) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IMMUTABLE_FIELDS = {
    "task_id",
    "nonce",
    "request_sha256",
    "request_bytes",
    "orchestration_id",
    "mode",
    "allow_pr",
    "allowed_paths",
    "baseline_sha",
    "branch",
    "deadline_at",
    "send_attempts",
    "delivery_guarantee",
    "revision",
    "code_write_permission",
    "code_branch",
    "code_paths",
    "source_repository",
    "result_repository",
    "source_sha",
}
RESULT_PERMISSION_KEYS = {"permissions", "allowed_paths", "mode", "write_targets", "allow_pr", "code_write_permission"}
RESULT_STATUSES = {"APPROVE", "REQUEST_CHANGES", "PASS", "FAIL", "COMPLETED_WITH_FINDINGS"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LockTimeout(TimeoutError):
    def __init__(self) -> None:
        super().__init__("lock_timeout")


class StaleRecord(RuntimeError):
    def __init__(self) -> None:
        super().__init__("stale_record")


def exact_result_paths(task_id: str) -> list[str]:
    return [
        f".pro_bridge/results/{task_id}.json",
        f".pro_bridge/reports/{task_id}.md",
    ]


def safe_repo_path(path: str) -> str:
    text = str(path).replace("\\", "/")
    if re.match(r"^[A-Za-z]:", text) or text.startswith("/"):
        raise ValueError("absolute path")
    parts = text.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path traversal")
    return text


def acceptable_branch(branch: str) -> bool:
    """Validate a literal short branch name, without Git's revision expansions."""
    if not isinstance(branch, str) or not branch:
        return False
    text = branch
    if text in {"main", "master", "HEAD", "@"} or text.startswith(("-", "refs/")):
        return False
    if text.endswith(".") or ".." in text or "@{" in text:
        return False
    if any(ord(char) <= 32 or ord(char) == 127 or char in "~^:?*[\\" for char in text):
        return False
    parts = text.split("/")
    return all(part and not part.startswith(".") and not part.endswith(".lock") for part in parts)


def protocol_is_v2(value: object) -> bool:
    return type(value) is int and value == 2


def json_nesting_exceeds(text: str, limit: int = 32) -> bool:
    depth = 0
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > limit:
                return True
        elif char in "}]":
            depth = max(0, depth - 1)
    return False


def load_result_json(text: str) -> object:
    if json_nesting_exceeds(text):
        raise ValueError("json nesting limit")
    return json.loads(text)


def result_contract_error(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return "result json is not an object"
    if not protocol_is_v2(payload.get("protocol_version")) or "self_upgrade" in payload:
        return "v1 or self-upgrade cannot grant v2 authority"
    if RESULT_PERMISSION_KEYS.intersection(payload):
        return "result redefined permissions"
    for key in ("task_id", "nonce", "request_sha256", "baseline_sha", "status", "report_sha256"):
        if not isinstance(payload.get(key), str) or not payload.get(key):
            return "missing or invalid " + key
    if not SHA256_RE.match(payload["report_sha256"]) or not SHA256_RE.match(payload["request_sha256"]) or not SHA_RE.match(payload["baseline_sha"]):
        return "result hash has the wrong form"
    if payload["status"] not in RESULT_STATUSES:
        return "result status is not allowed"
    return None


def binding_error(record: dict) -> str | None:
    """Refuse a row whose stored manifest bytes no longer match the request hash or the materialized authority fields."""
    try:
        manifest = json.loads(record.get("request_bytes") or "")
    except (TypeError, json.JSONDecodeError):
        return "request bytes are not the stored manifest"
    if not isinstance(manifest, dict):
        return "request bytes are not the stored manifest"
    if request_sha256(manifest) != record.get("request_sha256"):
        return "request hash does not match the stored manifest"
    if (record.get("code_write_permission") is True) != (manifest.get("code_write_permission") is True):
        return "materialized code_write_permission"
    for key in ("mode", "allow_pr", "baseline_sha", "branch", "code_branch"):
        if record.get(key) != manifest.get(key):
            return "materialized " + key
    if list(record.get("code_paths") or []) != list(manifest.get("code_paths") or []):
        return "materialized code_paths"
    if list(record.get("allowed_paths") or []) != list(manifest.get("allowed_paths") or []):
        return "materialized allowed_paths"
    if record.get("nonce") != manifest.get("nonce"):
        return "materialized nonce"
    if record.get("source_repository") != manifest.get("source_repository"):
        return "materialized source_repository"
    expected_result = manifest.get("result_repository") or manifest.get("source_repository")
    if record.get("result_repository") != expected_result:
        return "materialized result_repository"
    expected_source = manifest.get("source_sha") or manifest.get("baseline_sha")
    if record.get("source_sha") != expected_source:
        return "materialized source_sha"
    if record.get("deadline_at") != manifest.get("deadline_at"):
        return "materialized deadline_at"
    if record.get("task_id") != manifest.get("task_id"):
        return "materialized task_id"
    return None


def identity_error(selected_task_id: str, record: dict) -> str | None:
    """The queried task, the ledger row, and the stored manifest are one task or the row is not current."""
    if record.get("task_id") != selected_task_id:
        return "selected task does not match the stored row"
    try:
        manifest = json.loads(record.get("request_bytes") or "")
    except (TypeError, json.JSONDecodeError):
        return "request bytes are not the stored manifest"
    if not isinstance(manifest, dict) or manifest.get("task_id") != selected_task_id:
        return "selected task does not match the stored manifest"
    return None


def delivery_current(record: dict, selected_task_id: str | None = None) -> bool:
    """True only when this task's GitHub snapshot matches the current request and generation."""
    selected = record.get("task_id") if selected_task_id is None else selected_task_id
    if identity_error(selected, record) or binding_error(record):
        return False
    if record.get("last_observation") != "current_ok" or record.get("state") != "VERIFIED" or record.get("evidence_origin") != "github":
        return False
    generation = record.get("verification_generation")
    if generation is None or record.get("current_approval_generation") != generation:
        return False
    snapshot = record.get("production_snapshot")
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("task_id") != selected or snapshot.get("request_sha256") != record.get("request_sha256"):
        return False
    if snapshot.get("verification_generation") != generation or snapshot.get("evidence_origin") != "github":
        return False
    if snapshot.get("delivery_integrity") != "VERIFIED":
        return False
    return True


def current_approval(record: dict, selected_task_id: str | None = None) -> bool:
    """Current approval requires the current GitHub delivery and an explicit APPROVE."""
    if not delivery_current(record, selected_task_id):
        return False
    snapshot = record.get("production_snapshot") or {}
    return record.get("review_verdict") == "APPROVE" and record.get("review_approved") is True and snapshot.get("review_verdict") == "APPROVE" and snapshot.get("review_approved") is True


def production_snapshot(record: dict, generation: int | None = None) -> dict:
    evidence = record.get("result_evidence") if isinstance(record.get("result_evidence"), dict) else {}
    return {
        "task_id": record.get("task_id"),
        "state": "VERIFIED",
        "evidence_origin": "github",
        "review_verdict": record.get("review_verdict"),
        "result_evidence": evidence,
        "verified_commit_sha": evidence.get("commit"),
        "request_sha256": record.get("request_sha256"),
        "verification_generation": record.get("verification_generation") if generation is None else generation,
        "delivery_integrity": "VERIFIED",
        "review_approved": record.get("review_approved") is True,
    }


def send_gate(record: dict) -> bool:
    """The claim rule. Displayed browser_send_allowed must use this, not a cached flag."""
    if binding_error(record):
        return False
    try:
        manifest = json.loads(record.get("request_bytes") or "")
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    read_only = record.get("mode") == "read_only" and manifest.get("mode") == "read_only"
    authorized_write = (
        record.get("code_write_permission") is True
        and manifest.get("code_write_permission") is True
        and record.get("mode") == "write"
        and manifest.get("mode") == "write"
    )
    return read_only or authorized_write


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class LiveClock(Clock):
    """Production clock. time() is sampled when called, including after a lock wait."""

    def time(self) -> float:
        return time.time()


class FileLock:
    """Windows mutex. A dead owner is abandoned by the OS; the ledger file is never deleted."""

    def __init__(self, path: Path, timeout: float = 15.0) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self._handle = None

    def _mutex_name(self) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(self.path.resolve()).encode("utf-8")).hexdigest()[:24]
        return "Local\\AgentHandoffBridge-" + digest

    def __enter__(self) -> "FileLock":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.CreateMutexW(None, False, self._mutex_name())
        if not handle:
            raise OSError(ctypes.get_last_error())
        self._handle = handle
        # The mutex remains Windows-only. A verification budget caps its wait;
        # after timeout, an uncontended zero-wait lock can still record revocation.
        from process_runtime import current_budget
        budget = current_budget()
        remaining = self.timeout if budget is None else budget.remaining(self.timeout, allow_zero=True)
        waited = kernel.WaitForSingleObject(handle, max(0, int(remaining * 1000)))
        if waited in (0, 0x80):
            return self
        kernel.CloseHandle(handle)
        self._handle = None
        raise LockTimeout()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is None:
            return None
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.ReleaseMutex(self._handle)
        kernel.CloseHandle(self._handle)
        self._handle = None
        return None


class MockGitHub:
    def __init__(self, head: str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") -> None:
        self.head = head
        self.branches: dict[str, dict] = {}
        self.prs: list[dict] = []
        self.inbox: dict[str, dict] = {}
        self.post_count = 0
        self.create_pr_count = 0
        self.close_pr_count = 0
        self.force_push_count = 0
        self.rebase_count = 0
        self.drop_ack = False
        self.drop_pr_response = False
        self.ci: dict[str, dict] = {}

    def publish_snapshot(self, branch: str, owner: str, files: dict[str, str], commit: str) -> None:
        current = self.branches.get(branch, {"owner": owner, "commits": []})
        current["owner"] = current.get("owner", owner)
        commits = list(current.get("commits", []))
        commits.append({"sha": commit, "files": dict(files)})
        current["commits"] = commits
        current["head"] = commit
        current["files"] = dict(files)
        self.branches[branch] = current

    def query_prs(self, head_branch: str) -> list[dict]:
        return [item for item in self.prs if item["head"] == head_branch]


class MockBrowser:
    def __init__(self, github: MockGitHub) -> None:
        self.github = github
        self.online = True

    def post_task(self, manifest: dict) -> dict:
        if not self.online:
            raise ConnectionError("browser offline")
        self.github.post_count += 1
        self.github.inbox[manifest["task_id"]] = {
            "task_id": manifest["task_id"],
            "nonce": manifest["nonce"],
            "request_sha256": manifest["request_sha256"],
            "accepted": True,
        }
        if self.github.drop_ack:
            raise TimeoutError("ack lost after accept")
        return {"accepted": True, "task_id": manifest["task_id"]}


class WorkspaceGuard:
    def __init__(self, workspace: str, head: str, branch: str, dirty: bool) -> None:
        self.workspace = str(Path(workspace))
        self.head = head
        self.branch = branch
        self.dirty = dirty
        self.status_digest = f"{head}|{branch}|dirty={dirty}"
        self.junctions: dict[str, str] = {}
        self.stash_count = 0
        self.reset_count = 0
        self.clean_count = 0
        self.writes: list[str] = []

    def resolve(self, path: str) -> str:
        current = str(Path(path))
        seen = set()
        while current in self.junctions and current not in seen:
            seen.add(current)
            current = self.junctions[current]
        return str(Path(current))

    def fingerprint(self) -> str:
        return f"{self.head}|{self.branch}|dirty={self.dirty}|writes={len(self.writes)}"

    def check_temp_root(self, temp_root: str) -> str:
        resolved = self.resolve(temp_root)
        workspace = str(Path(self.workspace))
        if resolved == workspace or resolved.startswith(workspace + os.sep):
            return "blocked"
        return "ok"

    def write_temp(self, temp_root: str, relative: str) -> str:
        if self.check_temp_root(temp_root) != "ok":
            return "blocked"
        target = str(Path(self.resolve(temp_root)) / relative)
        self.writes.append(target)
        return "written"


class LedgerMissing(FileNotFoundError):
    """The requested ledger file is not there. Callers must not create a replacement."""


class TaskNotFound(KeyError):
    """The selected task is absent; distinguish it from a malformed ledger."""


class Ledger:
    def __init__(self, root: Path, clock: Clock, *, create: bool = True) -> None:
        self.root = Path(root)
        self.path = self.root / "ledger.json"
        self.lock_path = self.root / "ledger.lock"
        self.clock = clock
        if not self.path.is_file():
            if not create:
                raise LedgerMissing(str(self.path))
            self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(self.lock_path):
            if not self.path.exists():
                if not create:
                    raise LedgerMissing(str(self.path))
                self._write({"tasks": {}})

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def prepare(self, manifest: dict) -> dict:
        self._validate_manifest(manifest)
        stored = dict(manifest)
        stored["request_sha256"] = request_sha256(stored)
        stored_bytes = canonical_bytes(stored)
        with FileLock(self.lock_path):
            data = self._read()
            current = data["tasks"].get(stored["task_id"])
            if current is None:
                record = self._new_record(stored)
                data["tasks"][stored["task_id"]] = record
                self._write(data)
                return dict(record)
            if current["request_bytes"] != stored_bytes.decode("utf-8"):
                current["state"] = "BLOCKED"
                current["revision"] = int(current.get("revision", 0)) + 1
                current["notes"].append("immutable manifest conflict")
                self._write(data)
                return dict(current)
            return dict(current)

    def get(self, task_id: str) -> dict:
        with FileLock(self.lock_path):
            tasks = self._read()["tasks"]
            if task_id not in tasks:
                raise TaskNotFound(task_id)
            return dict(tasks[task_id])

    def claim_send(self, task_id: str) -> bool:
        with FileLock(self.lock_path):
            data = self._read()
            record = data["tasks"][task_id]
            if identity_error(task_id, record):
                return False
            if binding_error(record):
                record["notes"].append("stored manifest does not match its request hash")
                record["revision"] = int(record.get("revision", 0)) + 1
                self._write(data)
                return False
            if record["send_attempts"] != 0 or record["state"] != "PREPARED":
                record["notes"].append("send entitlement already used or state is not sendable")
                self._write(data)
                return False
            if self.clock.time() > record["deadline_at"]:
                record["state"] = "TIMEOUT"
                record["revision"] = int(record.get("revision", 0)) + 1
                record["notes"].append("deadline passed before send; entitlement not consumed")
                self._write(data)
                return False
            record["send_attempts"] = 1
            record["state"] = "SUBMISSION_UNKNOWN"
            record["revision"] = int(record.get("revision", 0)) + 1
            record["notes"].append("send entitlement persisted before network call")
            self._write(data)
            return True

    def begin_verification(self, task_id: str) -> tuple[dict, int]:
        """Start a generation and revoke the previous generation's current approval."""
        with FileLock(self.lock_path):
            data = self._read()
            record = data["tasks"][task_id]
            if record.get("task_id") != task_id:
                raise ValueError("task identity")
            self._archive_current(record)
            generation = int(record.get("verification_generation") or 0) + 1
            record["verification_generation"] = generation
            record["current_approval_generation"] = None
            record["last_observation"] = "verification_started"
            record["review_approved"] = False
            record["delivery_integrity"] = "NOT_CURRENT"
            record["revision"] = int(record.get("revision", 0)) + 1
            record["verification_context"] = {
                "selected_task_id": task_id,
                "request_sha256": record.get("request_sha256"),
                "nonce": record.get("nonce"),
                "baseline_sha": record.get("baseline_sha"),
                "result_repository": record.get("result_repository"),
                "branch": record.get("branch"),
                "verification_generation": generation,
            }
            record.setdefault("notes", []).append(f"verification generation {generation} started")
            self._write(data)
            return dict(record), generation

    def finish_verification(self, task_id: str, generation: int, **changes) -> dict:
        """Commit a conclusion only for the generation that started it."""
        with FileLock(self.lock_path):
            data = self._read()
            record = data["tasks"][task_id]
            if record.get("task_id") != task_id:
                raise ValueError("task identity")
            if int(record.get("verification_generation") or 0) != int(generation):
                raise StaleRecord()
            fixture = bool(changes.pop("fixture_result", False))
            for banned in ("production_snapshot", "verification_generation", "historical_verification", "current_approval_generation", "verification_context"):
                changes.pop(banned, None)
            notes = list(changes.pop("notes", []) or [])
            expected_request = changes.pop("expected_request_sha256", None)
            observed_binding = changes.pop("observed_binding", None)
            context = record.get("verification_context") if isinstance(record.get("verification_context"), dict) else {}
            observed_hash = observed_binding.get("request_sha256") if isinstance(observed_binding, dict) else None
            same_request = (
                context.get("verification_generation") == generation
                and context.get("selected_task_id") == task_id
                and context.get("request_sha256") == record.get("request_sha256")
                and context.get("nonce") == record.get("nonce")
                and context.get("baseline_sha") == record.get("baseline_sha")
                and (expected_request is None or expected_request == record.get("request_sha256"))
                and (observed_hash in (None, context.get("request_sha256")))
                and not identity_error(task_id, record)
                and not binding_error(record)
            )
            if not same_request:
                record["last_observation"] = "request_changed"
                record["review_approved"] = False
                record["current_approval_generation"] = None
                record["delivery_integrity"] = "NOT_CURRENT"
                record.setdefault("notes", []).append("request changed during verification")
                record["revision"] = int(record.get("revision", 0)) + 1
                self._write(data)
                return dict(record)
            if fixture and record.get("production_snapshot"):
                if "historical_fixture_observation" in changes:
                    record["historical_fixture_observation"] = changes["historical_fixture_observation"]
                if "fixture_observation" in changes:
                    record["fixture_observation"] = changes["fixture_observation"]
                if changes.get("last_observation"):
                    record["last_observation"] = changes["last_observation"]
                record["review_approved"] = False
            else:
                new_state = changes.get("state", record["state"])
                if record["state"] in {"BLOCKED", "TIMEOUT"} and new_state != record["state"]:
                    raise ValueError("terminal state")
                for key, value in changes.items():
                    record[key] = value
                if fixture:
                    record["review_approved"] = False
                    record["evidence_origin"] = "fixture"
            if (
                not fixture
                and record.get("last_observation") == "current_ok"
                and record.get("evidence_origin") == "github"
                and record.get("delivery_integrity") == "VERIFIED"
                and record.get("state") == "VERIFIED"
            ):
                record["current_approval_generation"] = generation
                snapshot = production_snapshot(record, generation)
                record["production_snapshot"] = snapshot
                record["historical_verification"] = dict(snapshot)
            if record["state"] not in ALLOWED_STATES:
                raise ValueError(record["state"])
            record.setdefault("notes", []).extend(notes)
            record["revision"] = int(record.get("revision", 0)) + 1
            self._write(data)
            return dict(record)

    def invalidate_selected(self, task_id: str, reason: str) -> dict:
        """Drop current approval on the selected ledger key only. Never follow a row's task_id."""
        with FileLock(self.lock_path):
            data = self._read()
            record = data["tasks"][task_id]
            record["last_observation"] = reason
            record["review_approved"] = False
            record["current_approval_generation"] = None
            record["delivery_integrity"] = "NOT_CURRENT"
            record.setdefault("notes", []).append(reason)
            record["revision"] = int(record.get("revision", 0)) + 1
            self._write(data)
            return dict(record)

    def note_start_failed(self, task_id: str, observed_generation: int | None) -> dict:
        """Record a failed start only while the observed generation is still current."""
        with FileLock(self.lock_path):
            data = self._read()
            record = data["tasks"][task_id]
            if record.get("verification_generation") != observed_generation:
                return dict(record)
            record["last_observation"] = "verification_start_failed"
            record["review_approved"] = False
            record["current_approval_generation"] = None
            record["delivery_integrity"] = "NOT_CURRENT"
            record.setdefault("notes", []).append("verification start failed")
            record["revision"] = int(record.get("revision", 0)) + 1
            self._write(data)
            return dict(record)

    def _archive_current(self, record: dict) -> None:
        if record.get("production_snapshot"):
            return
        if record.get("last_observation") == "current_ok" and record.get("evidence_origin") == "github" and record.get("delivery_integrity") == "VERIFIED":
            snapshot = production_snapshot(record, record.get("current_approval_generation", record.get("verification_generation")))
            record["production_snapshot"] = snapshot
            record["historical_verification"] = dict(snapshot)
            return
        if record.get("evidence_origin") == "fixture" and record.get("state") == "VERIFIED" and record.get("delivery_integrity") == "VERIFIED":
            record["historical_verification"] = {
                "state": "VERIFIED",
                "delivery_integrity": "VERIFIED",
                "review_verdict": record.get("review_verdict"),
                "evidence_origin": "fixture",
                "result_evidence": record.get("result_evidence"),
                "verified_commit_sha": (record.get("result_evidence") or {}).get("commit") if isinstance(record.get("result_evidence"), dict) else None,
                "request_sha256": record.get("request_sha256"),
                "verification_generation": record.get("verification_generation"),
            }

    def update(self, task_id: str, expected_revision: int | None = None, expected_state: str | None = None, **changes) -> dict:
        with FileLock(self.lock_path):
            data = self._read()
            record = data["tasks"][task_id]
            if expected_revision is not None and int(record.get("revision", 0)) != expected_revision:
                raise StaleRecord()
            if expected_state is not None and record["state"] != expected_state:
                raise StaleRecord()
            if "prompt" in changes:
                raise ValueError("prompt is derived from the manifest")
            new_state = changes.get("state", record["state"])
            if record["state"] in {"BLOCKED", "TIMEOUT"} and new_state != record["state"]:
                raise ValueError("terminal state")
            illegal = IMMUTABLE_FIELDS.intersection(changes)
            if illegal:
                raise ValueError("immutable field " + ",".join(sorted(illegal)))
            for key, value in changes.items():
                if key == "notes":
                    record["notes"].extend(value)
                else:
                    record[key] = value
            if record["state"] not in ALLOWED_STATES:
                raise ValueError(record["state"])
            record["revision"] = int(record.get("revision", 0)) + 1
            self._write(data)
            return dict(record)

    def _new_record(self, manifest: dict) -> dict:
        return {
            "task_id": manifest["task_id"],
            "nonce": manifest["nonce"],
            "request_sha256": manifest["request_sha256"],
            "request_bytes": canonical_bytes(manifest).decode("utf-8"),
            "orchestration_id": manifest["orchestration_id"],
            "mode": manifest["mode"],
            "allow_pr": manifest["allow_pr"],
            "allowed_paths": list(manifest["allowed_paths"]),
            "baseline_sha": manifest["baseline_sha"],
            "branch": manifest["branch"],
            "deadline_at": manifest["deadline_at"],
            "state": "PREPARED",
            "send_attempts": 0,
            "notes": ["prepared immutable request"],
            "remote": None,
            "pr": None,
            "late_evidence": None,
            "delivery_guarantee": DELIVERY_GUARANTEE,
            "revision": 0,
            "code_write_permission": manifest.get("code_write_permission") is True,
            "code_branch": manifest.get("code_branch"),
            "code_paths": list(manifest.get("code_paths") or []),
            "source_repository": manifest.get("source_repository"),
            "result_repository": manifest.get("result_repository") or manifest.get("source_repository"),
            "source_sha": manifest.get("source_sha") or manifest["baseline_sha"],
        }

    def _validate_manifest(self, manifest: dict) -> None:
        required = [
            "task_id",
            "nonce",
            "orchestration_id",
            "mode",
            "allowed_paths",
            "baseline_sha",
            "branch",
            "deadline_at",
            "allow_pr",
            "write_targets",
        ]
        missing = [key for key in required if key not in manifest]
        if missing:
            raise ValueError("missing " + ",".join(missing))
        if manifest["mode"] not in {"read_only", "write"}:
            raise ValueError("mode")
        if not UUID_RE.match(str(manifest["task_id"])):
            raise ValueError("task_id")
        if not NONCE_RE.match(str(manifest["nonce"])):
            raise ValueError("nonce")
        if not SHA_RE.match(str(manifest["baseline_sha"])):
            raise ValueError("baseline_sha")
        if not acceptable_branch(str(manifest["branch"])):
            raise ValueError("branch")
        deadline = manifest["deadline_at"]
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(float(deadline)):
            raise ValueError("deadline")
        allowed = [safe_repo_path(path) for path in manifest["allowed_paths"]]
        writes = [safe_repo_path(path) for path in manifest["write_targets"]]
        if manifest["mode"] == "read_only" and (writes != allowed or allowed != exact_result_paths(manifest["task_id"])):
            raise PermissionError("read_only manifest cannot declare write targets")
        if manifest["allow_pr"] and manifest["mode"] != "write":
            raise PermissionError("read_only task cannot allow PR creation")
        for repo_key in ("source_repository", "result_repository"):
            if repo_key in manifest and manifest[repo_key] is not None and not REPO_RE.match(str(manifest[repo_key])):
                raise ValueError(repo_key)
        if manifest.get("result_repository") and manifest.get("source_repository") and manifest["result_repository"] != manifest["source_repository"]:
            raise ValueError("result repository does not match source repository")
        if manifest.get("code_write_permission") is True:
            if manifest["mode"] != "write" or not manifest.get("code_branch") or not acceptable_branch(str(manifest["code_branch"])):
                raise PermissionError("code write permission is incomplete")
            code_paths = [safe_repo_path(path) for path in manifest.get("code_paths") or []]
            expected = f"tests/pro_bridge/fixtures/pro_codewrite_canary_{manifest['task_id']}.json"
            if code_paths != [expected]:
                raise PermissionError("code write permission is incomplete")
        elif manifest.get("code_write_permission") not in (None, False):
            raise ValueError("code_write_permission")
        elif manifest.get("code_branch") or list(manifest.get("code_paths") or []):
            raise PermissionError("code scope without permission")
        if "protocol_version" in manifest and not protocol_is_v2(manifest.get("protocol_version")):
            raise ValueError("protocol_version")
        task_type = manifest.get("task_type")
        if task_type is not None:
            if task_type in {"READ_ONLY_REVIEW", "RESEARCH"} and manifest["mode"] != "read_only":
                raise ValueError("task_type")
            if task_type == "CODE_WRITE_DRAFT" and manifest["mode"] != "write":
                raise ValueError("task_type")
            if task_type not in {"READ_ONLY_REVIEW", "RESEARCH", "CODE_WRITE_DRAFT"}:
                raise ValueError("task_type")
        if manifest.get("source_sha") not in (None, manifest["baseline_sha"]):
            raise ValueError("source_sha")
        if "deadline" in manifest and manifest["deadline"] != manifest["deadline_at"]:
            raise ValueError("deadline")
        permissions = manifest.get("permissions")
        if permissions is not None:
            if not isinstance(permissions, dict):
                raise ValueError("permissions")
            if permissions.get("allow_main_write") is True:
                raise PermissionError("permissions allow a main write")
            if "allow_pr" in permissions and permissions["allow_pr"] != manifest["allow_pr"]:
                raise ValueError("permissions allow_pr")
            if "result_paths" in permissions and list(permissions["result_paths"]) != allowed:
                raise ValueError("permissions result_paths")
            if "result_branch" in permissions and permissions["result_branch"] != manifest["branch"]:
                raise ValueError("permissions result_branch")
            if "explicit_code_write" in permissions and (permissions.get("explicit_code_write") is True) != (manifest.get("code_write_permission") is True):
                raise PermissionError("permissions explicit_code_write")
            if "code_branch" in permissions and permissions.get("code_branch") != manifest.get("code_branch"):
                raise ValueError("permissions code_branch")
            if "code_paths" in permissions and list(permissions.get("code_paths") or []) != list(manifest.get("code_paths") or []):
                raise ValueError("permissions code_paths")
            if "read_repositories" in permissions and permissions.get("read_repositories") != [manifest.get("source_repository")]:
                raise ValueError("permissions read_repositories")


class Bridge:
    def __init__(self, ledger: Ledger, github: MockGitHub, browser: MockBrowser, workspace: WorkspaceGuard) -> None:
        self.ledger = ledger
        self.github = github
        self.browser = browser
        self.workspace = workspace

    def submit(self, manifest: dict, crash_after_claim: bool = False) -> dict:
        preflight = self._preflight(manifest)
        if preflight is not None:
            return preflight
        if not self.ledger.claim_send(manifest["task_id"]):
            return self.recover(manifest["task_id"], reason="duplicate submit uses read-only recovery")
        if crash_after_claim:
            raise RuntimeError("crash after send entitlement was persisted")
        try:
            self.browser.post_task(self.ledger.get(manifest["task_id"]) | {"nonce": manifest["nonce"], "request_sha256": manifest["request_sha256"], "task_id": manifest["task_id"]})
        except TimeoutError:
            return self.ledger.update(manifest["task_id"], notes=["ack lost; remote may have accepted; no resend"])
        except ConnectionError as exc:
            return self.ledger.update(manifest["task_id"], state="BLOCKED", notes=[str(exc) + "; no resend"])
        return self.ledger.update(manifest["task_id"], state="SUBMITTED", notes=["ack matched task"])

    def recover(self, task_id: str, reason: str = "read-only recovery") -> dict:
        record = self.ledger.get(task_id)
        notes = [reason]
        if record["send_attempts"] > 0 and record["state"] == "SUBMISSION_UNKNOWN":
            notes.append("may_not_have_been_delivered")
        if self.ledger.clock.time() > record["deadline_at"] and record["state"] not in {"VERIFIED", "BLOCKED"}:
            late = self._read_remote(record)
            return self.ledger.update(
                task_id,
                state="TIMEOUT",
                late_evidence=late,
                notes=notes + ["deadline reached; no renewal and no resend"],
            )
        branch_decision = self._classify_branch(record)
        if branch_decision == "foreign":
            return self.ledger.update(task_id, state="BLOCKED", notes=notes + ["foreign result branch; no retry branch and no overwrite"])
        observation = self._read_remote(record)
        if observation is None:
            if record["state"] == "PREPARED":
                return self.ledger.update(task_id, notes=notes)
            return self.ledger.update(task_id, notes=notes + ["no remote artifact; remain read-only"])
        return self._consume_observation(record, observation, notes)

    def reconcile_late(self, task_id: str, human_approved: bool) -> dict:
        record = self.ledger.get(task_id)
        if record["state"] != "TIMEOUT":
            return record
        if not human_approved:
            return self.ledger.update(task_id, notes=["late result ignored without human approval"])
        observation = self._read_remote(record)
        return self.ledger.update(
            task_id,
            late_evidence=observation,
            notes=["human-approved read-only reconcile; state stays TIMEOUT; no resend and no write"],
        )

    def create_or_reuse_pr(self, task_id: str, head_sha: str) -> dict:
        record = self.ledger.get(task_id)
        if not record["allow_pr"] or record["mode"] != "write":
            return self.ledger.update(task_id, notes=["PR creation refused for read-only task"])
        matches = self.github.query_prs(record["branch"])
        if len(matches) > 1:
            return self.ledger.update(task_id, state="BLOCKED", pr={"matches": len(matches)}, notes=["multiple PRs; not closed and not recreated"])
        if len(matches) == 1:
            return self.ledger.update(task_id, pr=matches[0], notes=["existing PR reused"])
        self.github.create_pr_count += 1
        created = {"number": len(self.github.prs) + 1, "head": record["branch"], "task_id": task_id, "head_sha": head_sha}
        self.github.prs.append(created)
        if self.github.drop_pr_response:
            return self.ledger.update(task_id, state="SUBMISSION_UNKNOWN", notes=["PR create response lost; no immediate retry"])
        return self.ledger.update(task_id, pr=created, notes=["PR created once"])

    def fast_forward(self, task_id: str, expected_head: str, files: dict[str, str]) -> dict:
        record = self.ledger.get(task_id)
        if record["mode"] != "write":
            return self.ledger.update(task_id, state="BLOCKED", notes=["write refused for read-only task"])
        outside = [path for path in files if not self._path_allowed(path, record["allowed_paths"])]
        observed = self.github.head
        if observed != expected_head:
            return self.ledger.update(task_id, state="BLOCKED", notes=[f"head changed from {expected_head} to {observed}; no force and no rebase"])
        if outside:
            return self.ledger.update(task_id, state="BLOCKED", notes=["diff left the allowlist: " + ",".join(outside)])
        self.github.head = "new-head"
        return self.ledger.update(task_id, notes=["fast-forward accepted"])

    def attempt_forbidden(self, task_id: str, action: str) -> dict:
        return self.ledger.update(task_id, state="BLOCKED", notes=[f"forbidden action refused: {action}"])

    def guard_temp_root(self, task_id: str, temp_root: str) -> dict:
        before = self.workspace.fingerprint()
        decision = self.workspace.check_temp_root(temp_root)
        if decision != "ok":
            after = self.workspace.fingerprint()
            same = before == after and self.workspace.stash_count == self.workspace.reset_count == self.workspace.clean_count == 0
            return self.ledger.update(task_id, state="BLOCKED", notes=[f"temp root blocked; workspace unchanged={same}"])
        self.workspace.write_temp(temp_root, "note.txt")
        after = self.workspace.fingerprint()
        unchanged = self.workspace.head == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" and self.workspace.stash_count == 0
        return self.ledger.update(task_id, notes=[f"isolated write recorded; protected head unchanged={unchanged}; fingerprint_changed={before != after}"])

    def _preflight(self, manifest: dict) -> dict | None:
        record = self.ledger.get(manifest["task_id"])
        if record["state"] == "BLOCKED":
            return record
        if self._classify_branch(record) == "foreign":
            return self.ledger.update(manifest["task_id"], state="BLOCKED", notes=["preflight found a foreign branch; send not consumed"])
        if manifest["mode"] == "read_only":
            try:
                writes = [safe_repo_path(path) for path in manifest.get("write_targets") or []]
            except ValueError:
                writes = ["invalid"]
            if writes != exact_result_paths(manifest["task_id"]):
                return self.ledger.update(manifest["task_id"], state="BLOCKED", notes=["read_only write target refused"])
        return None

    def _classify_branch(self, record: dict) -> str:
        branch = self.github.branches.get(record["branch"])
        if branch is None:
            return "absent"
        if branch.get("owner") == record["task_id"]:
            return "owned"
        return "foreign"

    def _read_remote(self, record: dict) -> dict | None:
        branch = self.github.branches.get(record["branch"])
        if branch is None or not branch.get("commits"):
            return None
        head = branch["commits"][-1]
        return {"commit": head["sha"], "files": dict(head["files"]), "owner": branch.get("owner")}

    def _consume_observation(self, record: dict, observation: dict, notes: list[str]) -> dict:
        files = observation["files"]
        outside = [path for path in files if not self._path_allowed(path, record["allowed_paths"])]
        if outside:
            return self.ledger.update(record["task_id"], state="BLOCKED", remote=observation, notes=notes + ["remote path outside allowlist"])
        json_text = files.get(record["allowed_paths"][0]) if record["allowed_paths"] else None
        report_text = files.get(record["allowed_paths"][1]) if len(record["allowed_paths"]) > 1 else None
        if json_text is None:
            return self.ledger.update(record["task_id"], notes=notes + ["result json missing"])
        try:
            payload = load_result_json(json_text)
        except (json.JSONDecodeError, ValueError, RecursionError):
            return self.ledger.update(record["task_id"], state="BLOCKED", notes=notes + ["result json rejected"])
        contract_error = result_contract_error(payload)
        if contract_error:
            return self.ledger.update(record["task_id"], state="BLOCKED", remote=observation, notes=notes + [contract_error])
        identity_ok = (
            payload.get("task_id") == record["task_id"]
            and payload.get("nonce") == record["nonce"]
            and payload.get("request_sha256") == record["request_sha256"]
            and payload.get("baseline_sha") == record["baseline_sha"]
        )
        if not identity_ok:
            return self.ledger.update(record["task_id"], state="BLOCKED", remote=observation, notes=notes + ["task, nonce, request sha, or baseline mismatch"])
        if report_text is None:
            return self.ledger.update(record["task_id"], state="RESULT_SEEN", remote=observation, notes=notes + ["json arrived; markdown missing; not verified"])
        if content_sha256(report_text) != payload.get("report_sha256"):
            return self.ledger.update(record["task_id"], state="BLOCKED", remote=observation, notes=notes + ["report hash mismatch"])
        ci = self.github.ci.get(record["task_id"], {"conclusion": "missing", "unknown_errors": 1, "marker_present": False, "exit_code": 1})
        claimed_success = payload.get("status") == "PASS"
        ci_ok = ci.get("conclusion") == "success" and ci.get("unknown_errors") == 0 and ci.get("marker_present") is True and ci.get("exit_code") == 0
        if not ci_ok:
            return self.ledger.update(
                record["task_id"],
                state="RESULT_SEEN",
                remote=observation,
                notes=notes + [f"json claimed success={claimed_success} but CI is not acceptable; not verified"],
            )
        both_in_same_commit = record["allowed_paths"][0] in files and record["allowed_paths"][1] in files
        if not both_in_same_commit:
            return self.ledger.update(record["task_id"], state="RESULT_SEEN", remote=observation, notes=notes + ["artifacts are not one snapshot"])
        return self.ledger.update(record["task_id"], state="VERIFIED", remote=observation, notes=notes + ["same snapshot, hashes, and CI accepted"])

    def _path_allowed(self, path: str, allowed: list[str]) -> bool:
        return path in allowed

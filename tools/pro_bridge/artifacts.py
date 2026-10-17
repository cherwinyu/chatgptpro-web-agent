"""Exclusive per-run evidence directories with input and output byte identities.

These are private test artifacts, not redacted diagnostic exports. Roles label
who requested a run, not proof of reviewer independence. Existing runs are
never resumed, overwritten, moved or cleaned automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import deploy
import runtime_identity


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RunArtifacts:
    def __init__(self, directory: Path, source: Path, *, role: str, commands: list):
        self.root = deploy._safe_location(directory)
        self.source = deploy._safe_location(source)
        protected = [self.source]
        for key in ("PRO_BRIDGE_LEDGER", "PRO_BRIDGE_PROTECTED_LEDGER"):
            if os.environ.get(key):
                protected.append(deploy._safe_location(Path(os.environ[key])))
        if any(self.root == item or item in self.root.parents for item in protected):
            raise ValueError("artifacts must be outside sources and protected ledgers")
        if role not in {"implementer_selftest", "independent_qa", "ci_regression", "candidate_acceptance"}:
            raise ValueError("explicit supported evidence role required")
        # Atomic exclusive allocation also arbitrates simultaneous invocations.
        self.root.mkdir(parents=True, exist_ok=False)
        self.finished = False
        self.before = runtime_identity.inventory(self.source)
        self.metadata = {"schema_version": 1, "run_id": str(uuid.uuid4()), "role": role,
                         "source_commit": runtime_identity.worktree_head(self.source),
                         "source_files": self.before, "source_digest": runtime_identity.mapping_digest(self.before),
                         "commands": commands, "started_at": time.time(), "web_send_count": 0,
                         "role_is_independent_attestation": False}
        self.write_json("run-input.json", self.metadata)

    def write(self, name: str, raw: bytes) -> None:
        if self.finished or not runtime_identity.safe_name(name):
            raise ValueError("invalid or completed evidence write")
        target = deploy._safe_location(self.root / name)
        if self.root not in target.parents:
            raise ValueError("evidence path escapes run")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def write_json(self, name: str, value: dict) -> None:
        self.write(name, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))

    def finish(self, passed: bool) -> dict:
        if self.finished:
            raise ValueError("run evidence is already finalized")
        after = runtime_identity.inventory(self.source)
        unchanged = after == self.before
        files = {}
        for path in sorted(self.root.rglob("*")):
            runtime_identity.reject_link(path)
            if path.is_file():
                if path.stat().st_nlink != 1:
                    raise ValueError("evidence contains linked files")
                files[path.relative_to(self.root).as_posix()] = file_digest(path)
        result = {"schema_version": 1, "run_id": self.metadata["run_id"],
                  "role": self.metadata["role"], "source_commit": self.metadata["source_commit"],
                  "input_digest": self.metadata["source_digest"], "source_unchanged": unchanged,
                  "completed_at": time.time(), "passed": bool(passed and unchanged), "files": files,
                  "self_index_excluded": True, "web_send_count": 0}
        self.write_json("evidence-index.json", result)
        self.finished = True
        return result

"""Export raw Git blobs and install an isolated bridge directory transactionally.

Never deploy over a Git checkout or a live ledger. Callers must quiesce users
of the installation and independently pin the release manifest's SHA-256.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import runtime_identity as identity


class DeploymentFailure(RuntimeError):
    def __init__(self, report: dict) -> None:
        super().__init__(report.get("error", "deployment failed"))
        self.report = report


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "--no-pager", "-C", str(repo), *args],
                          capture_output=True, timeout=30, check=True).stdout


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _safe_location(path: Path) -> Path:
    # Do not resolve away a junction before checking it.
    absolute = Path(os.path.abspath(path))
    for part in (absolute, *absolute.parents):
        if part.exists() or part.is_symlink():
            identity.reject_link(part)
    # Canonicalize only AFTER rejecting links; Windows 8.3 aliases must compare equally.
    return absolute.resolve()


def _snapshot(root: Path) -> dict[str, str]:
    """Backups include *all* regular files, not only the managed source scope."""
    result: dict[str, str] = {}
    identity.reject_link(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in [*dirs, *files]:
            path = parent / name
            identity.reject_link(path)
            if name.casefold() in (".git", ".pro_bridge", "ledger.json"):
                raise ValueError("refusing deployment over Git metadata or task data: " + str(path))
        for name in files:
            path = parent / name
            if not path.is_file():
                raise ValueError("non-regular backup file")
            result[path.relative_to(root).as_posix()] = identity.digest(path.read_bytes())
    return dict(sorted(result.items()))


def export_runtime_release(repo: Path, commit: str, output: Path) -> dict:
    return export_release(repo, commit, output, package_kind="runtime")


def export_release(repo: Path, commit: str, output: Path, *, package_kind: str = "release") -> dict:
    if not identity.COMMIT.fullmatch(commit):
        raise ValueError("export requires a full immutable commit SHA")
    resolved = _git(repo, "rev-parse", "--verify", commit + "^{commit}").decode("ascii").strip()
    if resolved != commit:
        raise ValueError("commit did not resolve to the requested object")
    output = _safe_location(output)
    repository_root = Path(_git(repo, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    if output == repository_root or repository_root in output.parents:
        raise ValueError("export destination must be outside the source Git checkout")
    if output.exists():
        raise ValueError("export destination already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="." + output.name + ".export-", dir=output.parent))
    try:
        roots = identity.managed_roots_for(package_kind)
        files: dict[str, str] = {}
        folded: set[str] = set()
        for entry in _git(repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
            if not entry:
                continue
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            name = raw_name.decode("utf-8")
            if not identity.in_scope(name, roots):
                continue
            if (not identity.safe_name(name) or name.casefold() in folded
                    or kind != b"blob" or mode not in (b"100644", b"100755")):
                raise ValueError("unsafe Git release entry: " + name)
            folded.add(name.casefold())
            # No text=True, textconv, filters, shell redirection or archive attributes.
            raw = _git(repo, "cat-file", "blob", oid.decode("ascii"))
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            files[name] = identity.digest(raw)
        manifest = {"schema_version": 1, "source_commit": commit, "package_kind": package_kind,
                    "managed_roots": list(roots), "runtime_roots": list(identity.RUNTIME_ROOTS),
                    "files": dict(sorted(files.items())),
                    "runtime_source_digest": identity.mapping_digest({p: h for p, h in files.items() if identity.in_scope(p, identity.RUNTIME_ROOTS)})}
        _write_json(stage / identity.MANIFEST, manifest)
        pin = identity.digest((stage / identity.MANIFEST).read_bytes())
        identity.verify_release(stage, pin)
        stage.rename(output)
        return {"exported": True, "output": str(output), "source_commit": commit,
                "manifest_sha256": pin, "runtime_source_digest": manifest["runtime_source_digest"],
                "files_exported": len(files), "trust_instruction": "verify this manifest digest independently before install"}
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _literal_python_script(line: str, launcher: str) -> str:
    """Find Python's script operand, never a later sys.argv value.

    This is a deliberately small literal grammar, not a shell interpreter.
    Reject unknown options, code/module/stdin modes and shell composition.
    Quoted paths must occupy a whole token; preserve Windows backslashes.
    """
    if any(char in line for char in ";&|^`\r\n"):
        raise ValueError("shell composition or escaping is not supported")
    token_pattern = re.compile(r'''(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s"'`]+)(?=[ \t]|$)''')
    tokens = []
    position = 0
    while position < len(line):
        if line[position] in " \t":
            position += 1
            continue
        match = token_pattern.match(line, position)
        if match is None:
            raise ValueError("ambiguous literal quoting")
        token = match.group()
        tokens.append(token[1:-1] if token.startswith(('"', "'")) else token)
        position = match.end()
    index = 0
    # Only the documented simple py launcher selectors are supported here.
    if launcher in ("py", "py.exe") and tokens and re.fullmatch(r"-3(?:\.\d+)?", tokens[0]):
        index += 1
    flags = {"-b", "-bb", "-B", "-d", "-E", "-I", "-O", "-OO", "-P", "-q", "-s", "-S", "-u", "-v"}
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token in flags:
            index += 1
            continue
        if token in ("-W", "-X") or token.startswith(("-W", "-X")):
            option = token[:2]
            if len(token) == 2:
                index += 1
                if index >= len(tokens):
                    raise ValueError("missing interpreter option value")
                value = tokens[index]
            else:
                value = token[2:]
            if not value or (option == "-X" and value not in ("utf8", "utf8=0", "utf8=1")):
                raise ValueError("unsupported interpreter option value")
            index += 1
            continue
        if token.startswith("-"):
            # Includes -c/-m (separate or attached), stdin, --help and --version.
            raise ValueError("not an approved literal-script invocation")
        break
    if index >= len(tokens) or not tokens[index] or tokens[index].startswith("-"):
        raise ValueError("missing literal script operand")
    # Everything after this operand is script argv, never another entrypoint.
    return tokens[index]


def inspect_skill(skill: Path | None, destination: Path) -> dict:
    if skill is None:
        return {"status": "NOT_CHECKED", "reason": "no installed skill supplied"}
    skill = _safe_location(skill)
    destination = _safe_location(destination)
    raw = skill.read_bytes()
    text = raw.decode("utf-8-sig")
    calls = []
    unresolved = []
    # Recognize literal command lines and inline code spans only. A Python word
    # inside a comment, echo or shell wrapper is not evidence of execution.
    for match in re.finditer(r"(?m)(?:^|[`\s])(?P<launcher>python(?:\.exe)?|py(?:\.exe)?)[ \t]+(?P<args>[^\r\n`]+)", text):
        line = match.group("args").strip()
        start = match.start("launcher")
        boundary = max(text.rfind("\n", 0, start), text.rfind("`", 0, start))
        if text[boundary + 1:start].strip():
            unresolved.append(line)
            continue
        try:
            candidate = _literal_python_script(line, match.group("launcher"))
        except ValueError:
            unresolved.append(line)
            continue
        path = Path(candidate)
        if not path.is_absolute() or any(c in candidate for c in ("$", "%", "{", "}")):
            unresolved.append(candidate)
            continue
        calls.append(str(_safe_location(path)))
    allowed = {str(destination / "tools/pro_bridge/cli.py"), str(destination / "adapters/generic/run_bridge.py")}
    ok = bool(calls) and not unresolved and all(call in allowed for call in calls)
    return {"status": "MATCH" if ok else "MISMATCH_OR_UNRESOLVED", "skill_file": str(skill),
            "skill_sha256": identity.digest(raw), "entrypoints": sorted(set(calls)),
            "expected_entrypoints": sorted(allowed), "unresolved_commands": unresolved,
            "inspection": "literal_python_script_operand_only_not_runtime_host_attestation"}


def runtime_check(destination: Path, pin: str) -> dict:
    env = dict(os.environ)
    env[identity.TRUST_ENV] = pin
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONPATH", None)
    # These are pinned, stateless installation health checks, not live dispatch.
    # A pending transaction intentionally blocks normal configured launches.
    env.pop("PRO_BRIDGE_DEPLOYMENT_LOCK", None)
    env.pop("PRO_BRIDGE_DEPLOYMENT_LOCK_SHA256", None)
    command = [sys.executable, "-B", "-X", "utf8", str(destination / "adapters/generic/run_bridge.py")]
    completed = subprocess.run([*command, "identify"], cwd=destination, env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    data = json.loads(completed.stdout)
    if (data.get("provenance_status") != "VERIFIED"
            or data.get("bridge_file") != str((destination / "tools/pro_bridge/cli.py").resolve())):
        raise ValueError("installed CLI identity check failed")
    subprocess.run([*command, "route"], cwd=destination, env=env, capture_output=True, timeout=30, check=True)
    return data


def _paths(destination: Path, token: str) -> dict[str, Path]:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("invalid deployment token")
    prefix = "." + destination.name + "." + token
    result = {key: destination.parent / (prefix + "." + key) for key in ("stage", "backup", "previous", "failed", "journal")}
    result["lock"] = destination.parent / ("." + destination.name + ".deploy.lock")
    return result


# Only "switching" is write-ahead authority to rename the live directory.
# intent exists BEFORE lock acquisition; its contents never establish a prestate.
_PRE_SWITCH = {"intent", "starting", "stage_verified", "backup_verified", "refused_before_switch"}
_SWITCH_PHASES = {"switching", "old_moved", "new_moved", "runtime_checked", "committed", "rolled_back"}


def _recovery_phase(journal: dict) -> str:
    version = journal.get("journal_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported deployment journal; preserving transaction")
    phase = journal.get("phase")
    if phase == "recovered":
        # Preserve the original phase across a crash after the recovery journal
        # is written but before this transaction's lock is removed.
        phase = journal.get("recovery_from_phase", "rolled_back" if version == 1 else None)
    if not isinstance(phase, str) or phase not in _PRE_SWITCH | _SWITCH_PHASES:
        raise ValueError("unknown recovery phase; preserving transaction")
    return phase


def _directory_id(path: Path) -> list[int]:
    identity.reject_link(path)
    if not path.is_dir():
        raise ValueError("expected a transaction directory")
    info = path.stat()
    if not info.st_ino:
        raise ValueError("filesystem cannot identify a staged directory")
    return [info.st_dev, info.st_ino]


def _require_owned_new(destination: Path, paths: dict[str, Path], journal: dict) -> None:
    # A still-present stage was NOT promoted into destination. Never infer
    # ownership of a foreign live tree merely because a switch was intended.
    if paths["stage"].exists():
        raise ValueError("stage not promoted; refusing to move the current installation")
    if journal.get("journal_version", 1) == 2:
        if (_directory_id(destination) != journal.get("staged_directory_id")
                or _snapshot(destination) != journal.get("new_files")):
            raise ValueError("current installation is not the byte-verified staged tree")
    else:
        # Older journals lack directory IDs. Require their independently pinned
        # candidate bytes, not an arbitrary directory; backups still verify below.
        identity.verify_release(destination, journal["manifest_sha256"])
    if paths["failed"].exists():
        raise ValueError("failed-directory evidence already exists; refusing overwrite")


def _restore(destination: Path, paths: dict[str, Path], journal: dict) -> bool:
    """Recover only this transaction. Return whether the live path was changed."""
    phase = _recovery_phase(journal)
    restored = paths["stage"].with_name(paths["stage"].name + ".restore")
    for path in (*paths.values(), restored):
        _safe_location(path)
    if phase in _PRE_SWITCH:
        # No code in these phases is permitted to rename destination. In
        # particular a stale legacy intent is NOT permission to undo installer A.
        # Contradictory switch/restore footprints require manual investigation.
        if any(path.exists() for path in (paths["previous"], paths["failed"], restored)):
            raise ValueError("pre-switch journal has switch artifacts; preserving transaction")
        return False
    if (type(journal.get("had_previous")) is not bool
            or not isinstance(journal.get("old_files"), dict)):
        raise ValueError("missing locked prestate; preserving transaction")
    old = journal["old_files"]
    if journal["had_previous"]:
        if destination.is_dir() and _snapshot(destination) == old:
            return False  # Before first rename, or a previous recovery finished.
        if destination.exists():
            _require_owned_new(destination, paths, journal)
        source = next((p for p in (paths["previous"], paths["backup"]) if p.is_dir() and _snapshot(p) == old), None)
        if source is None:
            raise ValueError("no byte-verified backup is available; preserving all directories")
        restored = _safe_location(restored)
        if restored.exists():
            if not restored.is_dir():
                raise ValueError("recovery staging is not a directory; preserving it")
            staged_files = _snapshot(restored)  # Reject links and protected data.
            if staged_files != old:
                # A prior recovery may itself have died during copy. Preserve
                # that exact transaction-local partial tree, then rebuild. A
                # crash after this rename is safe: the next call copies anew.
                incomplete = restored.with_name(restored.name + ".incomplete-" + uuid.uuid4().hex)
                restored.rename(incomplete)
        if not restored.exists():
            shutil.copytree(source, restored)
        # Complete staging is reused only after exact file-set/byte validation.
        # Never overlay files into the live destination or blindly trust .restore.
        if _snapshot(restored) != old:
            raise ValueError("restore staging verification failed")
        if destination.exists():
            _require_owned_new(destination, paths, journal)
            destination.rename(paths["failed"])
        restored.rename(destination)
        if _snapshot(destination) != old:
            raise ValueError("restored bytes differ from verified backup")
        return True
    elif destination.exists():
        _require_owned_new(destination, paths, journal)
        destination.rename(paths["failed"])
        return True
    return False


def install_release(bundle: Path, destination: Path, pin: str, *, quiesced: bool = False,
                    skill: Path | None = None, health_check=None, fault_hook=None) -> dict:
    if not quiesced:
        raise ValueError("stop bridge users first and explicitly confirm --quiesced")
    bundle = _safe_location(bundle)
    destination = _safe_location(destination)
    if bundle == destination or bundle in destination.parents or destination in bundle.parents:
        raise ValueError("bundle and destination must not contain each other")
    verification = identity.verify_release(bundle, pin)
    skill_check = inspect_skill(skill, destination)
    if skill is not None and skill_check["status"] != "MATCH":
        raise ValueError("installed skill does not point to this deployment: " + json.dumps(skill_check))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not destination.is_dir():
        raise ValueError("destination must be a dedicated directory")
    if destination.exists():
        _snapshot(destination)  # Protection check only; not an authoritative prestate.
    old = None
    token = uuid.uuid4().hex
    paths = _paths(destination, token)
    journal = {"journal_version": 2, "token": token, "destination": str(destination),
               "phase": "intent", "manifest_sha256": pin}
    # Persist a complete intent BEFORE making the exclusive lock visible. A hard
    # link publishes the same complete bytes atomically (no empty/partial lock).
    # No unsafe fallback on filesystems without hard-link support. Later journal
    # updates replace its inode, leaving the lock's initial receipt immutable.
    _write_json(paths["journal"], journal)
    if fault_hook is not None:
        fault_hook("intent_written", paths)
    os.link(paths["journal"], paths["lock"])
    keep_lock = False
    switched = False
    report = {"destination": str(destination), "transaction_token": token,
              "journal": str(paths["journal"]), "backup": str(paths["backup"]), "skill_check": skill_check}

    def phase(value: str) -> None:
        journal["phase"] = value
        _write_json(paths["journal"], journal)
        if fault_hook is not None:
            fault_hook(value, paths)

    try:
        if fault_hook is not None:
            fault_hook("lock_published", paths)
        # Re-establish the authoritative prestate only AFTER acquiring the lock.
        # Another installer may have completed since our pre-lock protection check.
        _safe_location(destination)
        if destination.exists() and not destination.is_dir():
            raise ValueError("destination must be a dedicated directory")
        had_previous = destination.exists()
        old = _snapshot(destination) if had_previous else {}
        journal.update(had_previous=had_previous, old_files=old)
        phase("starting")
        manifest, _ = identity.read_manifest(bundle)
        paths["stage"].mkdir()
        for name in [*manifest["files"], identity.MANIFEST]:
            target = paths["stage"] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((bundle / name).read_bytes())
        identity.verify_release(paths["stage"], pin)
        journal.update(new_files=_snapshot(paths["stage"]),
                       staged_directory_id=_directory_id(paths["stage"]))
        phase("stage_verified")
        if journal["had_previous"]:
            shutil.copytree(destination, paths["backup"])
            if _snapshot(paths["backup"]) != old:
                raise ValueError("backup verification failed")
        phase("backup_verified")
        if (destination.exists() != journal["had_previous"]
                or (destination.exists() and _snapshot(destination) != old)):
            raise ValueError("destination changed while staging; refusing switch")
        identity.verify_release(paths["stage"], pin)
        phase("switching")
        identity.verify_release(paths["stage"], pin)
        if (destination.exists() != journal["had_previous"] or
                (journal["had_previous"] and
                 (_snapshot(paths["backup"]) != old or _snapshot(destination) != old))):
            raise ValueError("destination or backup changed before switch")
        if _directory_id(paths["stage"]) != journal["staged_directory_id"]:
            raise ValueError("staged directory changed before switch")
        # At most two whole-directory renames. Never overlay individual live files.
        switched = True
        if destination.exists():
            destination.rename(paths["previous"])
        phase("old_moved")
        paths["stage"].rename(destination)
        phase("new_moved")
        checked = (health_check or runtime_check)(destination, pin)
        identity.verify_release(destination, pin)
        if inspect_skill(skill, destination) != skill_check:
            raise ValueError("installed skill changed during deployment")
        phase("runtime_checked")
        phase("committed")
        return {**report, "installed": True, "rolled_back": False, "runtime_check": checked,
                "source_commit": verification["source_commit"], "manifest_sha256": pin}
    except BaseException as exc:
        try:
            if switched:
                _restore(destination, paths, journal)
            journal["phase"] = "rolled_back" if switched else "refused_before_switch"
            _write_json(paths["journal"], journal)
        except BaseException as restore_exc:
            keep_lock = True
            raise DeploymentFailure({**report, "installed": False, "rolled_back": False,
                                     "recovery_required": True, "error": str(exc),
                                     "recovery_error": str(restore_exc)}) from exc
        raise DeploymentFailure({**report, "installed": False, "rolled_back": switched,
                                 "destination_unchanged": (None if old is None else
                                     destination.exists() == journal["had_previous"] and
                                     (not destination.exists() or _snapshot(destination) == old)), "error": str(exc)}) from exc
    finally:
        if not keep_lock:
            # Remove only this transaction's lock, never another installer's lock.
            try:
                lock = json.loads(paths["lock"].read_text(encoding="utf-8"))
                if lock.get("token") == token:
                    paths["lock"].unlink()
            except (OSError, ValueError):
                pass


def recover(destination: Path, token: str, *, quiesced: bool = False) -> dict:
    if not quiesced:
        raise ValueError("recovery requires --quiesced; do not interrupt a live installer")
    destination = _safe_location(destination)
    paths = _paths(destination, token)
    for key in ("lock", "journal"):
        identity.reject_link(paths[key])
    lock_bytes = paths["lock"].read_bytes()
    lock = json.loads(lock_bytes, object_pairs_hook=identity._unique)
    journal = json.loads(paths["journal"].read_bytes(), object_pairs_hook=identity._unique)
    if any(not isinstance(item, dict) or item.get("token") != token
           or item.get("destination") != str(destination) for item in (lock, journal)):
        raise ValueError("deployment receipt does not match this destination")
    if (lock.get("journal_version", 1) != journal.get("journal_version", 1)
            or lock.get("manifest_sha256") != journal.get("manifest_sha256")):
        raise ValueError("deployment journal does not match its lock")
    original_phase = _recovery_phase(journal)
    touched = _restore(destination, paths, journal)
    action = "aborted_before_switch" if original_phase in _PRE_SWITCH else "restored_previous"
    journal.update(phase="recovered", recovery_from_phase=original_phase, recovery_action=action)
    _write_json(paths["journal"], journal)
    # Never release a replacement/foreign lock, even if its path is unchanged.
    identity.reject_link(paths["lock"])
    if paths["lock"].read_bytes() != lock_bytes:
        raise ValueError("deployment lock changed during recovery; preserving it")
    paths["lock"].unlink()
    return {"recovered": True, "destination": str(destination), "transaction_token": token,
            "recovery_action": action, "destination_touched": touched,
            "task_ledger_touched": False, "web_send_count": 0}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", allow_abbrev=False)
    export.add_argument("--repository", type=Path, required=True)
    export.add_argument("--commit", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--package-kind", choices=("release", "runtime"), default="release")
    install = sub.add_parser("install", allow_abbrev=False)
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--destination", type=Path, required=True)
    install.add_argument("--manifest-sha256", required=True)
    skill_choice = install.add_mutually_exclusive_group(required=True)
    skill_choice.add_argument("--skill", type=Path)
    skill_choice.add_argument("--no-skill", action="store_true", help="explicitly skip installed-skill inspection for a non-skill host")
    install.add_argument("--quiesced", action="store_true")
    recovery = sub.add_parser("recover", allow_abbrev=False)
    recovery.add_argument("--destination", type=Path, required=True)
    recovery.add_argument("--transaction-token", required=True)
    recovery.add_argument("--quiesced", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_release(args.repository, args.commit, args.output, package_kind=args.package_kind)
        elif args.command == "install":
            result = install_release(args.bundle, args.destination, args.manifest_sha256,
                                     quiesced=args.quiesced, skill=args.skill)
        else:
            result = recover(args.destination, args.transaction_token, quiesced=args.quiesced)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except DeploymentFailure as exc:
        print(json.dumps(exc.report, ensure_ascii=False, sort_keys=True))
        return 3
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc), "installed": False, "task_ledger_touched": False,
                          "web_send_count": 0}, ensure_ascii=False, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

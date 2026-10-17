"""One monotonic I/O budget and owned process-tree cleanup.

Windows uses an anonymous, non-breakaway Job Object. A small Python worker
waits for input BEFORE it can spawn the requested process; it is assigned to
our job before that input is sent. No PID/name-wide taskkill is used. POSIX
process groups exist for offline development tests, not as a ledger-lock port.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import ctypes
import json
import math
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import threading

MIN_GIT = (2, 32, 0)
DEFAULT_BUDGET_SECONDS = 30.0
CLEANUP_GRACE_SECONDS = 2.0
_ACTIVE: ContextVar["TimeBudget | None"] = ContextVar("pro_bridge_budget", default=None)
_GIT_ENV: ContextVar[dict | None] = ContextVar("pro_bridge_git_environment", default=None)
_GIT_CONFIG: ContextVar[tuple | None] = ContextVar("pro_bridge_git_config", default=None)


class TimeBudget:
    def __init__(self, seconds: float = DEFAULT_BUDGET_SECONDS) -> None:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("timeout must be a positive finite number")
        self.seconds = float(seconds)
        self.started = time.monotonic()
        self.ends = self.started + self.seconds

    def remaining(self, maximum: float | None = None, *, allow_zero: bool = False) -> float:
        left = max(0.0, self.ends - time.monotonic())
        if maximum is not None:
            left = min(left, max(0.0, maximum))
        if left <= 0 and not allow_zero:
            raise subprocess.TimeoutExpired("verification total budget", self.seconds)
        return left

    def cap_to_deadline(self, deadline_at: float) -> None:
        # Convert a wall-clock deadline ONCE to a monotonic bound. Never renew it.
        left = max(0.0, float(deadline_at) - time.time())
        self.ends = min(self.ends, time.monotonic() + left)


def current_budget() -> TimeBudget | None:
    return _ACTIVE.get()


@contextmanager
def operation_budget(seconds: float = DEFAULT_BUDGET_SECONDS):
    existing = current_budget()
    if existing is not None:
        yield existing
        return
    budget = TimeBudget(seconds)
    token = _ACTIVE.set(budget)
    try:
        yield budget
    finally:
        _ACTIVE.reset(token)


def bounded(function):
    """Give standalone Git readers one budget; nested readers share their caller's."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with operation_budget():
            return function(*args, **kwargs)
    return wrapped


_WORKER = """import json, subprocess, sys
command = json.loads(sys.stdin.buffer.readline().decode('utf-8'))
if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
    raise SystemExit(125)
try:
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, shell=False)
    raise SystemExit(child.wait())
except OSError:
    sys.stderr.write('process_spawn_failed\\n')
    raise SystemExit(127)
"""


class _WindowsJob:
    def __init__(self) -> None:
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.SetInformationJobObject.restype = w.BOOL
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.AssignProcessToJobObject.restype = w.BOOL
        kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        kernel.TerminateJobObject.restype = w.BOOL
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process: subprocess.Popen) -> None:
        # Worker is our unreaped child and cannot spawn anything until the handshake.
        handle = self.kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.CloseHandle(handle)

    def terminate(self) -> None:
        if self.handle and not self.kernel.TerminateJobObject(self.handle, 124):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _terminate_owned(process: subprocess.Popen, job: _WindowsJob | None) -> None:
    if job is not None:
        job.terminate()
    elif os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()


MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024


class OutputLimitExceeded(subprocess.SubprocessError):
    """Bounded stdout/stderr collection refused an oversized subprocess result."""


class _BoundedCapture:
    """Bounded in-memory pipe reads; the caller owns process-tree cleanup."""
    def __init__(self, process, limit):
        self.limit, self.total = limit, 0
        self.buffers = [bytearray(), bytearray()]
        self.lock = threading.Lock()
        self.overflow = threading.Event()
        self.failed = threading.Event()
        self.done = [threading.Event(), threading.Event()]
        self.threads = []
        for index, pipe in enumerate((process.stdout, process.stderr)):
            thread = threading.Thread(target=self._read, args=(index, pipe), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _read(self, index, pipe):
        try:
            while True:
                chunk = os.read(pipe.fileno(), min(65536, self.limit + 1))
                if not chunk:
                    break
                with self.lock:
                    if self.total + len(chunk) > self.limit:
                        self.overflow.set()
                        break
                    self.total += len(chunk)
                    self.buffers[index].extend(chunk)
        except (OSError, ValueError):
            self.failed.set()
        finally:
            self.done[index].set()

    def collect(self, process, payload, deadline, command, seconds):
        # The small Windows handshake shares the budget even under backpressure.
        feeder_done = threading.Event()
        def feed():
            try:
                if payload is not None:
                    pending = memoryview(payload)
                    while pending:
                        written = process.stdin.write(pending)
                        if not written:
                            raise OSError("handshake write failed")
                        pending = pending[written:]
                    process.stdin.close()
            except (OSError, ValueError):
                self.failed.set()
            finally:
                feeder_done.set()
        feeder = threading.Thread(target=feed, daemon=True)
        self.threads.append(feeder)
        feeder.start()
        while True:
            if self.overflow.is_set():
                raise OutputLimitExceeded("subprocess output exceeded configured byte limit")
            if self.failed.is_set():
                raise subprocess.SubprocessError("subprocess pipe read/write failed")
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(command, seconds)
            if all(event.is_set() for event in self.done) and feeder_done.is_set():
                # Reap after pipes close; don't signal a reaped POSIX PID.
                process.wait(timeout=left)
                return tuple(bytes(buffer) for buffer in self.buffers)
            self.overflow.wait(min(.02, left))

    def finish(self, process):
        end = time.monotonic() + CLEANUP_GRACE_SECONDS
        try:
            process.wait(timeout=max(.001, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
        for thread in self.threads:
            thread.join(timeout=max(0, end - time.monotonic()))
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe:
                pipe.close()


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        check: bool = True, timeout: float | None = None,
        max_output_bytes: int | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run without a shell/stdin, kill only owned children on timeout/cancellation.

    Process creation and OS teardown can add latency beyond the budget. Cleanup
    communication has a separate two-second ceiling, never a fresh work budget.
    """
    if max_output_bytes is not None and (type(max_output_bytes) is not int or max_output_bytes <= 0):
        raise ValueError("output limit must be a positive integer")
    with operation_budget(DEFAULT_BUDGET_SECONDS if timeout is None else timeout) as budget:
        budget.remaining(timeout)
        job = _WindowsJob() if os.name == "nt" else None
        process = None
        assigned = False
        capture = None
        try:
            actual = ([sys.executable, "-I", "-S", "-B", "-u", "-c", _WORKER]
                      if job is not None else command)
            process = subprocess.Popen(actual, cwd=cwd, env=env, shell=False,
                        stdin=subprocess.PIPE if job is not None else subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        start_new_session=(os.name == "posix"), bufsize=0 if max_output_bytes is not None else -1)
            if job is not None:
                job.assign(process)
                assigned = True
            payload = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n" if job else None
            if max_output_bytes is None:
                stdout, stderr = process.communicate(input=payload, timeout=budget.remaining(timeout))
            else:
                capture = _BoundedCapture(process, max_output_bytes)
                seconds = budget.remaining(timeout)
                stdout, stderr = capture.collect(process, payload, time.monotonic() + seconds, command, seconds)
            completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            # Nonzero exit is not cancellation: do not signal a reaped PID/group.
        except BaseException:
            if process is not None:
                if job is not None and not assigned:
                    # Only our handshake worker exists; the requested command never ran.
                    process.kill()
                else:
                    _terminate_owned(process, job)
                try:
                    if capture is None:
                        process.communicate(timeout=CLEANUP_GRACE_SECONDS)
                    else:
                        capture.finish(process)
                except subprocess.TimeoutExpired:
                    # Job close below is also kill-on-close. Never wait indefinitely.
                    process.kill()
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe:
                            pipe.close()
            raise
        finally:
            if job is not None:
                job.close()
        if capture is not None:
            capture.finish(process)
        if check:
            completed.check_returncode()
        return completed


_REPO_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"}


def git_environment(source: dict[str, str] | None = None, *, offline_root: Path | None = None) -> dict[str, str]:
    environment = dict(os.environ if source is None else source)
    for name in list(environment):
        upper = name.upper()
        if (upper in _REPO_ENV or upper.startswith(("GIT_TRACE", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
                or (offline_root is not None and upper.startswith("GIT_"))):
            environment.pop(name)
    environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GIT_PAGER="cat", PAGER="cat",
                       GIT_OPTIONAL_LOCKS="0", GIT_ASKPASS="", SSH_ASKPASS="", PYTHONDONTWRITEBYTECODE="1")
    if offline_root is not None:
        # No user hooks, aliases, signing, credentials, trace files or network protocols.
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
                           GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL="file",
                           GIT_TEMPLATE_DIR=str(offline_root / "empty-git-template"),
                           TEMP=str(offline_root), TMP=str(offline_root), TMPDIR=str(offline_root))
    return environment


@contextmanager
def git_context(environment: dict[str, str]):
    token = _GIT_ENV.set(dict(environment))
    try:
        yield
    finally:
        _GIT_ENV.reset(token)


def _run_git(args: list[str], *, repo: Path | None = None, check: bool = True,
        timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "--no-pager", "--no-replace-objects", "-c", "core.pager=cat", "-c", "gc.auto=0",
               "-c", "maintenance.auto=false", "-c", "credential.interactive=false",
               # Git's documented per-command null hook directory also works in
               # Git for Windows. No global/local configuration file is edited.
               "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    if repo is not None:
        command += ["-C", str(repo)]
    environment = git_environment(_GIT_ENV.get() if env is None else env)
    config = _GIT_CONFIG.get()
    if config is not None:
        # Only this private context can supply a config overlay. Caller-supplied
        # GIT_CONFIG_COUNT/KEY/VALUE remain stripped by git_environment above.
        # Values (possibly credentials) stay in memory, never argv or temp files.
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
                           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT=str(len(config)))
        for index, (key, value) in enumerate(config):
            environment["GIT_CONFIG_KEY_" + str(index)] = key
            environment["GIT_CONFIG_VALUE_" + str(index)] = value
    return run([*command, *args], check=check, timeout=timeout, env=environment,
               max_output_bytes=MAX_GIT_OUTPUT_BYTES)


def git(args: list[str], *, repo: Path | None = None, check: bool = True,
        timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    # Bridge ref probes have the literal shape: ls-remote <url> <refs...>.
    # Guard at the shared boundary so legacy CLI probes cannot bypass the new
    # target/config controls. --get-url is Git's non-network resolver itself.
    if not args or args[0] != "ls-remote" or "--get-url" in args:
        return _run_git(args, repo=repo, check=check, timeout=timeout, env=env)
    with operation_budget(DEFAULT_BUDGET_SECONDS if timeout is None else timeout):
        try:
            if len(args) < 2 or args[1].startswith("-"):
                raise RemoteTargetMismatch("remote_mismatch")
            if _GIT_CONFIG.get() is not None:
                require_remote_target(args[1], repo=repo)
                return _run_git(args, repo=repo, check=check, timeout=timeout, env=env)
            # Do not load an unrelated working project's local config for probes.
            with tempfile.TemporaryDirectory(prefix="pro-bridge-ref-probe-") as directory:
                root = Path(directory)
                with git_context(git_environment(_GIT_ENV.get() if env is None else env)), frozen_git_config(repo=root):
                    require_remote_target(args[1], repo=root)
                    return _run_git(args, repo=root, check=check, timeout=timeout)
        except ValueError:
            refused = subprocess.CompletedProcess(["git", *args], 3, b"", b"remote_target_or_configuration_refused\n")
            if check:
                refused.check_returncode()
            return refused


class RemoteTargetMismatch(ValueError):
    """Configured transport differs from the authorized literal URL; no fetch."""


@bounded
def require_remote_target(remote: str, *, repo: Path | None = None,
                          name: str | None = None) -> None:
    """Resolve Git's effective URL before I/O, without copying it into errors.

    Preserve normal noninteractive credential configuration, but never accept an
    insteadOf rewrite (including to a local path), a different additional URL, or an alternate
    remote helper. Call in the same repository/context immediately before EACH
    fetch/deepen or ref probe under frozen_git_config. Execution repositories
    must be newly created/controlled; this is not a hostile-local-admin sandbox.
    """
    if not isinstance(remote, str) or not remote or any(ord(c) < 32 for c in remote):
        raise RemoteTargetMismatch("remote_mismatch")
    args = (["remote", "get-url", "--all", name] if name is not None
            else ["ls-remote", "--get-url", "--", remote])
    resolved = git(args, repo=repo, check=False)
    urls = resolved.stdout.splitlines()
    if resolved.returncode or not urls or any(url != remote.encode("utf-8") for url in urls):
        raise RemoteTargetMismatch("remote_mismatch")
    # remote.<name>.vcs can select a helper independently of the URL scheme.
    helper = git(["config", "--get-all", "remote." + (name or remote) + ".vcs"],
                 repo=repo, check=False)
    if helper.returncode != 1:
        raise RemoteTargetMismatch("remote_mismatch")


@contextmanager
def frozen_git_config(*, repo: Path):
    """Capture effective config once, including resolved includes, in memory.

    Use only with a new/controlled execution directory (no untrusted local
    config). Replaying resolved entries with global/system reads disabled keeps
    validation and fetch on one config snapshot, even if a user edits .gitconfig
    meanwhile. Include directives themselves are NOT replayed. Credential
    helpers remain operator-trusted executables; this is not a Git sandbox.
    """
    if _GIT_CONFIG.get() is not None:
        yield
        return
    # Config overlays and global/system-file overrides must actually exist;
    # older Git must not silently ignore the environment-based isolation.
    version = git(["--version"], repo=repo).stdout.decode("ascii", "replace")
    match = re.match(r"git version (\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < MIN_GIT:
        raise ValueError("Git 2.32 or later is required for configuration snapshots")
    raw = git(["config", "--null", "--list", "--includes"], repo=repo).stdout
    if len(raw) > 1024 * 1024:
        raise ValueError("git configuration exceeds snapshot limit")
    items = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        key, separator, value = entry.partition(b"\n")
        name = key.decode("utf-8")
        lower = name.lower()
        if lower == "include.path" or (lower.startswith("includeif.") and lower.endswith(".path")):
            continue
        # A reader must never lazy-fetch from a second inherited promisor.
        # Only the fresh controlled origin is an authorized object source.
        if lower.startswith("remote.") and lower.endswith(".promisor") and name != "remote.origin.promisor":
            items.append((name, "false"))
        elif lower == "extensions.partialclone":
            items.append((name, "origin"))
        else:
            items.append((name, value.decode("utf-8") if separator else "true"))
    if len(items) > 4096:
        raise ValueError("git configuration exceeds snapshot limit")
    token = _GIT_CONFIG.set(tuple(items))
    try:
        yield
    finally:
        _GIT_CONFIG.reset(token)

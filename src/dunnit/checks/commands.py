"""Run declared proof commands.

Dunnit runs proof commands itself: an agent's transcript saying that tests
passed is not evidence. V2 ``argv`` commands execute without a shell, while
legacy ``run`` strings retain their documented platform-native shell behavior.
"""

from __future__ import annotations

import ctypes
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO

from dunnit.contract import CommandCheck
from dunnit.verdict import Evidence, Status

# Keep memory bounded even when a command continuously emits output. We retain
# extra bytes before producing the historical 4,000-character display tail so
# a split UTF-8 or ANSI sequence at the ring boundary can still be sanitized.
_OUTPUT_BYTES = 16_384
_DETAIL_TAIL = 4_000
_DISPLAY_LIMIT = 500
_PIPE_DRAIN_GRACE = 1.0

_FAIL_HINT = (
    "Read the failure output and fix the code it exercises — do not edit the "
    "check, skip the test, or weaken assertions."
)

# CSI, OSC, DCS/SOS/PM/APC, and simple two-byte escape sequences. Incomplete
# string controls consume the remainder rather than letting terminal payloads
# escape into CI annotations or a user's terminal.
_ANSI_ESCAPE = re.compile(
    r"\x1b(?:"
    r"\][\s\S]*?(?:\x07|\x1b\\|$)"
    r"|[P^_][\s\S]*?(?:\x1b\\|$)"
    r"|\[[0-?]*[ -/]*[@-~]"
    r"|[@-_]"
    r")"
)


class _TailBuffer:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            self._data.extend(chunk)
            overflow = len(self._data) - self._limit
            if overflow > 0:
                del self._data[:overflow]

    def bytes(self) -> bytes:
        with self._lock:
            return bytes(self._data)


def _drain_output(stream: BinaryIO, tail: _TailBuffer) -> None:
    try:
        while True:
            chunk = stream.read(8_192)
            if not chunk:
                return
            tail.append(chunk)
    except (OSError, ValueError):
        # The main thread may close the pipe after forcibly terminating an
        # inherited process tree. The command is already reported as an error.
        return


def _sanitize(value: bytes | str, *, limit: int = _DETAIL_TAIL) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value
    text = _ANSI_ESCAPE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        char
        for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"}
    )
    return text[-limit:].strip()


def _display_command(check: CommandCheck) -> str:
    if check.argv is not None:
        rendered = (
            subprocess.list2cmdline(check.argv) if os.name == "nt" else shlex.join(check.argv)
        )
    elif check.run is not None:
        rendered = check.run
    else:
        rendered = "<missing command>"
    return _sanitize(rendered, limit=_DISPLAY_LIMIT)


def _detail_with_output(prefix: str, output: str) -> str:
    """Append the output tail while bounding the complete rendered detail."""

    if not output:
        return prefix
    budget = max(0, _DETAIL_TAIL - len(prefix) - 1)
    return f"{prefix}\n{output[-budget:]}" if budget else prefix[:_DETAIL_TAIL]


def _inside_repo(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _merge_environment(
    base: Mapping[str, str], override: Mapping[str, str], *, windows: bool
) -> dict[str, str]:
    """Merge an environment using the target platform's key semantics.

    Windows environment keys are case-insensitive. Keeping both ``Path`` and
    ``PATH`` in the block makes the effective value dependent on lower-level
    process creation details, so remove any case-variant before applying the
    contract value.
    """

    merged = dict(base)
    if not windows:
        merged.update(override)
        return merged

    existing = {key.casefold(): key for key in merged}
    for key, value in override.items():
        previous = existing.get(key.casefold())
        if previous is not None and previous != key:
            merged.pop(previous, None)
        merged[key] = value
        existing[key.casefold()] = key
    return merged


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Best-effort termination of the command and every descendant process."""

    if os.name == "nt":
        # ``CREATE_NEW_PROCESS_GROUP`` alone does not make terminate() recurse.
        # taskkill is available on supported Windows installations and applies
        # /T to descendants before /F forces termination. Attempt it even when
        # the root has just exited: descendants may still own inherited pipes.
        try:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if (result is None or result.returncode != 0) and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
        return

    # start_new_session=True makes the child PID the process-group ID. Kill
    # the group even if the shell/root happened to exit during the timeout
    # race, because descendants may still hold the output pipe open.
    # Access dynamically because Windows typeshed intentionally omits this
    # POSIX-only symbol even though this branch cannot execute on Windows.
    kill_group = vars(os)["killpg"]
    try:
        kill_group(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.terminate()
        except (OSError, ValueError):
            pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass

    try:
        # A child can ignore SIGTERM after its parent exits, so check the group
        # itself before deciding that termination is complete.
        kill_group(process.pid, 0)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    else:
        try:
            kill_group(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except ProcessLookupError:
            return
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # There is nothing else the dependency-free core can portably do. The
        # ERROR verdict remains fail-closed and records incomplete execution.
        pass


class _WindowsJob:
    """A job object binding the command's process tree on Windows.

    Descendants inherit job membership when they are created, so terminating
    the job also reaps grandchildren that outlive the command root — the case
    ``taskkill /T`` cannot handle because the tree's root PID is already gone.
    """

    def __init__(self, process_handle: int) -> None:
        # typeshed only exposes WinDLL when type-checking for win32, so load
        # it dynamically (the same idiom as os.killpg above).
        api = vars(ctypes)["WinDLL"]("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        api.CreateJobObjectW.restype = pointer
        api.CreateJobObjectW.argtypes = [pointer, ctypes.c_wchar_p]
        api.AssignProcessToJobObject.argtypes = [pointer, pointer]
        api.TerminateJobObject.argtypes = [pointer, ctypes.c_uint]
        api.CloseHandle.argtypes = [pointer]
        job = api.CreateJobObjectW(None, None)
        if not job:
            raise OSError("could not create a job object for the command tree")
        if not api.AssignProcessToJobObject(job, process_handle):
            api.CloseHandle(job)
            raise OSError("could not assign the command to a job object")
        self._api = api
        self._job = job

    def terminate(self) -> None:
        self._api.TerminateJobObject(self._job, 1)

    def close(self) -> None:
        self._api.CloseHandle(self._job)


def _capture_process_tree(process: subprocess.Popen[bytes]) -> _WindowsJob | None:
    """Best-effort job capture so orphaned descendants remain terminable."""

    if os.name != "nt":
        return None
    handle = getattr(process, "_handle", None)
    if handle is None:
        return None
    try:
        return _WindowsJob(int(handle))
    except (OSError, TypeError, ValueError):
        return None


def _terminate_command_tree(process: subprocess.Popen[bytes], job: _WindowsJob | None) -> None:
    """Terminate via the captured job first, then the per-platform fallback."""

    if job is not None:
        job.terminate()
    _terminate_process_tree(process)


def _invalid_check(check: CommandCheck) -> str | None:
    if (check.run is None) == (check.argv is None):
        return "must define exactly one of 'run' or 'argv'"
    if check.run is not None and (
        type(check.run) is not str or not check.run.strip() or "\x00" in check.run
    ):
        return "has an invalid 'run' command"
    if check.argv is not None:
        if type(check.argv) is not list or not check.argv:
            return "has an invalid 'argv' command"
        if not all(type(arg) is str and arg and "\x00" not in arg for arg in check.argv):
            return "has an invalid 'argv' command"
    if type(check.timeout) is not int or check.timeout <= 0:
        return "has an invalid timeout"
    if check.dir is not None and (
        type(check.dir) is not str or not check.dir or "\x00" in check.dir
    ):
        return "has an invalid command directory"
    if type(check.env) is not dict or not all(
        type(key) is str
        and key
        and "=" not in key
        and "\x00" not in key
        and type(value) is str
        and "\x00" not in value
        for key, value in check.env.items()
    ):
        return "has invalid environment values"
    return None


def _resolved_argv(argv: list[str], env: Mapping[str, str], *, windows: bool) -> list[str]:
    """Resolve Windows PATHEXT shims while preserving shell-free argv execution."""

    command = list(argv)
    if not windows:
        return command
    path = next((value for key, value in env.items() if key.casefold() == "path"), None)
    resolved = shutil.which(command[0], path=path)
    if resolved is not None:
        command[0] = resolved
    return command


def run_command(check: CommandCheck, cwd: Path) -> Evidence:
    start = time.monotonic()
    invalid = _invalid_check(check)
    if invalid:
        return Evidence(
            check.name,
            Status.ERROR,
            f"command check {invalid}",
            hint="Fix the contract before re-running verification.",
            rule_id="command.invalid-contract",
            duration=time.monotonic() - start,
            scan_complete=False,
        )

    try:
        root = Path(cwd).resolve()
        where = (root / check.dir).resolve() if check.dir else root
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return Evidence(
            check.name,
            Status.ERROR,
            f"command directory could not be resolved: {_sanitize(str(exc))}",
            hint="Run verification from a valid repository directory.",
            rule_id="command.invalid-directory",
            duration=time.monotonic() - start,
            scan_complete=False,
        )
    if not _inside_repo(root, where):
        return Evidence(
            check.name,
            Status.ERROR,
            f"command directory escapes the repository root: {check.dir}",
            hint="Set this check's `dir` to a repository-relative directory.",
            rule_id="command.directory-escape",
            duration=time.monotonic() - start,
            scan_complete=False,
        )
    if not root.is_dir() or not where.is_dir():
        missing = check.dir or str(cwd)
        return Evidence(
            check.name,
            Status.ERROR,
            f"command directory does not exist or is not a directory: {_sanitize(missing)}",
            hint="Set this check's `dir` to an existing repository directory.",
            rule_id="command.directory-missing",
            duration=time.monotonic() - start,
            scan_complete=False,
        )

    env = _merge_environment(os.environ, check.env, windows=os.name == "nt")
    if check.argv is not None:
        command: str | list[str] = _resolved_argv(check.argv, env, windows=os.name == "nt")
    else:
        assert check.run is not None
        command = check.run
    display = _display_command(check)
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        popen_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True

    try:
        process = subprocess.Popen(
            command,
            shell=check.uses_shell,
            cwd=where,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **popen_options,
        )
    except (OSError, TypeError, ValueError) as exc:
        return Evidence(
            check.name,
            Status.ERROR,
            f"`{display}` could not run: {_sanitize(str(exc))}",
            hint="Check that the command and its `dir` exist in this repository.",
            rule_id="command.spawn-error",
            duration=time.monotonic() - start,
            scan_complete=False,
        )

    # Capture the tree immediately so termination can reap descendants that
    # outlive the root process.
    job = _capture_process_tree(process)
    tail = _TailBuffer(_OUTPUT_BYTES)
    assert process.stdout is not None  # PIPE above guarantees this
    reader = threading.Thread(target=_drain_output, args=(process.stdout, tail), daemon=True)
    reader.start()
    timed_out = False
    execution_error: str | None = None
    try:
        process.wait(timeout=check.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_command_tree(process, job)
    except (OSError, subprocess.SubprocessError) as exc:
        execution_error = _sanitize(str(exc))
        _terminate_command_tree(process, job)

    reader.join(timeout=_PIPE_DRAIN_GRACE)
    output_incomplete = reader.is_alive()
    if output_incomplete:
        if not timed_out:
            _terminate_command_tree(process, job)
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass
        reader.join(timeout=0.1)
    else:
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass
    if job is not None:
        job.close()
    duration = time.monotonic() - start
    output = _sanitize(tail.bytes())

    if execution_error is not None:
        detail = _detail_with_output(f"`{display}` execution failed: {execution_error}", output)
        return Evidence(
            check.name,
            Status.ERROR,
            detail,
            hint="Retry after fixing the local process-execution failure.",
            rule_id="command.execution-error",
            duration=duration,
            exit_code=process.returncode,
            scan_complete=False,
        )

    if timed_out:
        detail = _detail_with_output(
            f"`{display}` timed out after {check.timeout}s",
            output,
        )
        return Evidence(
            check.name,
            Status.ERROR,
            detail,
            hint="Make the command finish in time or raise this check's `timeout`.",
            rule_id="command.timeout",
            duration=duration,
            exit_code=process.returncode,
            scan_complete=False,
        )

    if output_incomplete:
        detail = _detail_with_output(
            f"`{display}` exited but its process tree kept the output stream open",
            output,
        )
        return Evidence(
            check.name,
            Status.ERROR,
            detail,
            hint="Make the proof command wait for and shut down every child process.",
            rule_id="command.output-incomplete",
            duration=duration,
            exit_code=process.returncode,
            scan_complete=False,
        )

    if process.returncode == 0:
        return Evidence(
            check.name,
            Status.PASS,
            f"`{display}` exited 0 in {duration:.1f}s",
            rule_id="command.pass",
            duration=duration,
            exit_code=0,
            scan_complete=True,
        )
    detail = _detail_with_output(f"`{display}` exited {process.returncode}", output)
    return Evidence(
        check.name,
        Status.FAIL,
        detail,
        hint=_FAIL_HINT,
        rule_id="command.exit-nonzero",
        duration=duration,
        exit_code=process.returncode,
        scan_complete=True,
    )

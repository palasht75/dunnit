"""Run declared proof commands. dunnit runs them itself — the agent's
transcript saying 'all tests pass' is not evidence."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from dunnit.contract import CommandCheck
from dunnit.verdict import Evidence, Status

_TAIL = 4000

_FAIL_HINT = (
    "Read the failure output and fix the code it exercises — do not edit the "
    "check, skip the test, or weaken assertions."
)


def run_command(check: CommandCheck, cwd: Path) -> Evidence:
    where = cwd / check.dir if check.dir else cwd
    env = {**os.environ, **check.env} if check.env else None
    start = time.monotonic()
    try:
        out = subprocess.run(
            check.run, shell=True, cwd=where, env=env, capture_output=True,
            text=True, timeout=check.timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return Evidence(
            check.name, Status.FAIL,
            f"`{check.run}` timed out after {check.timeout}s",
            hint="Make the command finish in time or raise this check's `timeout`.",
        )
    except OSError as exc:
        return Evidence(
            check.name, Status.FAIL,
            f"`{check.run}` could not run: {exc}",
            hint="Check the command and its `dir` exist in this repo.",
        )
    duration = time.monotonic() - start
    if out.returncode == 0:
        return Evidence(check.name, Status.PASS, f"`{check.run}` exited 0 in {duration:.1f}s")
    tail = (out.stdout + out.stderr)[-_TAIL:].strip()
    return Evidence(
        check.name, Status.FAIL,
        f"`{check.run}` exited {out.returncode}\n{tail}",
        hint=_FAIL_HINT,
    )

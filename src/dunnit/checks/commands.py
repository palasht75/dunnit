"""Run declared proof commands. dunnit runs them itself — the agent's
transcript saying 'all tests pass' is not evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dunnit.contract import CommandCheck
from dunnit.verdict import Evidence, Status

_TAIL = 2000


def run_command(check: CommandCheck, cwd: Path) -> Evidence:
    try:
        out = subprocess.run(
            check.run, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=check.timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return Evidence(check.name, Status.FAIL, f"timed out after {check.timeout}s")
    if out.returncode == 0:
        return Evidence(check.name, Status.PASS, f"`{check.run}` exited 0")
    tail = (out.stdout + out.stderr)[-_TAIL:].strip()
    return Evidence(check.name, Status.FAIL, f"`{check.run}` exited {out.returncode}\n{tail}")

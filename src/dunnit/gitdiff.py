"""Thin git helpers. All diff-based checks read the real repo state."""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileDiff:
    path: str
    status: str  # A, M, D, R...
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)


def _git(args: list[str], cwd: Path) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60, check=False
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def collect_diff(cwd: Path, base: str | None) -> list[FileDiff]:
    """Diff of working tree + index against ``base`` (or HEAD if None)."""
    ref = base or "HEAD"
    name_status = _git(["diff", "--name-status", "-M", ref], cwd)
    diffs: dict = {}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0][:1], parts[-1]
        diffs[path] = FileDiff(path=path, status=status)

    current: FileDiff | None = None
    for line in _git(["diff", "--unified=0", "-M", ref], cwd).splitlines():
        if line.startswith("+++ b/"):
            current = diffs.get(line[6:])
        elif line.startswith("--- a/") and line[6:] in diffs and diffs[line[6:]].status == "D":
            current = diffs[line[6:]]
        elif current is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current.added_lines.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                current.removed_lines.append(line[1:])
    return list(diffs.values())


def matches_any(path: str, globs: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    for g in globs:
        if fnmatch.fnmatch(path, g):
            return True
        if "/" not in g and fnmatch.fnmatch(name, g):
            return True
        if g.startswith("**/") and fnmatch.fnmatch(name, g[3:]):
            return True
    return False

"""Git diff collection and gitignore-style glob matching.

All diff-based checks read the real repo state — never the agent's
transcript. ``collect_diff`` covers tracked changes (worktree + index)
*and* untracked files, so brand-new files can't dodge inspection.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Untracked files above this size are skipped: line-based checks are useless
# on assets, and reading huge files would slow verification down.
_MAX_UNTRACKED_BYTES = 1_000_000
_MAX_UNTRACKED_FILES = 1_000


@dataclass
class FileDiff:
    path: str
    status: str  # A(dded), M(odified), D(eleted), R(enamed)
    old_path: str | None = None  # renames: where the file used to live
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
    """Diff of working tree + index + untracked files against ``base`` (HEAD if None)."""
    ref = base or "HEAD"
    diffs: dict[str, FileDiff] = {}
    by_old_path: dict[str, FileDiff] = {}

    for line in _git(["diff", "--name-status", "-M", ref], cwd).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][:1]
        if status == "R" and len(parts) >= 3:
            fd = FileDiff(path=parts[2], status="R", old_path=parts[1])
            by_old_path[parts[1]] = fd
        else:
            fd = FileDiff(path=parts[-1], status=status)
            if status == "D":
                by_old_path[fd.path] = fd
        diffs[fd.path] = fd

    current: FileDiff | None = None
    for line in _git(["diff", "--unified=0", "-M", ref], cwd).splitlines():
        if line.startswith("+++ b/"):
            current = diffs.get(line[6:])
        elif line.startswith("--- a/") and line[6:] in by_old_path:
            current = by_old_path[line[6:]]
        elif current is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current.added_lines.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                current.removed_lines.append(line[1:])

    untracked = _git(["ls-files", "--others", "--exclude-standard"], cwd).splitlines()
    for path in untracked[:_MAX_UNTRACKED_FILES]:
        if path in diffs:
            continue
        file = cwd / path
        try:
            if file.stat().st_size > _MAX_UNTRACKED_BYTES:
                continue
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue  # unreadable or binary: nothing line-based to inspect
        diffs[path] = FileDiff(path=path, status="A", added_lines=text.splitlines())

    return list(diffs.values())


@lru_cache(maxsize=1024)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into a regex over posix paths.

    ``**`` crosses directories, ``*``/``?`` do not. A pattern with no slash
    (``*.cfg``, ``dod.yaml``) matches at any depth; a leading ``/`` anchors
    it to the repo root; a trailing ``/`` means "everything under this
    directory".
    """
    pat = pattern.replace("\\", "/")
    rooted = pat.startswith("/")
    pat = pat.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    if "/" not in pat and not rooted:
        pat = "**/" + pat
    out: list[str] = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(path: str, globs: list[str]) -> bool:
    path = path.replace("\\", "/")
    return any(_glob_regex(g).match(path) for g in globs)

"""Protected paths: files the agent must not touch at all.

By default the contract file itself (dod.yaml) is protected — otherwise the
agent can simply weaken its own definition of done. Teams typically add
".github/**" and freeze "tests/**" for agent-authored changes.
"""

from __future__ import annotations

from dunnit.gitdiff import FileDiff, matches_any
from dunnit.verdict import Evidence, Status


def check_protected(diffs: list[FileDiff], protected_globs: list[str]) -> list[Evidence]:
    touched = [d.path for d in diffs if matches_any(d.path, protected_globs)]
    if touched:
        return [
            Evidence(
                "tamper:protected-path",
                Status.FAIL,
                f"protected files modified: {', '.join(sorted(touched))}",
            )
        ]
    return [Evidence("protected", Status.PASS, "no protected files touched")]

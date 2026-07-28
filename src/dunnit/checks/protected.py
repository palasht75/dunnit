"""Protected paths: files the agent must not touch at all.

By default the contract file itself (dod.yaml) is protected — otherwise the
agent can simply weaken its own definition of done. Teams typically add
".github/**" and freeze "tests/**" for agent-authored changes. Renames count
as touching both the old and new location.
"""

from __future__ import annotations

from dunnit.gitdiff import FileDiff, matches_any
from dunnit.verdict import Evidence, Status


def check_protected(
    diffs: list[FileDiff],
    protected_globs: list[str],
    contract_path: str | None = None,
) -> list[Evidence]:
    touched = []
    for d in diffs:
        # Creating the contract for the first time (dunnit init) is onboarding,
        # not tampering — but modifying or moving an existing one always is.
        if contract_path and d.path == contract_path and d.status == "A":
            continue
        paths = [d.path] + ([d.old_path] if d.old_path else [])
        if any(matches_any(p, protected_globs) for p in paths):
            touched.append(f"{d.old_path} -> {d.path}" if d.old_path else d.path)
    if touched:
        return [
            Evidence(
                "tamper:protected-path", Status.FAIL,
                f"protected files modified: {', '.join(sorted(touched))}",
                hint="Revert these changes — protected files are outside the task's "
                     "blast radius by contract.",
            )
        ]
    return [Evidence("protected", Status.PASS, "no protected files touched")]
